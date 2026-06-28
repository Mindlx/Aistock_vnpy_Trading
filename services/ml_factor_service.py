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
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from typing import Any, Dict, Optional

# ml 子系统路径
ML_ROOT = Path(__file__).resolve().parent.parent / "systems" / "MindLynx-Aistock"
DB_PATH = ML_ROOT / "data" / "stock_analysis.db"
OUTPUT_PATH = Path("data/realtime/ml_signal.json")

# 确保 data/realtime/ 存在
OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

# 所有代码以融合系统的 .venv 运行，但需要将 ml 源码加入路径
# 注意：import 是 from src.core.xxx，所以根路径是 ML_ROOT 本身
sys.path.insert(0, str(ML_ROOT))


class MLFactorService:
    """ml 因子层服务 — 纯数学计算，无 LLM"""

    @staticmethod
    def _to_l7_score(composite_score: float) -> float:
        """将因子引擎 raw composite_score 映射到 L7 [-3, +3] 空间。

        因子 composite_score 经 z-score 横截面归一化，范围约 ±2。
        用 tanh 软饱和映射至 L7 空间，±2→±2.88（接近 ±3），±0.5→±1.17。
        """
        return round(3.0 * math.tanh(composite_score * 1.5), 3)

    STOCK_CODES = [
        "001390", "300652", "600372", "605368",
        "000592", "603189", "603557", "688202", "601801", "300676",
        "603127", "000999",
    ]

    def __init__(self):
        self._engine = None
        self._db_conn = None
        self._db_cols: Optional[list] = None
        self._last_hash: Optional[int] = None

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
        """计算所有股票的因子得分（含横截面归一化）"""
        # 第一步：计算所有股票的原始因子
        raw_results: list = []  # FactorResult 对象列表
        code_map: dict[str, Any] = {}  # code → FactorResult
        stock_data: dict[str, dict] = {}  # code → {close, volume} for time_series

        for code in self.STOCK_CODES:
            try:
                rows = self.db.execute(
                    f"SELECT * FROM stock_daily WHERE code=? ORDER BY date", (code,)
                ).fetchall()
                if not rows:
                    continue
                daily = [dict(zip(self.db_cols, r)) for r in rows]
                result = self.engine.compute_for_stock(code, daily)
                raw_results.append(result)
                code_map[code] = result
                # Prepare stock_data for time_series_normalize
                stock_data[code] = {
                    "close": np.array([d.get("close", 0) or 0 for d in daily], dtype=float),
                    "volume": np.array([d.get("volume", 0) or 0 for d in daily], dtype=float),
                }
            except Exception as e:
                continue

        # 第二步：时序归一化（逐股票自身历史分布，N<30 时比截面归一化更稳健）
        if raw_results:
            self.engine.time_series_normalize(raw_results, stock_data, lookback=120)

        # 第三步：提取 composite_score + L7 映射
        results = {}
        for code, result in code_map.items():
            raw_score = float(result.composite_score)
            results[code] = {
                "composite_score": raw_score,
                "l7_score": self._to_l7_score(raw_score),
                "composite_label": result.composite_label,
                "factors": {k: float(v) for k, v in result.raw_factors.items()},
            }

        return {
            "stocks": results,
            "updated_at": datetime.now().isoformat(),
            "source": "ml_factor_service",
        }

    def _compute_one(self, code: str) -> Optional[Dict[str, Any]]:
        """计算单只股票的因子 composite_score 和 L7 映射"""
        rows = self.db.execute(
            f"SELECT * FROM stock_daily WHERE code=? ORDER BY date", (code,)
        ).fetchall()
        if not rows:
            return None
        daily = [dict(zip(self.db_cols, r)) for r in rows]
        result = self.engine.compute_for_stock(code, daily)
        raw_score = float(result.composite_score)
        return {
            "composite_score": raw_score,
            "l7_score": self._to_l7_score(raw_score),
            "composite_label": result.composite_label,
            "factors": {k: float(v) for k, v in result.raw_factors.items()},
        }

    def run_once(self) -> Optional[Dict[str, Any]]:
        """执行一次计算，无变化时跳过写入。返回 None 表示跳过。"""
        data = self.compute_all()

        # 内容校验：仅对关键值（l7_score）做 hash，无变化则跳过写入
        scores = {
            code: info.get("l7_score", 0)
            for code, info in data.get("stocks", {}).items()
        }
        current_hash = hash(tuple(sorted(scores.items())))
        if self._last_hash == current_hash:
            return None  # 数据无变化，跳过写文件

        self._last_hash = current_hash
        tmp = str(OUTPUT_PATH) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.rename(tmp, str(OUTPUT_PATH))
        return data

    def close(self):
        """关闭数据库连接，释放资源"""
        if self._db_conn is not None:
            self._db_conn.close()
            self._db_conn = None

    def run_daemon(self, interval: int = 300):
        """守护模式"""
        print(f"[ml-factor] daemon started, interval={interval}s")
        while True:
            try:
                data = self.run_once()
                if data is not None:
                    count = len(data.get("stocks", {}))
                    print(f"[ml-factor] {datetime.now().isoformat()} {count} stocks computed")
                else:
                    print(f"[ml-factor] {datetime.now().isoformat()} no change, skipped")
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
