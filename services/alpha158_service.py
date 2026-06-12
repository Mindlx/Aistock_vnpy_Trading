"""
Alpha158 因子层实时服务 — 使用58个Alpha158因子+LGB模型，
纯数学计算（无LLM），每N秒写入 alpha158_signal.json 到文件交换区。

用法:
    python services/alpha158_service.py                     # 执行一次
    python services/alpha158_service.py --daemon            # 守护模式
    python services/alpha158_service.py --daemon -i 300     # 每300秒
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# 项目根路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "systems/lynx_vnpy"))

import lightgbm as lgb
import numpy as np
import pandas as pd

from vnpy_bridge.alpha_predictor import _compute_alpha_factors, _normalize

DB_PATH = PROJECT_ROOT / "systems" / "MindLynx-Aistock" / "data" / "stock_analysis.db"
MODEL_PATH = PROJECT_ROOT / "systems" / "lynx_vnpy" / "models" / "alpha_lgb_model.txt"
OUTPUT_PATH = Path("data/realtime/alpha158_signal.json")

OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

STOCK_CODES = [
    "001390", "300652", "600372", "605368",
    "000592", "603189", "603557", "688202", "601801", "300676",
]


class Alpha158Service:
    """Alpha158 因子层服务 — 纯数学计算（58因子+LGB）"""

    def __init__(self):
        self._model = None
        self._last_hash: Optional[int] = None

    @property
    def model(self):
        if self._model is None:
            self._model = lgb.Booster(model_file=str(MODEL_PATH))
        return self._model

    def compute_all(self) -> Dict[str, Any]:
        """计算所有股票的Alpha158因子+LGB预测"""
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        results = {}

        for code in STOCK_CODES:
            try:
                rows = conn.execute(
                    "SELECT date, open, high, low, close, volume "
                    "FROM stock_daily WHERE code=? ORDER BY date", (code,)
                ).fetchall()
                if not rows or len(rows) < 60:
                    continue

                df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
                factors = _compute_alpha_factors(df)
                factors = _normalize(factors)

                last = factors.iloc[-1:].dropna(axis=1)
                if last.empty or last.shape[1] == 0:
                    continue

                prob_up = float(self.model.predict(last.values)[0])
                prob_up = np.clip(prob_up, 0.0, 1.0)

                # L7映射：prob_up → [-3, +3]
                import math
                p = max(0.001, min(0.999, prob_up))
                logit = math.log(p / (1 - p))
                l7 = round(3.0 * math.tanh(logit / 2.0), 3)

                results[code] = {
                    "prob_up": round(prob_up, 4),
                    "l7_score": l7,
                }
            except Exception as e:
                continue

        conn.close()
        return {
            "stocks": results,
            "updated_at": datetime.now().isoformat(),
            "source": "alpha158_service",
        }

    def run_once(self) -> Optional[Dict[str, Any]]:
        """执行一次计算，无变化跳过写入"""
        data = self.compute_all()

        scores = {
            code: info.get("l7_score", 0)
            for code, info in data.get("stocks", {}).items()
        }
        current_hash = hash(tuple(sorted(scores.items())))
        if self._last_hash == current_hash:
            return None

        self._last_hash = current_hash
        tmp = str(OUTPUT_PATH) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.rename(tmp, str(OUTPUT_PATH))
        return data

    def run_daemon(self, interval: int = 300):
        """守护模式"""
        print(f"[alpha158] daemon started, interval={interval}s")
        while True:
            try:
                data = self.run_once()
                if data is not None:
                    count = len(data.get("stocks", {}))
                    print(f"[alpha158] {datetime.now().isoformat()} {count} stocks computed")
                else:
                    print(f"[alpha158] {datetime.now().isoformat()} no change, skipped")
            except Exception as e:
                print(f"[alpha158] error: {e}")
            time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Alpha158 因子层实时服务")
    parser.add_argument("--daemon", action="store_true", help="守护模式")
    parser.add_argument("-i", "--interval", type=int, default=300, help="轮询间隔（秒）")
    args = parser.parse_args()

    service = Alpha158Service()
    if args.daemon:
        service.run_daemon(interval=args.interval)
    else:
        data = service.run_once()
        print(json.dumps(data, ensure_ascii=False, indent=2)[:500])


if __name__ == "__main__":
    main()
