"""
准实时融合服务 — 文件交换区驱动，定时扫描融合。

融合公式: score = ly×0.30 + ml_factor×0.40 + at×0.30

用法:
    python src/realtime_fusion.py                      # 执行一次
    python src/realtime_fusion.py --daemon              # 守护模式
    python src/realtime_fusion.py --daemon -i 300       # 每300秒
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 确保项目根目录在路径中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.wecom_notifier import WeComNotifier
from src.normalizer import SignalNormalizer, L7_SIGNAL_NAMES

REALTIME_DIR = Path("data/realtime")

# 推送变化阈值（按信号区域细分）
# L7 范围 [-3, +3]，分三个区域
THRESHOLD_NEUTRAL = 0.3      # |score| < 0.5: 中性区
THRESHOLD_BORDER = 0.2       # 0.5 ≤ |score| < 1.0: 临界区
THRESHOLD_DIRECTIONAL = 0.5  # |score| ≥ 1.0: 方向区


def _load_json(path: Path) -> dict:
    """安全读取 JSON 文件，文件不存在则返回空 dict"""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {}


class RealtimeFusion:
    """准实时融合服务 — 读文件交换区 → 融合 → 推送"""

    WEIGHTS = {"lynx": 0.30, "ml_factor": 0.40, "tradingagent": 0.30}

    def __init__(self):
        REALTIME_DIR.mkdir(parents=True, exist_ok=True)
        self._last_scores: Dict[str, float] = {}
        self._notifier: Optional[WeComNotifier] = None
        self._stock_names: Dict[str, str] = self._load_stock_names()
        self._load_weights()

    def _load_weights(self):
        """从 settings.yaml 读取权重，兼容默认值"""
        try:
            import yaml
            cfg_path = Path("config/settings.yaml")
            if cfg_path.exists():
                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
                w = cfg.get("weights", {})
                self.WEIGHTS = {
                    "lynx": w.get("lynx_vnpy", 0.30),
                    "ml_factor": w.get("mindlynx", 0.40),
                    "tradingagent": w.get("tradingagent", 0.30),
                }
        except Exception:
            pass  # 保持默认权重

    @staticmethod
    def _load_stock_names() -> Dict[str, str]:
        """从 stock_pool.csv 加载股票代码→名称映射"""
        import csv
        names = {}
        path = Path("config/stock_pool.csv")
        if path.exists():
            with open(path, encoding="utf-8") as f:
                for row in csv.DictReader(f, skipinitialspace=True):
                    code = (row.get("code") or "").strip()
                    name = (row.get("name") or "").strip()
                    if code:
                        names[code] = name
        return names

    @property
    def notifier(self):
        if self._notifier is None:
            try:
                import yaml
                cfg = yaml.safe_load(open("config/settings.yaml"))
                wc = cfg.get("wecom", {})
                self._notifier = WeComNotifier(
                    wc.get("webhook_url", ""),
                    enabled=wc.get("enabled", False),
                )
            except Exception:
                self._notifier = WeComNotifier("", enabled=False)
        return self._notifier

    def scan_and_fuse(self) -> List[Dict[str, Any]]:
        """扫描文件交换区，融合所有股票，返回有变化的条目"""
        ly = _load_json(REALTIME_DIR / "ly_signal.json")
        ml = _load_json(REALTIME_DIR / "ml_signal.json")
        at = _load_json(REALTIME_DIR / "at_signal.json")

        ly_stocks = ly.get("stocks", {})
        ml_stocks = ml.get("stocks", {})
        at_stocks = at.get("stocks", {})

        # 所有出现过的股票代码
        all_codes = set(ly_stocks) | set(ml_stocks) | set(at_stocks)

        changes = []
        for code in sorted(all_codes):
            ly_score = ly_stocks.get(code, {}).get("score", 0)
            ml_score = ml_stocks.get(code, {}).get("composite_score", 0)
            at_score = at_stocks.get(code, {}).get("score", 0)

            score = (
                ly_score * self.WEIGHTS["lynx"]
                + ml_score * self.WEIGHTS["ml_factor"]
                + at_score * self.WEIGHTS["tradingagent"]
            )
            score = round(max(-3.0, min(3.0, score)), 3)

            if self._should_push(code, score):
                signal = self._to_label(score)
                changes.append({
                    "code": code,
                    "score": score,
                    "signal": signal,
                    "ly": ly_score,
                    "ml": ml_score,
                    "at": at_score,
                })
                self._last_scores[code] = score

        return changes

    def _should_push(self, code: str, score: float) -> bool:
        """判断是否需要推送信号变化"""
        last = self._last_scores.get(code)
        if last is None:
            return True  # 首次出现
        delta = abs(score - last)
        abs_score = abs(score)
        if abs_score < 0.5:
            return delta > THRESHOLD_NEUTRAL
        elif abs_score < 1.0:
            return delta > THRESHOLD_BORDER
        else:
            return delta > THRESHOLD_DIRECTIONAL

    @staticmethod
    def _to_label(score: float) -> str:
        """L7 得分 → 中文标签（委托 normalizer）"""
        label = SignalNormalizer.map_normalized_to_label(score)
        return L7_SIGNAL_NAMES.get(label, "中性/持有")

    def push_changes(self, changes: List[Dict[str, Any]]):
        """推送信号变化到企业微信"""
        if not changes:
            return
        now = datetime.now().strftime("%H:%M")
        lines = [f"📊 盘中融合速报 ({now})", ""]
        for c in changes:
            name = self._stock_names.get(c['code'], c['code'])
            signal = c['signal']
            score = c['score']
            emoji = "🟢" if score > 0 else "🔴" if score < 0 else "⚪"
            lines.append(f"{emoji}**{name}** {signal}Δ{score:+.2f}")
        lines.append("")
        lines.append("📡 ly昨日 | ml实时 | at盘中")
        self.notifier.send_markdown("\n".join(lines))

    def run_once(self) -> List[Dict[str, Any]]:
        """执行一次扫描+融合+推送"""
        changes = self.scan_and_fuse()
        if changes:
            self.push_changes(changes)
        return changes

    def run_daemon(self, interval: int = 300):
        """守护模式"""
        print(f"[realtime-fusion] daemon started, interval={interval}s")
        while True:
            try:
                changes = self.run_once()
                if changes:
                    print(f"[realtime-fusion] {datetime.now().isoformat()} "
                          f"{len(changes)} changes pushed")
            except Exception as e:
                print(f"[realtime-fusion] error: {e}")
            time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="准实时融合服务")
    parser.add_argument("--daemon", action="store_true", help="守护模式")
    parser.add_argument("-i", "--interval", type=int, default=300, help="扫描间隔（秒）")
    args = parser.parse_args()

    service = RealtimeFusion()
    if args.daemon:
        service.run_daemon(interval=args.interval)
    else:
        changes = service.run_once()
        if changes:
            for c in changes:
                name = service._stock_names.get(c['code'], c['code'])
                print(f"{name}: {c['signal']} (score={c['score']:+.2f})")
        else:
            print("无变化")


if __name__ == "__main__":
    main()
