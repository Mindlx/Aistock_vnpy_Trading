"""
A 股数据统一读取层 — 降级链：akshare → efinance → yfinance

保障 mind_TradingAgent 的 A 股数据源稳定可靠。
零侵入：不修改 TradingAgent 任何代码，仅在 wrapper 层调用。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class AshareDataProvider:
    """
    A 股数据提供器，自带降级链。

    使用方式:
        provider = AshareDataProvider()
        df = provider.get_daily("601801", "2026-01-01", "2026-05-28")
    """

    def get_daily(self, code: str, start: str, end: str) -> Optional[Any]:
        """获取日K线数据，自动降级"""
        df = self._try_akshare(code, start, end)
        if df is not None:
            return df
        df = self._try_efinance(code, start, end)
        if df is not None:
            return df
        df = self._try_yfinance(code, start, end)
        return df

    @staticmethod
    def _try_akshare(code: str, start: str, end: str) -> Optional[Any]:
        try:
            import akshare as ak
            df = ak.stock_zh_a_hist(
                symbol=code, period="daily",
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
                adjust="qfq",
            )
            if df is not None and not df.empty:
                logger.debug(f"akshare [{code}]: {len(df)} rows")
                return df
        except Exception as e:
            logger.warning(f"akshare [{code}] 失败: {e}")
        return None

    @staticmethod
    def _try_efinance(code: str, start: str, end: str) -> Optional[Any]:
        try:
            import efinance as ef
            df = ef.stock.get_quote_history(code, beg=start, end=end)
            if df is not None and not df.empty:
                logger.debug(f"efinance [{code}]: {len(df)} rows")
                return df
        except Exception as e:
            logger.warning(f"efinance [{code}] 失败: {e}")
        return None

    @staticmethod
    def _try_yfinance(code: str, start: str, end: str) -> Optional[Any]:
        try:
            import yfinance as yf
            from src.mind_stock_config import get_yfinance_ticker
            ticker = yf.Ticker(get_yfinance_ticker(code))
            df = ticker.history(start=start, end=end)
            if df is not None and not df.empty:
                logger.debug(f"yfinance [{code}]: {len(df)} rows")
                return df
        except Exception as e:
            logger.warning(f"yfinance [{code}] 失败: {e}")
        return None

    @staticmethod
    def verify_stock(code: str) -> Dict[str, Any]:
        """
        验证股票数据是否可获取，返回各数据源的可用状态。
        用于前置检查，避免 TradingAgent 白跑。
        """
        result: Dict[str, Any] = {"code": code, "available": False, "sources": {}}

        # akshare
        try:
            import akshare as ak
            df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date="20260520", adjust="qfq")
            ok = df is not None and not df.empty
            result["sources"]["akshare"] = ok
            if ok:
                result["available"] = True
                result["latest_close"] = float(df["收盘"].iloc[-1])
        except Exception as e:
            result["sources"]["akshare"] = False

        # efinance
        try:
            import efinance as ef
            df = ef.stock.get_quote_history(code, beg="2026-05-20")
            ok = df is not None and not df.empty
            result["sources"]["efinance"] = ok
            if ok:
                result["available"] = True
        except Exception:
            result["sources"]["efinance"] = False

        # yfinance
        try:
            from src.mind_stock_config import get_yfinance_ticker
            import yfinance as yf
            ticker = yf.Ticker(get_yfinance_ticker(code))
            df = ticker.history(period="5d")
            ok = df is not None and not df.empty
            result["sources"]["yfinance"] = ok
            if ok:
                result["available"] = True
        except Exception:
            result["sources"]["yfinance"] = False

        # 快速降级判定：主数据源（akshare/efinance）全部不可用，但有 yfinance
        primary_down = not result["sources"].get("akshare") and not result["sources"].get("efinance")
        has_yfinance = result["sources"].get("yfinance", False)
        result["fast_degrade"] = primary_down and has_yfinance

        return result
