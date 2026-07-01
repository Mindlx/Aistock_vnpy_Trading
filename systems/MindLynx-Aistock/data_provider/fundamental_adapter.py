"""
AkShare fundamental adapter (fail-open).

This adapter intentionally uses capability probing against multiple AkShare
endpoint candidates. It should never raise to caller; partial data is allowed.
"""

from __future__ import annotations

import json
from datetime import date as _date_type
from pathlib import Path
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, cast

import pandas as pd

logger = logging.getLogger(__name__)


# ── 资本流缓存层 (B') ──

def _capital_flow_cache_path() -> Path:
    _root = Path(__file__).resolve().parent.parent.parent.parent
    return _root / "data" / "realtime" / "capital_flow_cache.json"


def _read_capital_flow_cache(stock_code: str) -> dict | None:
    p = _capital_flow_cache_path()
    if not p.exists():
        return None
    try:
        cache = json.loads(p.read_text(encoding="utf-8"))
        today = _date_type.today().isoformat()
        entry = cache.get(stock_code)
        if entry and entry.get("fetched_date") == today and entry.get("data", {}).get("stock_flow"):
            return entry["data"]
    except Exception:
        pass
    return None


def _write_capital_flow_cache(stock_code: str, data: dict) -> None:
    p = _capital_flow_cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        cache = {}
        if p.exists():
            cache = json.loads(p.read_text(encoding="utf-8"))
        cache[stock_code] = {"fetched_date": _date_type.today().isoformat(), "data": data}
        p.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


_DIVIDEND_KEYWORD_MAP: dict[str, list[str]] = {
    "per_share": [
        "每股派息",
        "每股现金红利",
        "每股分红",
        "每股派现",
        "派现(元/股)",
        "派息(元/股)",
        "税前派息(元/股)",
        "现金分红(税前)",
    ],
    "plan_text": [
        "分配方案",
        "分红方案",
        "实施方案",
        "派息方案",
        "方案",
        "预案",
        "方案说明",
    ],
    "ex_dividend_date": ["除权除息日", "除息日", "除权日", "除权除息", "除息日期"],
    "record_date": ["股权登记日", "登记日"],
    "announce_date": ["公告日期", "公告日", "实施公告日", "预案公告日"],
    "report_date": ["报告期", "报告日期", "截止日期", "统计截止日期"],
}


