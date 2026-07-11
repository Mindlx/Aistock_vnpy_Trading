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

class LynxDataLoader:
    """
    lynx_vnpy 信号读取器（零侵入版）

    不修改 lynx_signal.py 的任何代码。
    通过 Python import 机制直接调用其导出函数:
      - fetch_daily_bars(code) → DataFrame
      - compute_features(df) → DataFrame  
      - predict_signal(df, code, name) → dict

    模型文件位置: lynx_vnpy/models/{code}_model.pkl
    模型会自动按需训练或加载（lynx_signal.py 内部处理）。

    统一缓存集成（v2.0）:
      - 优先从 UnifiedCache (SQLite) 读取 OHLCV 数据
      - 缓存命中 → 跳过 Sina API 调用 (~60% API 调用减少)
      - 缓存未命中 → 调用 fetch_daily_bars → 自动写入缓存
      - 缓存默认 TTL: 24h (daily_ohlcv)，可通过 settings.yaml 调整
      - 零侵入: 不修改 lynx_signal.py 任何代码

    使用方式:
        loader = LynxDataLoader()
        signals = loader.load_by_date("2026-05-29")
        # 返回: {"601801": {"signal": "🟢 买入", "prob_up": 72.0, ...}, ...}
    """

    def __init__(
        self,
        lynx_project_root: str = "systems/lynx_vnpy",
        cache: Optional[UnifiedCache] = None,
        cache_enabled: bool = True,
        cache_ttl: int = 86400,
    ):
        """
        参数:
            lynx_project_root: lynx_vnpy 项目根目录的相对路径
            cache: UnifiedCache 实例。None 时使用全局默认缓存。
            cache_enabled: 是否启用缓存。设为 False 则每次强制从 API 获取。
            cache_ttl: 缓存 TTL（秒），默认 86400 (1天)。
        """
        self.lynx_root = Path(lynx_project_root).expanduser().resolve()
        self._imported = False
        self._lynx_module = None
        self._cache_enabled = cache_enabled
        self._cache_ttl = cache_ttl
        self._cache = cache if cache is not None else get_cache()
        self._cache_stats = {"hits": 0, "misses": 0, "fresh": 0}

    def _ensure_imported(self):
        """确保 lynx_signal 模块已导入（只导入一次）"""
        if self._imported:
            return True

        lynx_signal_path = self.lynx_root / "lynx_signal.py"
        if not lynx_signal_path.exists():
            logger.error(f"lynx_signal.py 不存在: {lynx_signal_path}")
            return False

        # 将 lynx_vnpy 项目根加入 sys.path
        if str(self.lynx_root) not in sys.path:
            sys.path.insert(0, str(self.lynx_root))

        try:
            import lynx_signal
            self._lynx_module = lynx_signal
            self._imported = True
            logger.info(f"lynx_signal 已导入 (from {lynx_signal_path})")
            return True
        except ImportError as e:
            logger.error(f"导入 lynx_signal 失败: {e}")
            # 可能缺少依赖，打印更详细的错误
            import traceback
            logger.debug(traceback.format_exc())
            return False

    def get_stock_list(self) -> List[str]:
        """获取 lynx_vnpy 的股票列表"""
        if not self._ensure_imported():
            return []
        return list(self._lynx_module.STOCK_CODES)

    def _fetch_with_cache(self, code: str) -> Optional[Any]:
        """Fetch OHLCV data with cache-first strategy.

        Returns DataFrame in Sina Chinese-column format (compatible with
        compute_features), or None if data unavailable.

        Flow:
            1. Check UnifiedCache (SQLite) → if hit & fresh, return remapped data
            2. Call lynx.fetch_daily_bars(code) → Sina API
            3. Store result in UnifiedCache → return
        """
        if self._cache_enabled:
            # Check if already fresh (skip API entirely if data is recent)
            if self._cache.is_fresh(code, "daily_ohlcv", self._cache_ttl):
                df_cached = self._cache.get_daily_ohlcv(
                    code, days=120, ttl_seconds=self._cache_ttl,
                    reverse_map=True, reverse_map_source="sina",
                )
                if df_cached is not None and not df_cached.empty:
                    self._cache_stats["hits"] += 1
                    self._cache_stats["fresh"] += 1
                    logger.debug(f"[LynxCache] {code} → fresh cache hit ({len(df_cached)} rows)")
                    return df_cached

            # Try cache even if not marked fresh (stale data better than API failure)
            df_cached = self._cache.get_daily_ohlcv(
                code, days=120, reverse_map=True, reverse_map_source="sina",
            )
            if df_cached is not None and not df_cached.empty:
                self._cache_stats["hits"] += 1
                logger.debug(f"[LynxCache] {code} → cache hit ({len(df_cached)} rows)")
                return df_cached

        # Cache miss: fetch from Sina API
        self._cache_stats["misses"] += 1
        lynx = self._lynx_module
        df = lynx.fetch_daily_bars(code)

        # Store to cache on success
        if df is not None and not df.empty and self._cache_enabled:
            try:
                self._cache.put_daily_ohlcv(code, df, source="sina")
                logger.debug(f"[LynxCache] {code} → stored to cache ({len(df)} rows)")
            except Exception as e:
                logger.warning(f"[LynxCache] {code} store failed: {e}")

        return df

    @property
    def cache_stats(self) -> Dict[str, int]:
        """Return cache hit/miss statistics for monitoring."""
        return dict(self._cache_stats)

    def load_by_date(self, date_str: str) -> Dict[str, Dict[str, Any]]:
        """
        通过直接调用 lynx_signal.py 的导出函数生成信号。

        不调用 run()（会打印+推送），而是逐只股票调用：
          _fetch_with_cache (cache-first) → compute_features → predict_signal

        返回:
            {stock_code: signal_dict, ...}
            signal_dict 包含: signal, strength, prob_up, rsi, macd_hist, atr_ratio, ...
        """
        if not self._ensure_imported():
            logger.warning("lynx_signal 无法导入，返回空信号")
            return {}

        lynx = self._lynx_module
        stock_codes = self.get_stock_list()
        if not stock_codes:
            return {}

        logger.info(
            f"lynx_vnpy: 开始获取 {len(stock_codes)} 只股票信号 "
            f"(缓存: {'启用' if self._cache_enabled else '禁用'})..."
        )
        results = {}

        for code in stock_codes:
            try:
                # 1. 获取日K数据（缓存优先 → Sina API 回退）
                df = self._fetch_with_cache(code)
                if df is None:
                    logger.warning(f"lynx_vnpy [{code}]: 无数据")
                    time.sleep(2)
                    continue

                # 2. 计算特征 + 双模型集成预测
                name = df.iloc[-1].get('股票名称', code)

                # 双模型集成（RF+LGB），回退到RF-only
                if hasattr(lynx, 'predict_ensemble'):
                    sig = lynx.predict_ensemble(df, code, name)
                else:
                    df_feat = lynx.compute_features(df)
                    sig = lynx.predict_signal(df_feat, code, name)
                if sig:
                    results[code] = sig
                    logger.debug(f"lynx_vnpy [{code}]: {sig['signal']} 置信 {sig['prob_up']}%")
                else:
                    logger.warning(f"lynx_vnpy [{code}]: 信号不足")

                # API 限流保护（缓存命中时无需等待）
                if not self._cache_enabled:
                    time.sleep(2)

            except Exception as e:
                logger.error(f"lynx_vnpy [{code}]: 异常 {e}")
                continue

        h, m = self._cache_stats["hits"], self._cache_stats["misses"]
        saved = h * 2  # ~2s saved per cache hit (API call + sleep)
        logger.info(
            f"lynx_vnpy: 完成，{len(results)}/{len(stock_codes)} 只获得信号 "
            f"(缓存命中 {h}/{h+m}, 节省 ~{saved}s API 调用时间)"
        )
        return results

    def run_original(self) -> List[Dict[str, Any]]:
        """
        直接运行 lynx_signal.run() 的原始逻辑（会打印到控制台）。
        仅用于诊断/调试，融合系统不建议使用。
        """
        if not self._ensure_imported():
            return []
        self._lynx_module.run()
        return []

    def force_cache_refresh(self) -> int:
        """Force-refresh all stock data in cache. Returns count of stocks refreshed."""
        if not self._ensure_imported():
            return 0
        lynx = self._lynx_module
        stock_codes = self.get_stock_list()
        count = 0
        for code in stock_codes:
            df = lynx.fetch_daily_bars(code)
            if df is not None and not df.empty:
                self._cache.put_daily_ohlcv(code, df, source="sina")
                count += 1
                time.sleep(2)
        logger.info(f"[LynxCache] Force-refreshed {count}/{len(stock_codes)} stocks")
        return count
