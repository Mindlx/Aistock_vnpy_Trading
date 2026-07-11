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

import requests

from src.unified_cache import UnifiedCache, get_cache

logger = logging.getLogger(__name__)

class Alpha158Loader:
    """Alpha158 因子层信号加载器 — 读取 alpha158_signal.json"""

    def __init__(self, signal_path: str = "data/realtime/alpha158_signal.json"):
        self.signal_path = Path(signal_path)

    def load_by_date(self, date_str: str | None = None) -> Dict[str, Dict[str, Any]]:
        if not self.signal_path.exists():
            logger.debug(f"alpha158_signal.json 不存在: {self.signal_path}")
            return {}
        try:
            with open(self.signal_path, "r") as f:
                data = json.load(f)
            stocks_data = data.get("stocks", {})
            results = {}
            for code, info in stocks_data.items():
                l7 = info.get("l7_score")
                if l7 is not None:
                    results[code] = {
                        "alpha158_l7": float(l7),
                        "alpha158_prob_up": float(info.get("prob_up", 0)),
                    }
            logger.debug(f"alpha158: {len(results)} 只股票")
            return results
        except Exception as e:
            logger.debug(f"alpha158 加载失败: {e}")
            return {}
