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
        优先检查数据仓库（本地 SQLite，微秒级），再试外部 API。
        用于前置检查，避免 TradingAgent 白跑。
        """
        result: Dict[str, Any] = {"code": code, "available": False, "sources": {}}

        # 数据仓库（本地 SQLite，首选）
        try:
            from services.data_warehouse import WarehouseReader
            wr = WarehouseReader()
            wdf = wr.get_daily(code, days=5)
            if wdf and len(wdf) >= 2:
                result["sources"]["warehouse"] = True
                result["available"] = True
                result["latest_close"] = wdf[-1].get("close", 0) if isinstance(wdf[-1], dict) else 0
        except Exception:
            result["sources"]["warehouse"] = False

        # akshare
        try:
            import akshare as ak
            df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date="20260520", adjust="qfq")
            ok = df is not None and not df.empty
            result["sources"]["akshare"] = ok
            if ok:
                result["available"] = True
                result["latest_close"] = float(df["收盘"].iloc[-1])
        except Exception:
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

        return result
