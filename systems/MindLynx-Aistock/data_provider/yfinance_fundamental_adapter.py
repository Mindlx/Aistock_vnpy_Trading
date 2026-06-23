"""
Yfinance fundamental adapter for HK/US stocks (fail-open).

Provides fundamental data (PE, PB, ROE, EPS, dividend yield, beta, sector,
industry, etc.) via yfinance Ticker.info for stocks not covered by AkShare.

Pattern follows AkshareFundamentalAdapter but with simpler semantics since
yfinance provides a pre-structured info dict rather than heterogeneous DF endpoints.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _safe_float(value: Any) -> float | None:
    """Best-effort float conversion."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _pct(decimal: Any) -> float | None:
    """Convert decimal ratio (0.05) to percentage (5.0)."""
    v = _safe_float(decimal)
    return round(v * 100, 4) if v is not None else None


class YfinanceFundamentalAdapter:
    """
    yfinance adapter for fundamentals of HK/US stocks.

    The caller (DataFetcherManager.get_fundamental_context) is responsible for:
    - Code normalization (base.py normalizes codes before calling here)
    - Market detection (us/hk)
    - Timeout / retry / budget management
    """

    def get_fundamental_bundle(self, stock_code: str, market: str) -> dict[str, Any]:
        """
        Return normalized fundamental blocks from yfinance Ticker.info.

        Args:
            stock_code: e.g. 'HK00700', 'AAPL', '0700.HK'
            market: 'hk' or 'us'

        Returns:
            Dict with growth, earnings, institution blocks (same shape as
            AkshareFundamentalAdapter.get_fundamental_bundle so the caller
            can use the same unpacking logic).
        """
        result: dict[str, Any] = {
            "status": "not_supported",
            "growth": {},
            "earnings": {},
            "institution": {},
            "valuation": {},
            "source_chain": [],
            "errors": [],
        }

        yf_code = self._convert_code(stock_code, market)

        import yfinance as yf

        try:
            ticker = yf.Ticker(yf_code)
            info = ticker.info
            if not info or not isinstance(info, dict) or len(info) == 0:
                result["status"] = "not_supported"
                result["errors"].append("yfinance Ticker.info returned empty dict")
                result["source_chain"].append(f"yfinance_fundamental:{yf_code}")
                return result
        except Exception as e:
            logger.warning("[YfinanceFundamental] %s yf.Ticker(%s).info failed: %s", stock_code, yf_code, e)
            result["errors"].append(f"ticker_info:{type(e).__name__}")
            result["source_chain"].append(f"yfinance_fundamental:{yf_code}")
            return result

        normalized = self._normalize_info(info)

        # ── Growth block ──────────────────────────────────────────
        growth: dict[str, Any] = {}
        for key in ("revenue_growth", "earnings_growth", "profit_margins", "roe"):
            val = normalized.get(key)
            if val is not None:
                growth[key] = val

        # ── Earnings block ────────────────────────────────────────
        earnings: dict[str, Any] = {}
        for key in ("eps_ttm", "eps_forward", "dividend_yield", "payout_ratio", "next_earnings_date"):
            val = normalized.get(key)
            if val is not None:
                earnings[key] = val

        # ── Institution block (limited from yfinance) ──────────────
        institution: dict[str, Any] = {}
        inst_pct = normalized.get("held_percent_institutions")
        if inst_pct is not None:
            institution["held_percent_institutions"] = inst_pct

        # ── Top-level enrichments (used by _attach_belong_boards / calibration) ──
        for key in ("beta", "sector", "industry", "full_time_employees", "description"):
            val = normalized.get(key)
            if val is not None and val != "":
                result[key] = val

        result["growth"] = growth
        result["earnings"] = earnings
        result["institution"] = institution

        has_content = bool(growth or earnings or institution)
        result["status"] = "ok" if has_content else "not_supported"
        result["source_chain"].append(f"yfinance_fundamental:{yf_code}")

        logger.debug(
            "[YfinanceFundamental] %s (%s) bundle: status=%s growth_keys=%d earnings_keys=%d",
            stock_code,
            yf_code,
            result["status"],
            len(growth),
            len(earnings),
        )
        return result

    @staticmethod
    def _convert_code(stock_code: str, market: str) -> str:
        """
        Convert internal stock code to yfinance Ticker format.

        Examples:
            _convert_code('HK00700', 'hk') -> '0700.HK'
            _convert_code('0700.HK', 'hk')  -> '0700.HK'
            _convert_code('AAPL', 'us')     -> 'AAPL'
            _convert_code('BRK.B', 'us')    -> 'BRK.B'
        """
        code = stock_code.strip().upper()

        if market == "hk":
            if code.startswith("HK"):
                digits = code[2:].lstrip("0") or "0"
                # Pad to 4 digits to match yfinance format (0700.HK, 0011.HK, etc.)
                return f"{digits.zfill(4)}.HK"
            if code.endswith(".HK"):
                return code
            if code.isdigit():
                return f"{code.zfill(4)}.HK"
            return code

        # US stocks pass through (AAPL, MSFT, BRK.B, etc.)
        if market == "us":
            return code

        # Fallback: detect from code pattern
        if code.startswith("HK") or code.endswith(".HK"):
            return YfinanceFundamentalAdapter._convert_code(stock_code, "hk")
        if code.replace(".", "").isalpha():
            return code
        return code

    @staticmethod
    def _normalize_info(raw_info: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize raw yfinance Ticker.info dict to canonical field names.

        Yaml Finance field name -> canonical mapping:
          revenueGrowth          -> revenue_growth (%, decimal->pct)
          earningsGrowth         -> earnings_growth (%, decimal->pct)
          profitMargins          -> profit_margins (%, decimal->pct)
          returnOnEquity         -> roe (%, decimal->pct)
          trailingEps            -> eps_ttm
          forwardEps             -> eps_forward
          dividendYield          -> dividend_yield (%, decimal->pct)
          trailingAnnualDividendYield -> also tried for dividend_yield
          payoutRatio            -> payout_ratio (%, decimal->pct)
          earningsDate           -> next_earnings_date
          heldPercentInstitutions -> held_percent_institutions (%, decimal->pct)
          beta                   -> beta
          sector                 -> sector
          industry               -> industry
          fullTimeEmployees      -> full_time_employees
          longBusinessSummary    -> description
        """
        result: dict[str, Any] = {}

        # Growth metrics (yfinance returns as decimal, convert to percentage)
        result["revenue_growth"] = _pct(raw_info.get("revenueGrowth"))
        result["earnings_growth"] = _pct(raw_info.get("earningsGrowth"))
        result["profit_margins"] = _pct(raw_info.get("profitMargins"))
        result["roe"] = _pct(raw_info.get("returnOnEquity"))

        # EPS
        result["eps_ttm"] = _safe_float(raw_info.get("trailingEps"))
        result["eps_forward"] = _safe_float(raw_info.get("forwardEps"))

        # Dividend yield: try trailingAnnualDividendYield first (more stable),
        # fall back to dividendYield (sometimes 0 for non-dividend stocks).
        div_yield_raw = raw_info.get("trailingAnnualDividendYield") or raw_info.get("dividendYield")
        result["dividend_yield"] = _pct(div_yield_raw)
        result["payout_ratio"] = _pct(raw_info.get("payoutRatio"))

        # Earnings date
        result["next_earnings_date"] = YfinanceFundamentalAdapter._extract_earnings_date(raw_info)

        # Institution holding
        result["held_percent_institutions"] = _pct(raw_info.get("heldPercentInstitutions"))

        # Risk
        result["beta"] = _safe_float(raw_info.get("beta"))

        # Classification
        result["sector"] = _safe_str(raw_info.get("sector"))
        result["industry"] = _safe_str(raw_info.get("industry"))
        result["full_time_employees"] = raw_info.get("fullTimeEmployees")
        result["description"] = _safe_str(raw_info.get("longBusinessSummary"))

        return result

    @staticmethod
    def _extract_earnings_date(info: dict[str, Any]) -> str | None:
        """
        Extract the next earnings date from yfinance info dict.

        Tries multiple keys in order: earningsDate (timestamp list),
        earningsTimestamp (int), nextEarningsDate (string).
        """
        candidates = []
        for ed_key in ("earningsDate", "earningsTimestamp", "nextEarningsDate"):
            ed_val = info.get(ed_key)
            if ed_val is not None:
                if isinstance(ed_val, (list, tuple)) and len(ed_val) > 0:
                    ed_val = ed_val[0]
                try:
                    import datetime

                    if isinstance(ed_val, (int, float)):
                        candidates.append(datetime.datetime.fromtimestamp(ed_val).strftime("%Y-%m-%d"))
                    else:
                        s = str(ed_val)[:10]
                        # basic date sanity
                        if len(s) == 10 and s[4] == "-":
                            candidates.append(s)
                except Exception:
                    continue
        return candidates[0] if candidates else None
