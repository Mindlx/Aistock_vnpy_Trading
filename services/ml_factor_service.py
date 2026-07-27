"""
ml 因子层实时服务 — 从 data_warehouse.db 读取日K线数据，
调用 FactorEngine 计算 14 因子 composite_score + l7_score。
同时读取最新的 LLM sentiment_score，以 L7 映射并入输出。

完全绕过 LLM 层，纯数学计算 + 独立 LLM 信号桥接。

用法:
    python services/ml_factor_service.py                     # 执行一次
    python services/ml_factor_service.py --daemon            # 守护模式
    python services/ml_factor_service.py --daemon -i 300     # 每300秒
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "data_warehouse.db"
OUTPUT_PATH = Path("data/realtime/ml_signal.json")

# 确保 data/realtime/ 存在
OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

# 将 ml 子系统源码加入路径 for FactorEngine 导入
sys.path.insert(0, str(PROJECT_ROOT / "systems" / "MindLynx-Aistock"))


class MLFactorService:
    """ml 因子层服务 — 纯数学计算，无 LLM"""

    @staticmethod
    def _load_stock_codes() -> list[str]:
        """从 config/stock_pool.csv 自动加载（单源配置）"""
        _pool_path = Path(__file__).resolve().parent.parent / "config" / "stock_pool.csv"
        if _pool_path.exists():
            with open(_pool_path) as _f:
                return [r["code"] for r in csv.DictReader(_f)]
        return []

    STOCK_CODES = _load_stock_codes()

    @staticmethod
    def _to_l7_score(composite_score: float) -> float:
        return round(3.0 * math.tanh(composite_score * 1.5), 3)

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
            meta = self.db.execute("PRAGMA table_info(daily_ohlcv)").fetchall()
            self._db_cols = [c[1] for c in meta]
        return self._db_cols

    def compute_all(self) -> Dict[str, Any]:
        """计算所有股票的因子得分（含横截面归一化）"""
        raw_results: list = []
        code_map: dict[str, Any] = {}
        stock_data: dict[str, dict] = {}

        for code in self.STOCK_CODES:
            try:
                rows = self.db.execute(
                    "SELECT * FROM daily_ohlcv WHERE stock_code=? ORDER BY date", (code,)
                ).fetchall()
                if not rows:
                    continue
                daily = [dict(zip(self.db_cols, r)) for r in rows]
                # 补充筹码集中度 (从 chip_distribution 取最新值)
                chip_row = self.db.execute(
                    "SELECT concentration FROM chip_distribution WHERE stock_code=? "
                    "ORDER BY date DESC LIMIT 1", (code,)
                ).fetchone()
                conc = float(chip_row[0]) if chip_row and chip_row[0] else 0.5
                for d in daily:
                    d["concentration"] = conc
                result = self.engine.compute_for_stock(code, daily)
                raw_results.append(result)
                code_map[code] = result
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

        # 第四步：数值级融合 — 读取最新 LLM sentiment_score，以 L7 映射并入
        try:
            import sqlite3
            ML_DB = PROJECT_ROOT / "systems" / "MindLynx-Aistock" / "data" / "stock_analysis.db"
            if ML_DB.exists():
                _ml_conn = sqlite3.connect(str(ML_DB))
                for code in list(results.keys()):
                    row = _ml_conn.execute(
                        "SELECT sentiment_score FROM analysis_history "
                        "WHERE code=? AND sentiment_score>0 "
                        "ORDER BY created_at DESC LIMIT 1", (code,)
                    ).fetchone()
                    if row and row[0]:
                        ss = int(row[0])
                        # 使用 normalizer 的 v4.0 映射
                        # 直接从文件路径加载(避免 sys.path 与 ML 子系统的 src 包冲突)
                        import importlib.util
                        _npath = str(PROJECT_ROOT / "src" / "normalizer.py")
                        _spec = importlib.util.spec_from_file_location("_ml_bridge_normalizer", _npath)
                        if _spec and _spec.loader:
                            _norm = importlib.util.module_from_spec(_spec)
                            _spec.loader.exec_module(_norm)
                            llm_l7 = _norm.SignalNormalizer.normalize_mindlynx_score(ss)
                        else:
                            llm_l7 = 0.0
                        results[code]["llm_sentiment"] = ss
                        results[code]["llm_l7_score"] = llm_l7
                    else:
                        results[code]["llm_sentiment"] = None
                        results[code]["llm_l7_score"] = None
                _ml_conn.close()
        except Exception as exc:
            print(f"[ml-factor] LLM bridge error: {exc}")

        return {
            "stocks": results,
            "updated_at": datetime.now().isoformat(),
            "source": "ml_factor_service",
        }

    def _compute_one(self, code: str) -> Optional[Dict[str, Any]]:
        """计算单只股票的因子 composite_score 和 L7 映射"""
        rows = self.db.execute(
            "SELECT * FROM daily_ohlcv WHERE stock_code=? ORDER BY date", (code,)
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
