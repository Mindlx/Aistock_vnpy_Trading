# services/eastmoney/fetcher.py
"""
东方财富数据获取器（services/eastmoney/ 规范路径）

用法:
    .venv/bin/python services/eastmoney/fetcher.py
    .venv/bin/python services/eastmoney/fetcher.py --no-push

输出:
    data/realtime/eastmoney_rating.json — 自选股缓存
    data/research/eastmoney_snapshot/ — 全市场日频快照
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd
import requests

# ── 路径解析 ──
# services/eastmoney/fetcher.py → up 3 levels → project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 自包含：不依赖任何子系统模块。使用 os.getenv + requests.post
_WEBHOOK_URL = os.getenv("WECOM_WEBHOOK_URL") or os.getenv("WECHAT_WEBHOOK_URL")

# 如果没有环境变量（手动运行），尝试从根 .env 加载
if not _WEBHOOK_URL:
    _root_env = _PROJECT_ROOT / ".env"
    if _root_env.exists():
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=_root_env)
        _WEBHOOK_URL = os.getenv("WECOM_WEBHOOK_URL") or os.getenv("WECHAT_WEBHOOK_URL")

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")

# ── 路径常量 ──
STOCK_POOL_PATH = _PROJECT_ROOT / "config" / "stock_pool.csv"
CACHE_PATH = _PROJECT_ROOT / "data" / "realtime" / "eastmoney_rating.json"
RESEARCH_DIR = _PROJECT_ROOT / "data" / "research" / "eastmoney_snapshot"
RESEARCH_DIR.mkdir(parents=True, exist_ok=True)


# ── 全市场快照存档 ──

def archive_market_snapshot(df: pd.DataFrame) -> None:
    """将全市场快照存档（含派生字段），供日后回测研究。"""
    try:
        today = datetime.now().strftime("%Y%m%d")
        csv_path = RESEARCH_DIR / f"snapshot_{today}.csv"
        if csv_path.exists():
            return

        df = df.copy()
        cost_col, price_col = "主力成本", "最新价"
        score_col, focus_col = "综合得分", "关注指数"

        if cost_col in df.columns and price_col in df.columns:
            cost = pd.to_numeric(df[cost_col], errors="coerce")
            price = pd.to_numeric(df[price_col], errors="coerce")
            df["cost_deviation_pct"] = ((price - cost) / cost * 100).round(2)

        if score_col in df.columns and focus_col in df.columns:
            s = pd.to_numeric(df[score_col], errors="coerce").fillna(50)
            f = pd.to_numeric(df[focus_col], errors="coerce").fillna(50)
            df["score_x_focus"] = (s * f / 100).round(1)

        try:
            if STOCK_POOL_PATH.exists():
                pool_df = pd.read_csv(STOCK_POOL_PATH, dtype=str)
                pool_codes = set(pool_df["code"].str.strip())
                df["is_our_stock"] = df["代码"].isin(pool_codes)
        except Exception:
            df["is_our_stock"] = False

        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        logger.info("全市场快照已存档: %s (%d 只股票, %d 列)", csv_path, len(df), len(df.columns))

        summary = {
            "date": today, "total_stocks": len(df),
            "our_stocks": int(df["is_our_stock"].sum()) if "is_our_stock" in df else 0,
            "fields": list(df.columns),
            "focus_avg": round(float(pd.to_numeric(df[focus_col], errors="coerce").mean()), 1),
            "score_avg": round(float(pd.to_numeric(df[score_col], errors="coerce").mean()), 1),
            "cost_deviation_avg": round(float(df["cost_deviation_pct"].mean()), 2) if "cost_deviation_pct" in df else None,
        }
        summary_path = RESEARCH_DIR / f"snapshot_{today}_summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("全市场快照存档失败: %s", e)


# ── 辅助函数 ──

def _session_label() -> str:
    return "早盘" if datetime.now().hour < 12 else "午盘"


def load_stock_pool(path: Path) -> list[dict[str, str]]:
    df = pd.read_csv(path, dtype=str).fillna("")
    return [{"code": row["code"].strip(), "name": row["name"].strip(), "market": row.get("market", "").strip()}
            for _, row in df.iterrows()]


def fetch_desire(symbol: str) -> pd.DataFrame | None:
    try:
        df = ak.stock_comment_detail_scrd_desire_em(symbol=symbol)
        if df is not None and not df.empty:
            return df.sort_values("交易日期", ascending=False).head(1)
    except Exception as e:
        logger.warning("[%s] 参与意愿拉取失败: %s", symbol, e)
    return None


def fetch_focus(symbol: str) -> pd.DataFrame | None:
    try:
        df = ak.stock_comment_detail_scrd_focus_em(symbol=symbol)
        if df is not None and not df.empty:
            return df.sort_values("交易日", ascending=False).head(5)
    except Exception as e:
        logger.warning("[%s] 关注度拉取失败: %s", symbol, e)
    return None


def fetch_market_snapshot(df=None):
    if df is None:
        try:
            df = ak.stock_comment_em()
        except Exception as e:
            logger.warning("全市场快照拉取失败: %s", e)
            return {}
    if df is None or df.empty:
        return {}
    focus = pd.to_numeric(df["关注指数"], errors="coerce")
    score = pd.to_numeric(df["综合得分"], errors="coerce")
    inst = pd.to_numeric(df["机构参与度"], errors="coerce")
    return {"total_stocks": len(df), "focus_avg": round(float(focus.mean()), 1),
            "focus_median": round(float(focus.median()), 1),
            "score_avg": round(float(score.mean()), 1),
            "score_median": round(float(score.median()), 1),
            "institution_avg": round(float(inst.mean()), 4)}


def fetch_market_stock_map(df=None):
    if df is None:
        try:
            df = ak.stock_comment_em()
        except Exception as e:
            logger.warning("全市场个股数据获取失败: %s", e)
            return {}
    if df is None or df.empty:
        return {}
    result = {}
    for _, row in df.iterrows():
        code = str(row.get("代码", "")).strip()
        score = pd.to_numeric(row.get("综合得分"), errors="coerce")
        inst = pd.to_numeric(row.get("机构参与度"), errors="coerce")
        if code:
            entry = {}
            if not pd.isna(score):
                entry["score"] = round(float(score), 1)
            if not pd.isna(inst):
                entry["institution"] = round(float(inst), 4)
            if entry:
                result[code] = entry
    return result


def _calc_focus_trend(df: pd.DataFrame) -> tuple[float, str]:
    values = []
    for _, row in df.iterrows():
        try:
            values.append(float(row.get("用户关注指数", 0)))
        except (ValueError, TypeError):
            pass
    if not values:
        return 0.0, "?"
    avg = sum(values[:5]) / len(values[:5])
    if len(values) >= 2:
        trend = "↑" if values[0] > values[-1] else ("↓" if values[0] < values[-1] else "→")
    else:
        trend = "→"
    return round(avg, 1), trend


def _desire_level(val: float) -> str:
    if val >= 80: return "强烈做多"
    if val >= 65: return "做多强劲"
    if val >= 55: return "偏多活跃"
    if val >= 45: return "多空均衡"
    if val >= 35: return "偏空观望"
    if val >= 20: return "做空明显"
    return "极度悲观"


def _focus_level(val: float) -> str:
    if val >= 80: return "极度拥挤"
    if val >= 65: return "非常活跃"
    if val >= 55: return "较为活跃"
    if val >= 45: return "热度正常"
    if val >= 35: return "偏冷清"
    if val >= 20: return "冷门"
    return "极度冷清"


# ⚠️ 以下阈值分类仅为简讯推送展示用，非LLM注入路径。
# 354条w/f校准发现参与意愿与T+1涨跌幅无显著正相关，暂保留观察。

def _conclusion_short(w: float, f: float) -> str:
    if w >= 80: return "强烈做多"
    if w >= 55: return "谨慎偏多" if f >= 65 else "做多良好"
    if w >= 45:
        if f >= 80: return "防范回调"
        if f >= 65: return "风险较大"
        if f >= 55: return "观望为主"
        return "等待确认"
    if w >= 20: return "抛压加剧" if f >= 65 else "减仓为主"
    return "坚决离场"


def _combined_grade(w: float, f: float, is_st: bool = False) -> tuple[str, str]:
    if is_st:
        return "❌", "ST股风险，建议规避"
    if w >= 80: return "✅", f"{_desire_level(w)} {_focus_level(f)} {_conclusion_short(w, f)}"
    if w >= 55: return "📈", f"{_desire_level(w)} {_focus_level(f)} {_conclusion_short(w, f)}"
    if w >= 45: return "💤", f"{_desire_level(w)} {_focus_level(f)} {_conclusion_short(w, f)}"
    if w >= 20: return "📉", f"{_desire_level(w)} {_focus_level(f)} {_conclusion_short(w, f)}"
    return "❌", f"{_desire_level(w)} {_focus_level(f)} {_conclusion_short(w, f)}"


# ── 主流程 ──

def fetch_all(stocks: list[dict]) -> dict[str, Any] | None:
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    entries: dict[str, dict] = {}
    total = len(stocks)

    for i, stock in enumerate(stocks, 1):
        code, name = stock["code"], stock["name"]
        is_st = name.startswith("*ST")
        logger.info("[%d/%d] %s(%s)...", i, total, name, code)

        desire_val = desire_change = desire_str = None
        focus_avg = focus_trend = None

        ddf = fetch_desire(code)
        if ddf is not None and not ddf.empty:
            row = ddf.iloc[0]
            try:
                desire_val = round(float(row.get("参与意愿", 0) or 0), 1)
                desire_change = round(float(row.get("参与意愿变化", 0) or 0), 1)
                desire_str = _desire_level(desire_val)
            except (ValueError, TypeError):
                pass

        fdf = fetch_focus(code)
        if fdf is not None and not fdf.empty:
            focus_avg, focus_trend = _calc_focus_trend(fdf)

        w = desire_val or 50
        f_avg = focus_avg or 50
        _combined_grade(w, f_avg, is_st)

        entries[code] = {"name": name, "desire": desire_val, "desire_level": desire_str,
                         "desire_change": desire_change, "focus_avg": focus_avg, "focus_trend": focus_trend}
        if i < total:
            time.sleep(1.5)

    _market_df = ak.stock_comment_em()

    result = {"fetched_at": fetched_at, "market": fetch_market_snapshot(_market_df), "stocks": entries}

    try:
        market_map = fetch_market_stock_map(_market_df)
        for code, entry in result["stocks"].items():
            extra = market_map.get(code, {})
            if extra.get("institution") is not None:
                entry["institution"] = extra["institution"]
            if extra.get("score") is not None:
                entry["score"] = extra["score"]
    except Exception as e:
        logger.warning("市场补充数据注入失败: %s", e)

    success_count = sum(1 for e in entries.values() if e["desire"] is not None or e["focus_avg"] is not None)
    if success_count == 0:
        logger.warning("所有股票数据获取失败，不更新缓存")
        return None

    try:
        if _market_df is not None and not _market_df.empty:
            archive_market_snapshot(_market_df)
    except Exception as e:
        logger.warning("市场快照存档失败: %s", e)

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("已写入缓存: %s (%d/%d 只成功)", CACHE_PATH, success_count, total)
    return result


def _generate_brief_text(result: dict, session: str) -> str:
    now = datetime.now().strftime("%H:%M")
    stocks_data = result["stocks"]

    # 构建带图标和结论的行（与 generate_rating_report.py 格式一致）
    _ICON_ORDER = {"✅": 0, "📈": 1, "💤": 2, "📉": 3, "❌": 4}
    rows = []
    for code, data in stocks_data.items():
        name = data["name"]
        w = float(data.get("desire") or 50)
        f = float(data.get("focus_avg") or 50)
        is_st = name.startswith("*ST")
        icon, _conclusion = _combined_grade(w, f, is_st)
        dl = _desire_level(w)
        fl = _focus_level(f)
        cs = _conclusion_short(w, f)
        short_line = cs
        rows.append({
            "icon": icon,
            "name": name,
            "desire_val": f"{w:.1f}",
            "focus_val": f"{f:.1f}",
            "short_line": short_line,
        })

    rows.sort(key=lambda r: _ICON_ORDER.get(r["icon"], 99))
    lines = [f"💰 {now} 东方财富评级"]
    for r in rows:
        dv = int(float(r['desire_val'])) if float(r['desire_val']) > 0 else 0
        fv = int(float(r['focus_val'])) if float(r['focus_val']) > 0 else 0
        if dv > 0 and fv > 0:
            lines.append(f"{r['icon']} **{r['name']}**{dv}/{fv}｜{r['short_line']}")
        else:
            lines.append(f"{r['icon']} **{r['name']}**｜{r['short_line']}")

    return "\n".join(lines)


def push_brief(result: dict, session: str) -> bool:
    """推送简讯到企业微信（requests.post，不依赖 ML 子系统）。"""
    if not _WEBHOOK_URL:
        logger.warning("微信 Webhook 未配置，跳过推送")
        return False
    try:
        text = _generate_brief_text(result, session)
        resp = requests.post(_WEBHOOK_URL,
                             json={"msgtype": "markdown", "markdown": {"content": text}},
                             timeout=10)
        if resp.status_code == 200 and resp.json().get("errcode") == 0:
            logger.info("简讯已推送（%s）", session)
            return True
        logger.warning("简讯推送失败: %s", resp.text[:200])
        return False
    except Exception as e:
        logger.warning("简讯推送异常: %s", e)
        return False


def main():
    parser = argparse.ArgumentParser(description="东方财富数据获取器")
    parser.add_argument("--no-push", action="store_true", help="仅缓存，不推送微信")
    args = parser.parse_args()

    if not STOCK_POOL_PATH.exists():
        logger.error("股票池文件不存在: %s", STOCK_POOL_PATH)
        sys.exit(1)

    stocks = load_stock_pool(STOCK_POOL_PATH)
    logger.info("已加载 %d 只自选股", len(stocks))

    result = fetch_all(stocks)
    if not result:
        sys.exit(1)

    if not args.no_push:
        push_brief(result, _session_label())

    for code, data in sorted(result["stocks"].items()):
        logger.info("  %s(%s)  desire=%s  focus=%s", data["name"], code,
                    data.get("desire", "--"), data.get("focus_avg", "--"))


if __name__ == "__main__":
    main()
