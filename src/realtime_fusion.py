"""
准实时融合服务 — 文件交换区驱动，定时扫描融合。

融合公式: score = ly×0.30 + mindlynx×0.40 + at×0.30

用法:
    python src/realtime_fusion.py                      # 执行一次
    python src/realtime_fusion.py --daemon              # 守护模式
    python src/realtime_fusion.py --daemon -i 300       # 每300秒
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# 确保项目根目录在路径中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.wecom_notifier import WeComNotifier
from src.normalizer import SignalNormalizer, L7_SIGNAL_NAMES, MAX_POSITION_DISAGREEMENT, L7_POSITION, L7_EMOJI

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

    WEIGHTS = {"lynx": 0.30, "mindlynx": 0.40, "tradingagent": 0.30}

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
                    "mindlynx": w.get("mindlynx", 0.40),
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
                with open("config/settings.yaml", encoding="utf-8") as _f:
                    cfg = yaml.safe_load(_f)
                wc = cfg.get("wecom", {})
                # 优先读取环境变量（项目根 .env），兼容旧版 yaml 配置
                webhook_url = os.getenv("WECOM_WEBHOOK_URL") or wc.get("webhook_url", "")
                self._notifier = WeComNotifier(
                    webhook_url,
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

        # 判断各系统是否有效：有数据即可（不检查时间新鲜度，各系统有自身的更新周期）
        ly_available = bool(ly_stocks)
        ml_available = bool(ml_stocks)
        at_available = bool(at_stocks)
        valid_count = sum([ly_available, ml_available, at_available])
        is_degraded = valid_count < 3

        # 权重重分配：子系统文件缺失时，权重重新归一化到有效系统
        # 例如 ML 文件缺失时 ly:at 权重从 0.30:0.30 变为 0.50:0.50
        _avail = {"lynx": ly_available, "mindlynx": ml_available, "tradingagent": at_available}
        _total = sum(self.WEIGHTS[k] for k, v in _avail.items() if v)
        if _total > 0:
            adj_weights = {k: (self.WEIGHTS[k] / _total if v else 0.0) for k, v in _avail.items()}
        else:
            adj_weights = self.WEIGHTS  # 所有系统无数据，此分支实际不会进入（all_codes为空）

        # 所有出现过的股票代码
        all_codes = set(ly_stocks) | set(ml_stocks) | set(at_stocks)

        # 获取实时行情价格（替代lynx信号中的昨收盘价）
        realtime_prices = self._fetch_realtime_prices(all_codes)

        changes = []
        for code in sorted(all_codes):
            ly_score = (ly_stocks.get(code) or {}).get("score", 0) if ly_available else 0
            ml_score = (ml_stocks.get(code) or {}).get("l7_score", 0) if ml_available else 0
            at_score = (at_stocks.get(code) or {}).get("score", 0) if at_available else 0

            # 防御: 任一score为NaN则清零（防止NaN传播导致全线崩溃）
            if math.isnan(ly_score): ly_score = 0.0
            if math.isnan(ml_score): ml_score = 0.0
            if math.isnan(at_score): at_score = 0.0

            score = (
                ly_score * adj_weights["lynx"]
                + ml_score * adj_weights["mindlynx"]
                + at_score * adj_weights["tradingagent"]
            )
            score = round(max(-3.0, min(3.0, score)), 3)

            # 分歧检测 + 不确定性惩罚
            has_disagreement, disagreement_score = self._detect_disagreement(
                ly_score, ml_score, at_score
            )
            penalty = self._uncertainty_penalty(disagreement_score)
            score_penalized = round(max(-3.0, score - penalty), 3)

            # 仓位建议
            label = SignalNormalizer.map_normalized_to_label(score_penalized)
            signal_name = L7_SIGNAL_NAMES.get(label, "中性/持有")
            position = L7_POSITION.get(label, "0成")
            if has_disagreement:
                position = SignalNormalizer.cap_position_for_disagreement(position)

            if self._should_push(code, score_penalized):
                changes.append({
                    "code": code,
                    "score": score_penalized,
                    "signal": label,
                    "signal_name": signal_name,
                    "position": position,
                    "ly": ly_score,
                    "ml": ml_score,
                    "at": at_score,
                    "has_disagreement": has_disagreement,
                    "is_degraded": is_degraded,
                    "price": realtime_prices.get(code, {}).get("price", 0) or ly_stocks.get(code, {}).get("price", 0),
                    "pct_chg": realtime_prices.get(code, {}).get("pct_chg", 0) or ly_stocks.get(code, {}).get("pct_chg", 0),
                    "volume_ratio": realtime_prices.get(code, {}).get("volume_ratio", 0) or ly_stocks.get(code, {}).get("volume_ratio", 0),
                })
                self._last_scores[code] = score_penalized

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

    def _detect_disagreement(self, ly_score, ml_score, at_score):
        """检测三个系统间的方向分歧"""
        scores = [s for s in [ly_score, ml_score, at_score] if s is not None]
        if len(scores) < 2:
            return False, 0.0
        has_bullish = any(s > 0.5 for s in scores)
        has_bearish = any(s < -0.5 for s in scores)
        disagreement = has_bullish and has_bearish
        disagreement_score = 0.0
        if disagreement and len(scores) > 1:
            mean = sum(scores) / len(scores)
            variance = sum((s - mean) ** 2 for s in scores) / len(scores)
            disagreement_score = math.sqrt(variance)
        return disagreement, disagreement_score

    @staticmethod
    def _uncertainty_penalty(disagreement_score: float) -> float:
        """分歧分数 → 不确定性惩罚"""
        if disagreement_score <= 0.5:
            return 0.0
        return min(2.0, max(0.0, (disagreement_score - 0.5) * 1.2))

    @staticmethod
    def _to_label(score: float) -> str:
        """L7 得分 → 中文标签（委托 normalizer）"""
        label = SignalNormalizer.map_normalized_to_label(score)
        return L7_SIGNAL_NAMES.get(label, "中性/持有")

    def push_changes(self, changes: List[Dict[str, Any]]):
        """推送信号变化到企业微信（按融合得分降序排列，看多→看空）"""
        if not changes:
            return
        now = datetime.now().strftime("%H:%M")
        lines = [f"🛟lma {now} 融合速报", ""]
        for c in sorted(changes, key=lambda x: x['score'], reverse=True):
            name = self._stock_names.get(c['code'], c['code'])
            signal_display = c.get('signal_name', c['signal'])
            score = c['score']
            position = c.get('position', '')
            emoji = L7_EMOJI.get(c['signal'], "⚪")
            price = c.get('price', 0)
            pct = c.get('pct_chg', 0)
            vr = c.get('volume_ratio', 0)
            price_str = f"¥{price:.2f}" if price else ""
            chg_str = f"{pct:+.2f}%" if pct else ""
            vr_str = f"量比{vr:.2f}" if vr else ""
            extras = ' '.join(x for x in [price_str, chg_str, vr_str] if x)
            line = f"{emoji} **{name}**｜{extras} {signal_display} Δ{score:+.2f}"
            lines.append(line)
        self.notifier.send_markdown("\n".join(lines))

    def run_once(self) -> List[Dict[str, Any]]:
        """执行一次扫描+融合+推送"""
        changes = self.scan_and_fuse()
        if changes:
            self.push_changes(changes)
        return changes

    @staticmethod
    def _is_trading_day(d: datetime | None = None) -> bool:
        """检查是否为交易日（跳过周六日）。"""
        if d is None:
            d = datetime.now()
        return d.isoweekday() <= 5  # 1=Mon ... 5=Fri

    @staticmethod
    def _is_trading_hour(d: datetime | None = None) -> bool:
        """检查当前是否在A股交易时段（09:30-11:30 / 13:00-15:00）。"""
        if d is None:
            d = datetime.now()
        h, m = d.hour, d.minute
        # 上午盘 09:30-11:30
        if h == 9 and m >= 30:
            return True
        if 10 <= h <= 10:
            return True
        if h == 11 and m <= 30:
            return True
        # 下午盘 13:00-15:00
        if 13 <= h <= 14:
            return True
        if h == 15 and m == 0:
            return True
        return False

    @staticmethod
    def _fetch_realtime_prices(codes: set[str]) -> dict[str, dict[str, float]]:
        """从新浪行情API获取实时价格、涨跌幅、量比。

        Returns:
            {code: {"price": float, "pct_chg": float, "volume_ratio": float}}
        """
        prices: dict[str, dict[str, float]] = {}
        for code in sorted(codes):
            prefix = "sh" if code.startswith(("6", "5", "9")) else "sz"
            url = f"https://hq.sinajs.cn/list={prefix}{code}"
            try:
                resp = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=5)
                if resp.status_code == 200:
                    parts = resp.text.split(",")
                    # 格式: 股票名,今开,昨收,当前价,最高,最低,买一价,卖一价,成交量(手),成交额(元)
                    # parts[2]=昨收, parts[3]=当前价
                    if len(parts) >= 30:
                        prev_close = float(parts[2]) if parts[2] else 0.0
                        current = float(parts[3]) if parts[3] else 0.0
                        pct = round((current - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0.0
                        prices[code] = {
                            "price": current,
                            "pct_chg": pct,
                            "volume_ratio": 0.0,  # Sina不直接提供量比，用ly/DB值兜底
                        }
            except Exception:
                pass
        return prices

    @staticmethod
    def _seconds_until_next_trading_day(resume_hour: int = 9, resume_min: int = 33) -> float:
        """计算到下一个交易日 resume_hour:resume_min 的秒数。"""
        now = datetime.now()
        current_weekday = now.isoweekday()
        # 周六 → +2 天到周一; 周日 → +1 天到周一
        days_ahead = {6: 2, 7: 1}.get(current_weekday, 0)
        if days_ahead > 0 or (current_weekday == 5 and now.hour >= resume_hour):
            # 周五收盘后 → +3 天到下周一
            if current_weekday == 5:
                days_ahead = 3
            next_day = (now + timedelta(days=days_ahead)).replace(
                hour=resume_hour, minute=resume_min, second=0, microsecond=0,
            )
            return (next_day - now).total_seconds()
        return 0.0

    def run_daemon(self, interval: int = 300):
        """守护模式 — 仅在交易日交易时段运行，周末和盘后自动跳过。"""
        print(f"[realtime-fusion] daemon started, interval={interval}s", flush=True)
        while True:
            # 非交易日跳过
            if not self._is_trading_day():
                wait = self._seconds_until_next_trading_day()
                next_label = (datetime.now() + timedelta(seconds=wait)).strftime("%a %m-%d %H:%M")
                print(f"[realtime-fusion] 非交易日，休眠 {wait/3600:.1f}h 到 {next_label}", flush=True)
                time.sleep(wait)
                continue

            # 非交易时段跳过
            if not self._is_trading_hour():
                # 计算到下一个交易时段（下午13:00或次日09:30）的秒数
                now = datetime.now()
                if now.hour < 9 or (now.hour == 9 and now.minute < 30):
                    # 早于09:30 → 睡到09:33
                    next_open = now.replace(hour=9, minute=33, second=0, microsecond=0)
                elif now.hour < 13:
                    # 午休 11:30-13:00 → 睡到13:03
                    next_open = now.replace(hour=13, minute=3, second=0, microsecond=0)
                else:
                    # 收盘后 → 睡到次日09:33
                    tomorrow = now + timedelta(days=1)
                    next_open = tomorrow.replace(hour=9, minute=33, second=0, microsecond=0)
                    # 如果明天是周末，用交易日的09:33
                    if next_open.isoweekday() >= 6:
                        days_ahead = {6: 2, 7: 1}[next_open.isoweekday()]
                        next_open = (next_open + timedelta(days=days_ahead)).replace(
                            hour=9, minute=33, second=0, microsecond=0,
                        )
                wait = (next_open - now).total_seconds()
                next_label = next_open.strftime("%a %m-%d %H:%M")
                print(f"[realtime-fusion] 非交易时段，休眠 {wait/3600:.1f}h 到 {next_label}", flush=True)
                time.sleep(wait)
                continue

            try:
                changes = self.run_once()
                if changes:
                    print(f"[realtime-fusion] {datetime.now().isoformat()} "
                          f"{len(changes)} changes pushed", flush=True)
            except Exception as e:
                print(f"[realtime-fusion] error: {e}", flush=True)
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
