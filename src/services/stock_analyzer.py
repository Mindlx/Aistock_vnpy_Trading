from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)


class StockAnalyzer:
    """技术面评分与降级分析。"""

    def technical_rating(self, hist) -> str:
        """Compute Buy/Overweight/Hold/Underweight/Sell from OHLCV."""
        closes = hist["Close"].values
        highs = hist["High"].values
        lows = hist["Low"].values
        volumes = hist["Volume"].values
        n = len(closes)
        if n < 20:
            return "Hold"

        latest_close = float(closes[-1])
        prev_close = float(closes[-2]) if n >= 2 else latest_close

        ma5 = np.mean(closes[-5:]) if n >= 5 else latest_close
        ma10 = np.mean(closes[-10:]) if n >= 10 else latest_close
        ma20 = np.mean(closes[-20:]) if n >= 20 else latest_close

        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains[-14:]) if n >= 15 else 0
        avg_loss = np.mean(losses[-14:]) if n >= 15 else 1e-6
        rs = avg_gain / max(avg_loss, 1e-10)
        rsi = 100 - (100 / (1 + rs))

        import pandas as pd
        ema12 = pd.Series(closes).ewm(span=12).mean().values
        ema26 = pd.Series(closes).ewm(span=26).mean().values
        macd = ema12 - ema26
        macd_hist = macd[-1] - np.mean(macd[-9:]) if n >= 9 else macd[-1]

        vol_ma5 = np.mean(volumes[-5:]) if n >= 5 else volumes[-1]
        vol_ratio = volumes[-1] / max(vol_ma5, 1)

        score = 0
        if latest_close > ma10:
            score += 1
        elif latest_close < ma10:
            score -= 1

        if ma5 > ma10:
            score += 1
        elif ma5 < ma10:
            score -= 1

        if ma10 > ma20:
            score += 1
        elif ma10 < ma20:
            score -= 1

        if rsi > 60:
            score += 1
        elif rsi < 40:
            score -= 1

        if macd_hist > 0:
            score += 1
        elif macd_hist < 0:
            score -= 1

        if vol_ratio > 1.2 and latest_close > prev_close:
            score += 1
        elif vol_ratio > 1.2 and latest_close < prev_close:
            score -= 1

        if score >= 4:
            return "Buy"
        elif score >= 2:
            return "Overweight"
        elif score <= -4:
            return "Sell"
        elif score <= -2:
            return "Underweight"
        else:
            return "Hold"

    def fallback_analysis(
        self, stock_code: str, trade_date: str, stock_name: str = "", error: str = ""
    ) -> Dict[str, Any]:
        result = self._empty_result(stock_code, stock_name, error)

        try:
            # 仓库优先 (本地SQLite, 微秒级)
            from services.data_warehouse import WarehouseReader
            reader = WarehouseReader()
            hist = reader.get_daily_df(stock_code, days=90)
            if hist is not None and not hist.empty and len(hist) >= 20:
                hist = hist.rename(columns={
                    "open": "Open", "high": "High", "low": "Low",
                    "close": "Close", "volume": "Volume",
                })
                using_warehouse = True
            else:
                import yfinance as yf
                from src.mind_stock_config import is_shanghai
                suffix = ".SS" if is_shanghai(stock_code) else ".SZ"
                yf_ticker = f"{stock_code}{suffix}"
                ticker = yf.Ticker(yf_ticker)
                hist = ticker.history(period="3mo")
                using_warehouse = False

            if hist is not None and not hist.empty:
                latest = hist.iloc[-1]
                if len(hist) >= 2:
                    prev_close = hist.iloc[-2]["Close"]
                    change_pct = ((float(latest["Close"]) - float(prev_close)) / float(prev_close)) * 100
                else:
                    change_pct = 0.0

                rating = self.technical_rating(hist)
                yf_ticker = f"{stock_code}.{'SS' if stock_code.startswith(('6','5','9')) else 'SZ'}"
                result.update({
                    "code": stock_code,
                    "name": stock_name or stock_code,
                    "yf_ticker": yf_ticker,
                    "rating": rating,
                    "change_pct": round(change_pct, 2),
                    "latest_close": float(latest["Close"]),
                    "trade_date": trade_date,
                    "success": True,
                    "error": None,
                    "_fallback_data": not using_warehouse,
                    "_data_source": "warehouse" if using_warehouse else "yfinance",
                })
        except Exception as e:
            logger.error(f"降级分析失败 [{stock_code}]: {e}")
            result["error"] = str(e)

        return result

    @staticmethod
    def _empty_result(code: str, name: str, error: str = "") -> Dict[str, Any]:
        return {
            "code": code,
            "name": name,
            "yf_ticker": "",
            "rating": "Hold",
            "final_decision": "",
            "trade_date": "",
            "success": False,
            "error": error,
        }
