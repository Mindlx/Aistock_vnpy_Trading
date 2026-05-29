"""
===================================
DataMixin — 数据获取相关方法
===================================

从 StockAnalysisPipeline 巨石类中提取的数据获取相关方法。
"""

import logging
from datetime import date, datetime
from typing import Any

import pandas as pd

from data_provider.base import normalize_stock_code
from src.core.trading_calendar import (
    get_effective_trading_date,
    get_market_for_stock,
    get_market_now,
    is_market_open,
)

logger = logging.getLogger(__name__)


class DataMixin:
    """数据获取相关方法 mixin，由 StockAnalysisPipeline 多重继承。"""

    def fetch_and_save_stock_data(
        self,
        code: str,
        force_refresh: bool = False,
        current_time: datetime | None = None,
    ) -> tuple[bool, str | None]:
        """
        获取并保存单只股票数据

        断点续传逻辑：
        1. 检查数据库是否已有最新可复用交易日数据
        2. 如果有且不强制刷新，则跳过网络请求
        3. 否则从数据源获取并保存

        Args:
            code: 股票代码
            force_refresh: 是否强制刷新（忽略本地缓存）
            current_time: 本轮运行冻结的参考时间，用于统一断点续传目标交易日判断

        Returns:
            Tuple[是否成功, 错误信息]
        """
        stock_name = code
        try:
            # 首先获取股票名称
            stock_name = self.fetcher_manager.get_stock_name(code, allow_realtime=False)

            target_date = self._resolve_resume_target_date(code, current_time=current_time)

            # 断点续传检查：如果最新可复用交易日的数据已存在，则跳过
            if not force_refresh and self.db.has_today_data(code, target_date):
                logger.info(f"{stock_name}({code}) {target_date} 数据已存在，跳过获取（断点续传）")
                return True, None

            # 从数据源获取数据
            logger.info(f"{stock_name}({code}) 开始从数据源获取数据...")
            df, source_name = self.fetcher_manager.get_daily_data(code, days=30)

            if df is None or df.empty:
                return False, "获取数据为空"

            # 保存到数据库
            saved_count = self.db.save_daily_data(df, code, source_name)
            logger.info(f"{stock_name}({code}) 数据保存成功（来源: {source_name}，新增 {saved_count} 条）")

            return True, None

        except Exception as e:
            error_msg = f"获取/保存数据失败: {str(e)}"
            logger.error(f"{stock_name}({code}) {error_msg}")
            return False, error_msg

    def _augment_historical_with_realtime(self, df: pd.DataFrame, realtime_quote: Any, code: str) -> pd.DataFrame:
        """
        Augment historical OHLCV with today's realtime quote for intraday MA calculation.
        Issue #234: Use realtime price instead of yesterday's close for technical indicators.
        """
        if df is None or df.empty or "close" not in df.columns:
            return df
        if realtime_quote is None:
            return df
        price = getattr(realtime_quote, "price", None)
        if price is None or not (isinstance(price, (int, float)) and price > 0):
            return df

        # Optional: skip augmentation on non-trading days (fail-open)
        enable_realtime_tech = getattr(self.config, "enable_realtime_technical_indicators", True)
        if not enable_realtime_tech:
            return df
        market = get_market_for_stock(code)
        market_today = get_market_now(market).date()
        if market and not is_market_open(market, market_today):
            return df

        last_val = df["date"].max()
        last_date = (
            last_val.date()
            if hasattr(last_val, "date")
            else (last_val if isinstance(last_val, date) else pd.Timestamp(last_val).date())
        )
        yesterday_close = float(df.iloc[-1]["close"]) if len(df) > 0 else price
        open_p = (
            getattr(realtime_quote, "open_price", None) or getattr(realtime_quote, "pre_close", None) or yesterday_close
        )
        high_p = getattr(realtime_quote, "high", None) or price
        low_p = getattr(realtime_quote, "low", None) or price
        vol = getattr(realtime_quote, "volume", None) or 0
        amt = getattr(realtime_quote, "amount", None)
        pct = getattr(realtime_quote, "change_pct", None)

        if last_date >= market_today:
            # Update last row with realtime close (copy to avoid mutating caller's df)
            df = df.copy()
            idx = df.index[-1]
            df.loc[idx, "close"] = price
            if open_p is not None:
                df.loc[idx, "open"] = open_p
            if high_p is not None:
                df.loc[idx, "high"] = high_p
            if low_p is not None:
                df.loc[idx, "low"] = low_p
            if vol:
                df.loc[idx, "volume"] = vol
            if amt is not None:
                df.loc[idx, "amount"] = amt
            if pct is not None:
                df.loc[idx, "pct_chg"] = pct
        else:
            # Append virtual today row
            new_row = {
                "code": code,
                "date": market_today,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": price,
                "volume": vol,
                "amount": amt if amt is not None else 0,
                "pct_chg": pct if pct is not None else 0,
            }
            new_df = pd.DataFrame([new_row])
            df = pd.concat([df, new_df], ignore_index=True)
        return df

    @staticmethod
    def _resolve_resume_target_date(code: str, current_time: datetime | None = None) -> date:
        """
        Resolve the trading date used by checkpoint/resume checks.
        """
        market = get_market_for_stock(normalize_stock_code(code))
        return get_effective_trading_date(market, current_time=current_time)
