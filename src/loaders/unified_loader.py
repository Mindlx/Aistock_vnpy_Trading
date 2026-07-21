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

import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.loaders.lynx_loader import LynxDataLoader
from src.loaders.mindlynx_loader import MindLynxDataLoader
from src.loaders.tradingagent_loader import TradingAgentDataLoader
from src.loaders.ml_factor_loader import MLFactorLoader
from src.loaders.alpha158_loader import Alpha158Loader
from src.unified_cache import UnifiedCache, get_cache

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

    def load_all(self, date_str: str) -> List[Dict[str, Any]]:
        """
        加载三系统的所有信号，按股票池组织。

        每个系统独立加载：
        - 如果一个系统暂时不可用（文件不存在/导入失败），自动跳过
        - 融合引擎的 _compute_adjusted_weights 会处理缺失

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

        # 按股票池组织
        stock_signals = []
        for stock in self.stock_pool:
            code = stock["code"]
            lynx = lynx_signals.get(code, {})
            mindlynx = mindlynx_signals.get(code, {})
            ta = ta_signals.get(code.upper(), {})

            # lynx: signal 可能含 emoji 如 "🟢 买入"
            lynx_signal_raw = lynx.get("signal", "")
            lynx_prob_up = lynx.get("prob_up", 50.0)

            # MindLynx: signal/score 来自报告解析
            mindlynx_advice = mindlynx.get("signal", "")
            mindlynx_score = mindlynx.get("score", 50)
            mindlynx_has_data = bool(mindlynx)  # 子系统是否真正有数据（非空dict）

            # TradingAgent: rating 来自日志
            ta_rating = ta.get("rating", "")
            ta_has_data = bool(ta)  # 子系统是否真正有数据（非空dict）

            # ml_factor 因子层信号
            ml_factor = ml_factor_signals.get(code, {})
            ml_factor_l7 = ml_factor.get("ml_factor_l7")
            ml_factor_has_data = ml_factor_l7 is not None

            # alpha158 因子层信号
            alpha158 = alpha158_signals.get(code, {})
            alpha158_l7 = alpha158.get("alpha158_l7")
            alpha158_has_data = alpha158_l7 is not None

            # 至少有一个系统有真实数据才加入
            has_data = bool(lynx_signal_raw) or bool(mindlynx_advice) or bool(ta_rating) or ml_factor_has_data or alpha158_has_data

            price = 0.0
            pct_chg = 0.0
            volume_ratio = 0.0
            ma5 = ma10 = ma20 = 0.0

            # 通过数据仓库获取行情和技术指标（缓存优先，5层降级链）
            try:
                from services.data_warehouse import WarehouseReader
                reader = WarehouseReader()
                hf = reader.get_daily_df(code, days=120)
                if hf is not None and not hf.empty and len(hf) >= 2:
                    hl = hf.iloc[-1]
                    hp = hf.iloc[-2]
                    price = float(hl.get("close", hl.get("收盘", 0)))
                    prev_close = float(hp.get("close", hp.get("收盘", 0)))
                    if prev_close > 0:
                        pct_chg = round((price - prev_close) / prev_close * 100, 2)
                    if "volume_ratio" in hf.columns:
                        volume_ratio = round(float(hf["volume_ratio"].iloc[-1]), 2)
                    if "ma5" in hf.columns:
                        ma5 = round(float(hf["ma5"].iloc[-1]), 2)
                    if "ma10" in hf.columns:
                        ma10 = round(float(hf["ma10"].iloc[-1]), 2)
                    if "ma20" in hf.columns:
                        ma20 = round(float(hf["ma20"].iloc[-1]), 2)
            except Exception:
                logger.debug("数据仓库读取失败: %s", code)

            if has_data:
                stock_signals.append({
                    "code": code,
                    "name": stock["name"],
                    "price": price,
                    "pct_chg": pct_chg,
                    "volume_ratio": volume_ratio,
                    "ma5": ma5,
                    "ma10": ma10,
                    "ma20": ma20,
                    "lynx_signal": lynx_signal_raw if lynx_signal_raw else "观望",
                    "lynx_prob_up": float(lynx_prob_up) if lynx_prob_up is not None else 50.0,
                    "mindlynx_advice": mindlynx_advice if mindlynx_advice else "观望",
                    "mindlynx_score": int(mindlynx_score) if mindlynx_score is not None else 50,
                    "mindlynx_valid": mindlynx_has_data,
                    "mindlynx_trend": mindlynx.get("trend", ""),
                    "mindlynx_sentiment": mindlynx.get("sentiment_score"),
                    "mindlynx_factor_baseline": mindlynx.get("factor_baseline"),
                    "mindlynx_operation": mindlynx.get("operation_advice", mindlynx_advice),
                    "mindlynx_analysis": mindlynx.get("analysis_summary", ""),
                    "mindlynx_ideal_buy": mindlynx.get("ideal_buy"),
                    "mindlynx_stop_loss": mindlynx.get("stop_loss"),
                    "mindlynx_take_profit": mindlynx.get("take_profit"),
                    "tradingagent_rating": ta_rating if ta_rating else "Hold",
                    "tradingagent_valid": ta_has_data,
                    "ta_debate_state": ta.get("debate_state", {}),
                    # ml_factor 因子层信号
                    "ml_factor_l7": ml_factor_l7,
                    "ml_factor_valid": ml_factor_has_data,
                    "ml_factor_label": ml_factor.get("ml_factor_label", ""),
                    # alpha158 因子信号
                    "alpha158_l7": alpha158_l7,
                    "alpha158_valid": alpha158_has_data,
                })

        logger.info(f"UnifiedDataLoader: 组织完成 {len(stock_signals)} 只股票")
        return stock_signals
