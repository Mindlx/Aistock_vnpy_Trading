"""Stock knowledge base builder.

Aggregates historical context for each stock from multiple sources:
1. Prior analysis history (what was the last conclusion?)
2. Fundamental snapshots (PE/PB trends)
3. Factor score history (is this stock improving or declining?)
4. Event history (recent announcements, buybacks, etc.)
5. Board/sector context

Outputs a compact knowledge prompt injected into LLM analysis context.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/stock_analysis.db"
REPORTS_DIR = "reports"

# Per-stock knowledge base cache (5min TTL, avoids ~650 duplicate queries/day)
_kb_cache: dict[str, tuple[float, dict]] = {}
_KB_CACHE_TTL = 300


def _parse_report_for_stock(code: str, report_path: str) -> str | None:
    """Extract per-stock analysis from daily report markdown.

    Returns a compact summary: risks, catalysts, one-liner conclusion.
    """
    import re

    try:
        with open(report_path, encoding="utf-8") as f:
            text = f.read()
    except (FileNotFoundError, OSError):
        return None

    # Find the stock section: "## 🟡 雷迪克 (300652)" or "## ⚪ XXX (code)"
    pattern = rf"## [^\n]*\({re.escape(code)}\)\s*\n(.*?)(?=\n## |\n---|\Z)"
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return None

    section = match.group(1)

    # Extract key parts
    parts: list[str] = []
    for line in section.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("|"):
            continue
        # Capture meaningful lines only
        if any(kw in stripped for kw in ["舆情", "业绩预期", "风险警报", "利好催化", "一句话决策", "最新动态"]):
            parts.append(stripped)

    if not parts:
        return None

    return "\n".join(parts[:8])  # Cap at 8 lines


def _parse_ts(ts_str: str) -> datetime:
    """Parse datetime string with or without microseconds."""
    try:
        return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")


def build_stock_knowledge(
    code: str,
    db_path: str = DEFAULT_DB_PATH,
    *,
    max_age_days: dict[str, int] | None = None,
) -> dict:
    """Build comprehensive stock knowledge context with freshness control.

    Args:
        code: stock code
        db_path: SQLite database path
        max_age_days: per-source TTL in days. Defaults:
            analysis: 7d (sentiment decays fast)
            fundamentals: 90d (quarterly reports)
            events: 30d (announcement impact window)
            sector: 180d (industry rarely changes)
            factor: 14d (factor scores decay)

    Returns:
        dict with knowledge_prompt key for LLM injection.
    """
    # Cache check: avoid redundant queries within 5-minute window
    import time as _time

    _now_ts = _time.time()
    if code in _kb_cache:
        _ts, _cached = _kb_cache[code]
        if _now_ts - _ts < _KB_CACHE_TTL:
            return _cached

    if max_age_days is None:
        max_age_days = {
            "analysis": 7,
            "fundamentals": 90,
            "events": 30,
            "sector": 180,
            "factor": 14,
        }

    conn = sqlite3.connect(db_path)
    now = datetime.now()
    result: dict = {}
    freshness: list[str] = []

    # 1. Prior analysis — within TTL
    cutoff = (now - timedelta(days=max_age_days["analysis"])).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT sentiment_score, operation_advice, trend_prediction, created_at "
        "FROM analysis_history WHERE code=? AND sentiment_score IS NOT NULL "
        "AND date(created_at) >= ? "
        "ORDER BY created_at DESC LIMIT 5",
        (code, cutoff[:10]),
    ).fetchall()
    if rows:
        prior = []
        for score, advice, trend, ts_str in rows[:3]:
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f")
            except ValueError:
                ts = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
            age = (now.date() - ts.date()).days
            # Use absolute timestamp — LLMs need exact dates, not relative ones
            ts_label = ts.strftime("%m-%d %H:%M")
            prior.append(f"[{ts_label}] {score}分 {advice} {trend}")
        result["analysis_history"] = prior
        scores = [r[0] for r in reversed(rows)]
        if len(scores) >= 2 and scores[0] != scores[-1]:
            result["score_trend"] = f"{'↑' if scores[-1] > scores[0] else '↓'}{abs(scores[-1] - scores[0])}分"
        else:
            result["score_trend"] = "→ 稳定"
        # Freshness indicator — use absolute cutoff date
        oldest = min((now.date() - _parse_ts(r[3]).date()).days for r in rows[:3]) if rows else 0
        if oldest > 3:
            cutoff_date = (now - timedelta(days=max_age_days["analysis"])).strftime("%Y-%m-%d")
            freshness.append(f"⚠️ 分析数据早于{cutoff_date}(已过TTL={max_age_days['analysis']}d)")

    # 1.5 Factor score history — track composite_score trend (F5 fix)
    cutoff_factor = (now - timedelta(days=max_age_days["factor"])).strftime("%Y-%m-%d")
    factor_rows = conn.execute(
        "SELECT context_snapshot, created_at FROM analysis_history "
        "WHERE code=? AND context_snapshot IS NOT NULL AND date(created_at) >= ? "
        "ORDER BY created_at DESC LIMIT 5",
        (code, cutoff_factor[:10]),
    ).fetchall()
    if factor_rows:
        import re
        factor_scores = []
        for snap_str, ts_str in factor_rows:
            try:
                snap = __import__("json").loads(snap_str) if isinstance(snap_str, str) else snap_str
                fp = snap.get("factor_profile", "")
                m = re.search(r"\*\*综合得分\*\*:\s*([+-]?\d+\.?\d*)\s*\((.+?)\)", fp)
                if m:
                    factor_scores.append((float(m.group(1)), m.group(2), ts_str[:10]))
            except Exception:
                continue
        if len(factor_scores) >= 2:
            recent = factor_scores[0][0]
            prior = factor_scores[-1][0]
            trend = "↑" if recent > prior else ("↓" if recent < prior else "→")
            result["factor_trend"] = (
                f"近{max_age_days['factor']}天因子综合得分: {factor_scores[0][2]} {recent:+.2f} ({factor_scores[0][1]}), "
                f"较{len(factor_scores)}次前 {trend}{abs(recent - prior):.2f}"
            )
        elif factor_scores:
            result["factor_trend"] = f"最新因子综合得分: {factor_scores[0][2]} {factor_scores[0][0]:+.2f} ({factor_scores[0][1]})"

    # 2. Fundamental trend
    cutoff = (now - timedelta(days=max_age_days["fundamentals"])).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT payload, created_at FROM fundamental_snapshot "
        "WHERE code=? AND date(created_at) >= ? ORDER BY created_at DESC LIMIT 2",
        (code, cutoff),
    ).fetchall()
    if rows and rows[0][0]:
        try:
            snap = __import__("json").loads(rows[0][0]) if isinstance(rows[0][0], str) else rows[0][0]
            val = snap.get("valuation", snap.get("data", {}))
            pe = val.get("pe_ratio") if isinstance(val, dict) else None
            pb = val.get("pb_ratio") if isinstance(val, dict) else None
            if pe or pb:
                result["fundamentals"] = f"PE={pe or '?'} PB={pb or '?'}"
                age = (now.date() - _parse_ts(rows[0][1]).date()).days
                if age > 30:
                    cutoff_date = (now - timedelta(days=max_age_days["fundamentals"])).strftime("%Y-%m-%d")
                    freshness.append(f"⚠️ 基本面数据早于{cutoff_date}(已过TTL={max_age_days['fundamentals']}d)")
        except (__import__("json").JSONDecodeError, KeyError, TypeError):
            pass

    # 3. Authoritative announcements (from news_intel — 权威公告)
    cutoff = (now - timedelta(days=max_age_days["events"])).strftime("%Y-%m-%d")
    ann_rows = conn.execute(
        "SELECT dimension, title, snippet, provider FROM news_intel "
        "WHERE code=? AND dimension IN ('announcements','latest_news') "
        "AND fetched_at >= ? ORDER BY fetched_at DESC LIMIT 3",
        (code, cutoff),
    ).fetchall()
    if ann_rows:
        announcements = []
        for dim, title, snippet, provider in ann_rows:
            text = str(title or '') + ' ' + str(snippet or '')
            # Extract key terms
            keywords = ["减持", "增持", "回购", "业绩", "重组", "中标", "合同", "退市", "ST", "分红", "送转", "问询", "处罚"]
            hits = [k for k in keywords if k in text]
            label = f"[{dim}] " + (",".join(hits) if hits else str(title or '')[:60])
            announcements.append(label)
        if announcements:
            result["authoritative_news"] = announcements[:5]

    # 4. Events (alert triggers)
    cutoff = (now - timedelta(days=max_age_days["events"])).strftime("%Y-%m-%d %H:%M:%S")
    try:
        event_rows = conn.execute(
            "SELECT reason, status, triggered_at FROM alert_triggers "
            "WHERE target=? AND triggered_at >= ? ORDER BY triggered_at DESC LIMIT 5",
            (code, cutoff),
        ).fetchall()
        if event_rows:
            events = []
            for reason, status, ts in event_rows[:3]:
                events.append(f"[{status}] {reason[:80] if reason else '?'}")
            result["events"] = events
    except sqlite3.OperationalError:
        pass

    # 4. Board/sector
    cutoff = (now - timedelta(days=max_age_days["sector"])).strftime("%Y-%m-%d %H:%M:%S")
    board_rows = conn.execute(
        "SELECT payload FROM fundamental_snapshot WHERE code=? "
        "AND payload LIKE '%belong_boards%' AND date(created_at) >= ? "
        "ORDER BY created_at DESC LIMIT 1",
        (code, cutoff),
    ).fetchall()
    if board_rows and board_rows[0][0]:
        try:
            snap = __import__("json").loads(board_rows[0][0]) if isinstance(board_rows[0][0], str) else board_rows[0][0]
            boards = snap.get("belong_boards", [])
            if boards:
                result["sector"] = ", ".join(b.get("name", b) for b in boards[:3] if isinstance(b, dict))
        except (__import__("json").JSONDecodeError, KeyError):
            pass

    conn.close()

    # 5. Latest report content (daily markdown report)
    report_text = _find_latest_report(code)
    if report_text:
        result["daily_report"] = report_text

    result["knowledge_prompt"] = _build_prompt(code, result, freshness)
    _kb_cache[code] = (_time.time(), result)
    return result


def _find_latest_report(code: str) -> str | None:
    """Find and parse the most recent daily report for this stock."""
    import glob as _glob

    report_files = sorted(_glob.glob(f"{REPORTS_DIR}/report_*.md"), reverse=True)
    for rf in report_files[:3]:  # Check last 3 reports
        text = _parse_report_for_stock(code, rf)
        if text:
            age = _report_age_days(rf)
            if age <= 7:  # Only use reports within 7 days
                return text[:400]  # Cap to prevent prompt bloat
    return None


def _report_age_days(path: str) -> int:
    """Extract date from report filename and compute age in days."""
    import re as _re
    from datetime import date as _date

    match = _re.search(r"(\d{4})(\d{2})(\d{2})", path)
    if not match:
        return 999
    try:
        report_date = _date(int(match[1]), int(match[2]), int(match[3]))
        return (_date.today() - report_date).days
    except ValueError:
        return 999


def _build_prompt(code: str, ctx: dict, freshness: list[str] | None = None) -> str:
    """Build a compact knowledge prompt for LLM injection."""
    lines = [f"### 股票背景知识 ({code})"]

    # Data freshness header
    if freshness:
        lines.append("> ⚠️ 数据新鲜度警告:")
        for f in freshness:
            lines.append(f"> {f}")

    if ctx.get("analysis_history"):
        lines.append("**近期分析历史** (TTL=7天):")
        for h in ctx["analysis_history"]:
            lines.append(f"  - {h}")
        lines.append(f"  评分趋势: {ctx.get('score_trend', '?')}")

    if ctx.get("factor_trend"):
        lines.append(f"\n**因子评分趋势** (TTL=14天): {ctx['factor_trend']}")

    if ctx.get("fundamentals"):
        lines.append(f"\n**基本面** (TTL=90天): {ctx['fundamentals']}")

    if ctx.get("sector"):
        lines.append(f"\n**所属板块** (TTL=180天): {ctx['sector']}")

    if ctx.get("authoritative_news"):
        lines.append("\n**权威公告** (TTL=30天, 来源:东方财富/巨潮资讯):")
        for n in ctx["authoritative_news"]:
            lines.append(f"  - {n}")

    if ctx.get("events"):
        lines.append("\n**近期事件** (TTL=30天):")
        for e in ctx["events"]:
            lines.append(f"  - {e}")

    if ctx.get("daily_report"):
        lines.append("\n**上次完整分析摘要** (TTL=7天, 来源: 日报):")
        lines.append(ctx["daily_report"])

    # Freshness scoring summary
    stale_count = len(freshness) if freshness else 0
    if stale_count > 0:
        lines.append(f"\n⚠️ {stale_count} 类数据已超过时效阈值。请降低这些数据的参考权重。")
    else:
        lines.append("\n✅ 所有背景数据均在时效窗口内。")

    lines.append("")
    lines.append("> 背景知识仅供参考。市场瞬息万变，过时数据可能产生误导。")
    lines.append("> 优先相信当前的技术指标和因子得分，而非历史结论。")

    return "\n".join(lines)


def build_all_knowledge(stock_codes: list[str], db_path: str = DEFAULT_DB_PATH) -> dict[str, str]:
    """Build knowledge prompts for multiple stocks at once."""
    result = {}
    for code in stock_codes:
        try:
            ctx = build_stock_knowledge(code, db_path)
            result[code] = ctx.get("knowledge_prompt", "")
        except Exception as exc:
            logger.debug("[KnowledgeBase] %s failed: %s", code, exc)
            result[code] = ""
    return result