def _safe_float(value: Any) -> float | None:
    """Best-effort float conversion with string cleaning."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v if not (v != v) else None  # NaN → None
    try:
        s = str(value).strip().replace(",", "").replace("%", "").replace("亿元", "00000000").replace("万元", "0000")
        if not s:
            return None
        v = float(s)
        return v if not (v != v) else None
    except (ValueError, TypeError):
        return None


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value).strip()
    except Exception:
        return ""


def _normalize_code(code: Any) -> str | None:
    if code is None:
        return None
    c = str(code).strip().upper().replace(".", "").replace("SZ", "").replace("SH", "").replace("BJ", "")
    if c.isdigit():
        return c
    return None


def _normalize_report_date(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if len(raw) >= 10:
        return raw[:10]
    return raw if raw else None


def _parse_dividend_plan_to_per_share(plan_text: str) -> float | None:
    text = _safe_str(plan_text)
    if not text:
        return None

    for pattern in (
        r"(?:每)?\s*10\s*股?\s*派(?:发)?\s*([0-9]+(?:\.[0-9]+)?)\s*元",
        r"10\s*派\s*([0-9]+(?:\.[0-9]+)?)\s*元",
    ):
        match = re.search(pattern, text)
        if match:
            parsed = _safe_float(match.group(1))
            if parsed is not None and parsed > 0:
                return parsed / 10.0

    match_per_share = re.search(r"每\s*股\s*派(?:发)?\s*([0-9]+(?:\.[0-9]+)?)\s*元", text)
    if match_per_share:
        return _safe_float(match_per_share.group(1))

    return None


def _extract_dividend_info(bonus_df: pd.DataFrame, stock_code: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if bonus_df is None or bonus_df.empty:
        return result

    code_cols = [
        c for c in bonus_df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码", "ts_code", "symbol"))
    ]
    target = _normalize_code(stock_code)
    if not code_cols:
        row = bonus_df.iloc[0]
    else:
        matched = pd.DataFrame()
        for col in code_cols:
            try:
                series = bonus_df[col].astype(str).map(_normalize_code)
                cur = bonus_df[series == target]
                if not cur.empty:
                    matched = cur
                    break
            except Exception:
                continue
        row = matched.iloc[0] if not matched.empty else bonus_df.iloc[0]

    plan_text = _safe_str(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["plan_text"]))
    if plan_text:
        plan_result = _parse_dividend_plan_to_per_share(plan_text)
        if plan_result is not None:
            result["per_share"] = plan_result
            result["plan_text"] = plan_text

    ex_date = _safe_str(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["ex_dividend_date"]))
    if ex_date:
        result["ex_dividend_date"] = ex_date

    record_date = _safe_str(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["record_date"]))
    if record_date:
        result["record_date"] = record_date

    announce_date = _safe_str(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["announce_date"]))
    if announce_date:
        result["announce_date"] = announce_date

    return result


def _safe_date_parse(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(raw[:10].replace("-", "").replace("/", ""), fmt.replace("-", "").replace("/", ""))
            return dt.strftime("%Y-%m-%d")
        except Exception:
            continue
    return _normalize_report_date(value)


def _pick_by_keywords(row: pd.Series, keywords: list[str]) -> Any | None:
    """
    Return first non-empty row value whose column name contains any keyword.
    """
    for col in row.index:
        col_s = str(col)
        if any(k in col_s for k in keywords):
            val = row.get(col)
            if val is not None and str(val).strip() not in ("", "-", "nan", "None"):
                return val
    return None


def _quote_style_value(row: pd.Series, keywords: list[str]) -> float | None:
    """
    Pick a percentage-based value from a column whose name matches keywords,
    but also matches the row-level metadata style where column values contain '%'.
    """
    for col in row.index:
        col_s = str(col)
        if any(k in col_s for k in keywords):
            raw = row.get(col)
            s = _safe_str(raw)
            if s and "%" in s:
                try:
                    return float(s.replace("%", "").strip())
                except Exception:
                    pass
    return None


def _extract_from_financial_abstract(df: pd.DataFrame) -> dict[str, Any] | None:
    """
    Parse transposed stock_financial_abstract format (rows=indicators, cols=dates).
    """
    if df is None or df.empty:
        return None
    if "ITEM" not in df.columns:
        return None

    result: dict[str, Any] = {}
    date_cols = [c for c in df.columns if c != "ITEM"]

    def _pick(col_keywords: list[str]) -> pd.Series | None:
        for keyword in col_keywords:
            mask = df["ITEM"].astype(str).str.contains(keyword, na=False)
            matched = df[mask]
            if not matched.empty:
                return matched.iloc[0]
        return None

    revenue_row = _pick(["营业总收入", "营业收入", "营收"])
    profit_row = _pick(["净利润", "归母净利润", "母公司股东净利润"])
    roe_row = _pick(["净资产收益率", "ROE"])
    gross_row = _pick(["毛利率"])
    ocf_row = _pick(["经营活动产生的现金流量净额", "经营活动现金流", "经营现金流"])

    if date_cols:
        result["report_date"] = _normalize_report_date(date_cols[0])

    for row_, keys, target_key in [
        (revenue_row, ["营业总收入", "营业收入", "营收"], "revenue"),
        (profit_row, ["净利润", "归母净利润", "母公司股东净利润"], "net_profit_parent"),
        (roe_row, ["净资产收益率", "ROE"], "roe"),
        (gross_row, ["毛利率"], "gross_margin"),
        (ocf_row, ["经营活动产生的现金流量净额", "经营活动现金流", "经营现金流"], "operating_cash_flow"),
    ]:
        if row_ is not None:
            for col_s in date_cols:
                val = row_.get(col_s)
                parsed = _safe_float(val)
                if parsed is not None:
                    result[target_key] = parsed
                    break

    # Revenue YoY & Net Profit YoY from abstract (if present)
    rev_yoy_row = _pick(["营业收入同比增长", "营收同比", "收入同比", "同比增长率"])
    if rev_yoy_row is not None:
        for col_s in date_cols:
            val = rev_yoy_row.get(col_s)
            parsed = _safe_float(val)
            if parsed is not None:
                result["revenue_yoy"] = parsed
                break

    profit_yoy_row = _pick(["净利润同比增长", "净利同比", "归母净利润同比增长"])
    if profit_yoy_row is not None:
        for col_s in date_cols:
            val = profit_yoy_row.get(col_s)
            parsed = _safe_float(val)
            if parsed is not None:
                result["net_profit_yoy"] = parsed
                break

    return result if any(v is not None for v in result.values()) else None


def _extract_latest_row(df: pd.DataFrame, stock_code: str) -> pd.Series | None:
    """
    Select the most relevant row for the given stock.
    """
    if df is None or df.empty:
        return None

    code_cols = [
        c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码", "ts_code", "symbol"))
    ]
    target = _normalize_code(stock_code)
    if code_cols:
        for col in code_cols:
            try:
                series = df[col].astype(str).map(_normalize_code)
                matched = df[series == target]
                if not matched.empty:
                    return matched.iloc[0]
            except Exception:
                continue
        return None

    # Fallback: use latest row
    return df.iloc[0]


class AkshareFundamentalAdapter:
    """AkShare adapter for fundamentals, capital flow and dragon-tiger signals."""

    def _call_df_candidates(
        self,
        candidates: list[tuple[str, dict[str, Any]]],
    ) -> tuple[pd.DataFrame | None, str | None, list[str]]:
        errors: list[str] = []
        try:
            import akshare as ak
        except Exception as exc:
            return None, None, [f"import_akshare:{type(exc).__name__}"]

        for attempt in range(3):  # 原始 + 2 次重试
            for func_name, kwargs in candidates:
                fn = getattr(ak, func_name, None)
                if fn is None:
                    continue
                try:
                    df = fn(**kwargs)
                    if isinstance(df, pd.Series):
                        df = df.to_frame().T
                    if isinstance(df, pd.DataFrame) and not df.empty:
                        return df, func_name, errors
                except Exception as exc:
                    err_tag = f"{func_name}:{type(exc).__name__}"
                    if err_tag not in errors:
                        errors.append(err_tag)
                    continue

            # 全部候选人失败：如果是连接类错误且还有重试次数，等一秒再试
            if attempt < 2 and any("ConnectionError" in e or "RemoteDisconnected" in e or "Timeout" in e or "ProtocolError" in e for e in errors):
                time.sleep(1)
                errors.append(f"retry:{attempt+1}")
            else:
                break
        return None, None, errors

    def _try_tushare_capital_flow(self, stock_code: str) -> dict | None:
        """Fallback: try Tushare Pro moneyflow (旧版个股资金流) when akshare all fail."""
        try:
            from src.config import get_config
            cfg = get_config()
            if not cfg.tushare_token:
                return None

            from .tushare_fetcher import _TushareHttpClient
            client = _TushareHttpClient(cfg.tushare_token, timeout=15)

            # Convert: 000592 → 000592.SZ,  601801 → 601801.SH
            if stock_code[:1] in ("6", "9"):
                market = "SH"
            elif stock_code[:1] in ("4", "8"):
                market = "BJ"
            else:
                market = "SZ"
            ts_code = f"{stock_code}.{market}"

            df = client.query("moneyflow", ts_code=ts_code)
            if df is None or df.empty:
                return None

            # moneyflow 返回多条记录（多日），取最新一条
            if "trade_date" in df.columns:
                df = df.sort_values("trade_date", ascending=False)
            row = df.iloc[0]

            # 主力净流入 = 大单净流入(买-卖) + 超大单净流入(买-卖)
            # moneyflow 字段: buy_lg_vol/sell_lg_vol (大单), buy_elg_vol/sell_elg_vol (超大单)
            # 先用金额(Amount)字段，失败回退到量(Vol)字段
            buy_lg = _safe_float(row.get("buy_lg_amount"))
            sell_lg = _safe_float(row.get("sell_lg_amount"))
            if buy_lg is None:
                buy_lg = _safe_float(row.get("buy_lg_vol"))
                sell_lg = _safe_float(row.get("sell_lg_vol"))

            buy_elg = _safe_float(row.get("buy_elg_amount"))
            sell_elg = _safe_float(row.get("sell_elg_amount"))
            if buy_elg is None:
                buy_elg = _safe_float(row.get("buy_elg_vol"))
                sell_elg = _safe_float(row.get("sell_elg_vol"))

            if buy_lg is not None and sell_lg is not None:
                net_main = (buy_lg - sell_lg) + (
                    (buy_elg - sell_elg) if buy_elg is not None and sell_elg is not None else 0.0
                )
            else:
                net_main = _safe_float(row.get("net_mf_amount"))  # total net flow (万元)
                if net_main is None:
                    net_main = _safe_float(row.get("net_mf_vol"))

            if net_main is None:
                return None

            return {
                "main_net_inflow": net_main,
                "inflow_5d": None,    # moneyflow 仅单日, Phase 1 暂不拉多日
                "inflow_10d": None,
            }
        except Exception as exc:
            logger.debug("[AkshareFundamentalAdapter] tushare capital flow fallback failed: %s", exc)
            return None

    def get_fundamental_bundle(self, stock_code: str) -> dict[str, Any]:
        """
        Return normalized fundamental blocks from AkShare with partial tolerance.
        """
        result: dict[str, Any] = {
            "status": "not_supported",
            "growth": {},
            "earnings": {},
            "institution": {},
            "source_chain": [],
            "errors": [],
        }

        # Financial indicators
        fin_df, fin_source, fin_errors = self._call_df_candidates(
            [
                ("stock_financial_abstract", {"symbol": stock_code}),
                ("stock_financial_analysis_indicator", {"symbol": stock_code}),
            ]
        )
        result["errors"].extend(fin_errors)
        if fin_df is not None:
            # stock_financial_abstract 是转置格式（行为指标、列为日期）
            if fin_source == "stock_financial_abstract":
                fin_parsed = _extract_from_financial_abstract(fin_df)
                if fin_parsed is not None:
                    result["growth"] = {
                        "revenue_yoy": fin_parsed.get("revenue_yoy"),
                        "net_profit_yoy": fin_parsed.get("net_profit_yoy"),
                        "roe": fin_parsed.get("roe"),
                        "gross_margin": fin_parsed.get("gross_margin"),
                    }
                    financial_report_payload = {
                        "report_date": fin_parsed.get("report_date"),
                        "revenue": fin_parsed.get("revenue"),
                        "net_profit_parent": fin_parsed.get("net_profit_parent"),
                        "operating_cash_flow": fin_parsed.get("operating_cash_flow"),
                        "roe": fin_parsed.get("roe"),
                    }
                    if any(v is not None for v in financial_report_payload.values()):
                        result["earnings"]["financial_report"] = financial_report_payload
                    result["source_chain"].append(f"growth:{fin_source}")
            else:
                # stock_financial_analysis_indicator 标准列格式
                row = _extract_latest_row(fin_df, stock_code)
                if row is not None:
                    revenue_yoy = _safe_float(_pick_by_keywords(row, ["营业收入同比", "营收同比", "收入同比", "同比增长"]))
                    profit_yoy = _safe_float(_pick_by_keywords(row, ["净利润同比", "净利同比", "归母净利润同比"]))
                    roe = _safe_float(_pick_by_keywords(row, ["净资产收益率", "ROE", "净资产收益"]))
                    gross_margin = _safe_float(_pick_by_keywords(row, ["毛利率"]))
                    report_date = _normalize_report_date(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["report_date"]))
                    revenue = _safe_float(_pick_by_keywords(row, ["营业总收入", "营业收入", "营收"]))
                    net_profit_parent = _safe_float(_pick_by_keywords(row, ["归母净利润", "母公司股东净利润", "净利润"]))
                    operating_cash_flow = _safe_float(
                        _pick_by_keywords(row, ["经营活动产生的现金流量净额", "经营现金流", "经营活动现金流"])
                    )
                    result["growth"] = {
                        "revenue_yoy": revenue_yoy,
                        "net_profit_yoy": profit_yoy,
                        "roe": roe,
                        "gross_margin": gross_margin,
                    }

                    financial_report_payload = {
                        "report_date": report_date,
                        "revenue": revenue,
                        "net_profit_parent": net_profit_parent,
                        "operating_cash_flow": operating_cash_flow,
                        "roe": roe,
                    }
                    if any(v is not None for v in financial_report_payload.values()):
                        result["earnings"]["financial_report"] = financial_report_payload
                    result["source_chain"].append(f"growth:{fin_source}")

        # Dividend
        bonus_df, bonus_source, bonus_errors = self._call_df_candidates(
            [
                ("stock_dividents_cninfo", {"symbol": stock_code}),
                ("stock_dividend_rights", {"symbol": stock_code}),
            ]
        )
        result["errors"].extend(bonus_errors)
        if bonus_df is not None:
            dividend_info = _extract_dividend_info(bonus_df, stock_code)
            if dividend_info:
                result["earnings"]["dividend"] = dividend_info
                result["source_chain"].append(f"dividend:{bonus_source}")

        # Institutional holdings (top10 holders)
        if stock_code[:1] in ("6", "9"):
            top10_symbol = stock_code
        else:
            top10_symbol = stock_code.zfill(6)

        top10_df, top10_source, top10_errors = self._call_df_candidates(
            [
                ("stock_top10_holders", {"symbol": top10_symbol}),
                ("stock_top10_holders", {"symbol": top10_symbol, "date": ""}),
            ]
        )
        result["errors"].extend(top10_errors)
        if top10_df is not None:
            row = _extract_latest_row(top10_df, stock_code)
            if row is not None:
                holder_change = _safe_float(_pick_by_keywords(row, ["增减", "变化", "持股变化", "变动"]))
                result["institution"]["top10_holder_change"] = holder_change
                result["source_chain"].append(f"top10:{top10_source}")

        has_content = bool(result["growth"] or result["earnings"] or result["institution"])
        result["status"] = "partial" if has_content else "not_supported"
        return result

    def get_capital_flow(self, stock_code: str, top_n: int = 5) -> dict[str, Any]:
        """
        Return stock + sector capital flow.

        (A') Tushare Pro moneyflow_dc 优先（已付费，更稳定）.
        (B') 数据湖缓存: 先查 data/realtime/capital_flow_cache.json, 成功则写入.
        (C') akshare 10 个 fallback 降级.
        """
        result: dict[str, Any] = {
            "status": "not_supported",
            "stock_flow": {},
            "sector_rankings": {"top": [], "bottom": []},
            "source_chain": [],
            "errors": [],
        }

        # ── (B') 先查缓存 ──
        cached = _read_capital_flow_cache(stock_code)
        if cached is not None:
            result.update(cached)
            result["source_chain"].append("capital_cache:hit")
            return result

        # ── (A') 付费源优先：Tushare Pro moneyflow_dc ──
        tushare_flow = self._try_tushare_capital_flow(stock_code)
        if tushare_flow is not None:
            result["stock_flow"] = tushare_flow
            result["source_chain"].append("capital_stock:tushare")
        else:
            # ── (C') tushare 失败 → akshare 10 candidates 降级 ──
            market = "sh" if stock_code[:1] in ("6", "9") else "sz"
            alt_market = "1" if stock_code[:1] in ("6", "9") else "0"
            stock_df, stock_source, stock_errors = self._call_df_candidates([
                ("stock_individual_fund_flow", {"stock": stock_code, "market": market}),
                ("stock_individual_fund_flow", {"symbol": stock_code, "market": market}),
                ("stock_individual_fund_flow", {"stock": stock_code, "market": alt_market}),
                ("stock_individual_fund_flow", {"symbol": stock_code, "market": alt_market}),
                ("stock_individual_fund_flow", {"stock": stock_code}),
                ("stock_individual_fund_flow", {"symbol": stock_code}),
                ("stock_main_fund_flow", {"symbol": stock_code}),
                ("stock_main_fund_flow", {}),
                ("stock_fund_flow_individual", {"symbol": stock_code}),
            ])
            result["errors"].extend(stock_errors)
            if stock_df is not None:
                row = _extract_latest_row(stock_df, stock_code)
                if row is not None:
                    net_inflow = _safe_float(_pick_by_keywords(row, ["主力净流入", "净流入", "净额"]))
                    inflow_5d = _safe_float(_pick_by_keywords(row, ["5日", "五日"]))
                    inflow_10d = _safe_float(_pick_by_keywords(row, ["10日", "十日"]))
                    result["stock_flow"] = {
                        "main_net_inflow": net_inflow,
                        "inflow_5d": inflow_5d,
                        "inflow_10d": inflow_10d,
                    }
                    result["source_chain"].append(f"capital_stock:{stock_source}")

        # ── 板块排行（独立于个股资金流，两边都尝试）──
        sector_df, sector_source, sector_errors = self._call_df_candidates(
            [
                ("stock_sector_fund_flow_rank", {}),
                ("stock_sector_fund_flow_summary", {}),
            ]
        )
        result["errors"].extend(sector_errors)
        if sector_df is not None:
            name_col = next(
                (c for c in sector_df.columns if any(k in str(c) for k in ("板块", "行业", "名称", "name"))), None
            )
            flow_col = next(
                (c for c in sector_df.columns if any(k in str(c) for k in ("净流入", "主力", "flow", "净额"))), None
            )
            if name_col and flow_col:
                work_df = sector_df[[name_col, flow_col]].copy()
                work_df[flow_col] = pd.to_numeric(work_df[flow_col], errors="coerce")
                work_df = work_df.dropna(subset=[flow_col])
                top_df = work_df.nlargest(top_n, flow_col)
                bottom_df = work_df.nsmallest(top_n, flow_col)
                result["sector_rankings"] = {
                    "top": [
                        {"name": _safe_str(r[name_col]), "net_inflow": float(r[flow_col])} for _, r in top_df.iterrows()
                    ],
                    "bottom": [
                        {"name": _safe_str(r[name_col]), "net_inflow": float(r[flow_col])}
                        for _, r in bottom_df.iterrows()
                    ],
                }
                result["source_chain"].append(f"capital_sector:{sector_source}")

        has_content = bool(
            result["stock_flow"] or result["sector_rankings"]["top"] or result["sector_rankings"]["bottom"]
        )
        result["status"] = "partial" if has_content else "not_supported"

        # ── (B') 写入数据湖缓存（即使 partial 也写，供跨 task 复用）──
        if has_content:
            _write_capital_flow_cache(stock_code, result)

        return result
