"""
三系统输出数据加载适配器（零侵入版）

核心原则：不修改三个原有系统的任何代码。
所有读取逻辑完全自包含在 fusion_system 内部。

数据获取方式：
- lynx_vnpy:     通过 Python import 直接调用 lynx_signal.py 的导出函数
                  优先读取统一缓存 (UnifiedCache)，缓存未命中时回退到 Sina API
- MindLynx:      读取 reports/ 目录下已生成的 Markdown 报告文件
- TradingAgent:  读取 ~/.mind_tradingagent/logs/ 目录下已输出的 JSON 日志

⚠️ 仅供学习和研究目的，不构成任何投资建议
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.loaders.lynx_loader import LynxDataLoader
from src.loaders.mindlynx_loader import MindLynxDataLoader
from src.loaders.tradingagent_loader import TradingAgentDataLoader
from src.loaders.ml_factor_loader import MLFactorLoader
from src.loaders.alpha158_loader import Alpha158Loader

logger = logging.getLogger(__name__)

class UnifiedDataLoader:
    """
    统一数据加载器 — 按股票池组织四个系统的数据（ly/ml/at/ml_factor）。

    每个系统独立加载，互不影响。
    提供 single_source_of_truth 方法：为每只股票生成融合引擎所需的输入。

    使用方式:
        loader = UnifiedDataLoader()
        stock_signals = loader.load_all("2026-05-29")
        # 返回: [{"code": "601801", "name": "皖新传媒",
        #          "lynx_signal": "...", "lynx_prob_up": ...,
        #          "mindlynx_advice": "...", "mindlynx_score": ...,
        #          "tradingagent_rating": "...",
        #          "ml_factor_l7": ..., "ml_factor_valid": ...}, ...]
    """

    def __init__(
        self,
        lynx_root: str = "systems/lynx_vnpy",
        mindlynx_reports: str = "systems/MindLynx-Aistock/reports/",
        tradingagent_logs: str = "~/.mind_tradingagent/logs/",
        stock_pool_path: str = "config/stock_pool.csv",
    ) -> None:
        self.lynx = LynxDataLoader(lynx_root)
        self.mindlynx = MindLynxDataLoader(mindlynx_reports)
        self.tradingagent = TradingAgentDataLoader(tradingagent_logs)
        self.ml_factor = MLFactorLoader()
        self.alpha158 = Alpha158Loader()
        self.stock_pool = self._load_stock_pool(stock_pool_path)

        logger.info(
            f"UnifiedDataLoader: 股票池 {len(self.stock_pool)} 只, "
            f"系统: lynx/√ mindlynx/√ tradingagent/√ ml_factor/√ alpha158/√"
        )

    @staticmethod
    def _load_stock_pool(path: str) -> List[Dict[str, str]]:
        """从 CSV 加载股票池"""
        import csv
        stocks = []
        csv_path = Path(path)
        if not csv_path.exists():
            logger.warning(f"股票池文件不存在: {path}")
            return []
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, skipinitialspace=True)
            for row in reader:
                code = (row.get("code") or "").strip()
                name = (row.get("name") or "").strip()
                if code:
                    stocks.append({"code": code, "name": name})
        return stocks

    def _fetch_market_data(self, code: str) -> dict:
        """通过数据仓库获取行情"""
        data = {"price": 0.0, "pct_chg": 0.0, "volume_ratio": 0.0, "ma5": 0.0, "ma10": 0.0, "ma20": 0.0}
        try:
            from services.data_warehouse import WarehouseReader
            reader = WarehouseReader()
            hf = reader.get_daily_df(code, days=120)
            if hf is not None and not hf.empty and len(hf) >= 2:
                hl, hp = hf.iloc[-1], hf.iloc[-2]
                data["price"] = float(hl.get("close", hl.get("收盘", 0)))
                prev_close = float(hp.get("close", hp.get("收盘", 0)))
                if prev_close > 0:
                    data["pct_chg"] = round((data["price"] - prev_close) / prev_close * 100, 2)
                for col in ("volume_ratio", "ma5", "ma10", "ma20"):
                    if col in hf.columns:
                        data[col] = round(float(hf[col].iloc[-1]), 2)
        except Exception:
            logger.debug("数据仓库读取失败: %s", code)
        return data

    def load_all(self, date_str: str) -> List[Dict[str, Any]]:
        """
        加载三系统的所有信号，按股票池组织。
        返回: fusion_engine.fuse_stock_pool() 可直接接收的格式
        """
        logger.info(f"UnifiedDataLoader: 加载 {date_str} 数据...")

        # 各系统独立加载
        lynx_signals = self.lynx.load_by_date(date_str)
        mindlynx_signals = self.mindlynx.load_by_date(date_str)
        ta_signals = self.tradingagent.load_all_by_date(date_str)
        ml_factor_signals = self.ml_factor.load_by_date(date_str)
        alpha158_signals = self.alpha158.load_by_date(date_str)

        logger.info(
            f"数据就绪: lynx={len(lynx_signals)} mindlynx={len(mindlynx_signals)} "
            f"tradingagent={len(ta_signals)} ml_factor={len(ml_factor_signals)} alpha158={len(alpha158_signals)}"
        )

        stock_signals = []
        for stock in self.stock_pool:
            code = stock["code"]
            ly = lynx_signals.get(code, {})
            ml = mindlynx_signals.get(code, {})
            ta = ta_signals.get(code.upper(), {})
            mf = ml_factor_signals.get(code, {})
            a158 = alpha158_signals.get(code, {})

            has_data = bool(ly.get("signal")) or bool(ml.get("score")) or \
                       bool(ta.get("rating")) or mf.get("ml_factor_l7") is not None or \
                       a158.get("alpha158_l7") is not None
            if not has_data:
                continue

            md = self._fetch_market_data(code)
            stock_signals.append({
                "code": code, "name": stock["name"], **md,
                "lynx_signal": ly.get("signal", "观望"),
                "lynx_prob_up": float(ly.get("prob_up", 50.0)),
                **{f"mindlynx_{k}": ml.get(k, "") for k in ("trend", "sentiment_score", "factor_baseline",
                     "operation_advice", "analysis_summary", "ideal_buy", "stop_loss", "take_profit")},
                "mindlynx_advice": ml.get("signal", "观望"),
                "mindlynx_score": int(ml.get("score", 50)),
                "mindlynx_valid": bool(ml),
                "tradingagent_rating": ta.get("rating", "Hold"),
                "tradingagent_valid": bool(ta),
                "ta_debate_state": ta.get("debate_state", {}),
                "ml_factor_l7": mf.get("ml_factor_l7"),
                "ml_factor_valid": mf.get("ml_factor_l7") is not None,
                "ml_factor_label": mf.get("ml_factor_label", ""),
                "alpha158_l7": a158.get("alpha158_l7"),
                "alpha158_valid": a158.get("alpha158_l7") is not None,
            })

        logger.info(f"UnifiedDataLoader: 组织完成 {len(stock_signals)} 只股票")
        return stock_signals
