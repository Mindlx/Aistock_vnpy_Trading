"""
ml 因子层实时服务 — 直接从 stock_daily DB 读取 OHLCV 数据，
调用 FactorEngine 计算 12 因子 composite_score。
完全绕过 LLM 层，纯数学计算。

用法:
    python services/ml_factor_service.py                     # 执行一次
    python services/ml_factor_service.py --daemon            # 守护模式
    python services/ml_factor_service.py --daemon -i 300     # 每300秒
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# ml 子系统路径
ML_ROOT = Path(__file__).resolve().parent.parent / "systems" / "MindLynx-Aistock"
DB_PATH = ML_ROOT / "data" / "stock_analysis.db"
FACTOR_SRC = str(ML_ROOT / "src")
OUTPUT_PATH = Path("data/realtime/ml_signal.json")

# 确保 data/realtime/ 存在
OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

# 所有代码以融合系统的 .venv 运行，但需要将 ml 源码加入路径
sys.path.insert(0, FACTOR_SRC)


class MLFactorService:
    """ml 因子层服务 — 纯数学计算，无 LLM"""

    STOCK_CODES = [
        "001390", "300652", "600372", "605368",
        "000592", "603189", "603557", "688202", "601801", "300676",
    ]

    def __init__(self):
        self._engine = None
        self._db_conn = None
        self._db_cols: Optional[list] = None

    @property
    def engine(self):
        if self._engine is None:
            from src.core.factor_engine import FactorEngine
            self._engine = FactorEngine()
        return self._engine

    @property
    def db(self):
        if self._db_conn is None:
            import sqlite3
            self._db_conn = sqlite3.connect(str(DB_PATH))
        return self._db_conn

    @property
    def db_cols(self) -> list:
        if self._db_cols is None:
            meta = self.db.execute("PRAGMA table_info(stock_daily)").fetchall()
            self._db_cols = [c[1] for c in meta]
        return self._db_cols

    def compute_all(self) -> Dict[str, Any]:
        """计算所有股票的最新因子得分"""
        results = {}
        for code in self.STOCK_CODES:
            try:
                score = self._compute_one(code)
                if score is not None:
                    results[code] = score
            except Exception as e:
                # 单只股票失败不影响其他
                continue
        return {
            "stocks": results,
            "updated_at": datetime.now().isoformat(),
            "source": "ml_factor_service",
        }

    def _compute_one(self, code: str) -> Optional[Dict[str, Any]]:
        """计算单只股票的因子 composite_score"""
        rows = self.db.execute(
            f"SELECT * FROM stock_daily WHERE code=? ORDER BY date", (code,)
        ).fetchall()
        if not rows:
            return None
        daily = [dict(zip(self.db_cols, r)) for r in rows]
        result = self.engine.compute_for_stock(code, daily)
        return {
            "composite_score": float(result.composite_score),
            "composite_label": result.composite_label,
            "factors": {k: float(v) for k, v in result.raw_factors.items()},
        }

    def run_once(self) -> Dict[str, Any]:
        """执行一次计算并原子写入文件"""
        data = self.compute_all()
        tmp = str(OUTPUT_PATH) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.rename(tmp, str(OUTPUT_PATH))
        return data

    def run_daemon(self, interval: int = 300):
        """守护模式"""
        print(f"[ml-factor] daemon started, interval={interval}s")
        while True:
            try:
                data = self.run_once()
                count = len(data.get("stocks", {}))
                print(f"[ml-factor] {datetime.now().isoformat()} {count} stocks computed")
            except Exception as e:
                print(f"[ml-factor] error: {e}")
            time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="ml 因子层实时服务")
    parser.add_argument("--daemon", action="store_true", help="守护模式")
    parser.add_argument("-i", "--interval", type=int, default=300, help="轮询间隔（秒）")
    args = parser.parse_args()

    service = MLFactorService()
    if args.daemon:
        service.run_daemon(interval=args.interval)
    else:
        data = service.run_once()
        print(json.dumps(data, ensure_ascii=False, indent=2)[:500])


if __name__ == "__main__":
    main()
