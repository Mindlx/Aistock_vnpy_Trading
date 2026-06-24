#!/usr/bin/env python3
"""
东方财富评级数据获取器。

读取 stock_pool.csv → akshare拉取参与意愿+关注度
→ 写入 data/realtime/eastmoney_rating.json 供LLM下游消费
→ 推送简讯到个人微信。

用法:
    .venv/bin/python scripts/fetch_eastmoney_rating.py
    .venv/bin/python scripts/fetch_eastmoney_rating.py --no-push  # 仅缓存，不推送

输出:
    data/realtime/eastmoney_rating.json
    仅在数据获取成功时写入（API失败则保留上次缓存）。
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import akshare as ak
import argparse
import pandas as pd

# ── 路径 ──────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # systems/MindLynx-Aistock
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import setup_env

setup_env()
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)

# ── 股票池 ────────────────────────────────────────────────
STOCK_POOL_PATH = PROJECT_ROOT.parent.parent / "config" / "stock_pool.csv"

# ── 缓存输出 ──────────────────────────────────────────────
CACHE_PATH = PROJECT_ROOT.parent.parent / "data" / "realtime" / "eastmoney_rating.json"


def _session_label() -> str:
    h = datetime.now().hour
    return "早盘" if h < 12 else "午盘"


def load_stock_pool(path: Path) -> list[dict[str, str]]:
    """读取 stock_pool.csv，返回 [{code, name, market}]。"""
    df = pd.read_csv(path, dtype=str).fillna("")
    return [
        {"code": row["code"].strip(), "name": row["name"].strip(), "market": row.get("market", "").strip()}
        for _, row in df.iterrows()
    ]


def fetch_desire(symbol: str) -> pd.DataFrame | None:
    """拉取东方财富参与意愿数据（最新一条）。"""
    try:
        df = ak.stock_comment_detail_scrd_desire_em(symbol=symbol)
        if df is not None and not df.empty:
            return df.sort_values("交易日期", ascending=False).head(1)
    except Exception as e:
        logger.warning("[%s] 参与意愿拉取失败: %s", symbol, e)
    return None


def fetch_focus(symbol: str) -> pd.DataFrame | None:
    """拉取东方财富用户关注度数据（最近5条用于趋势判断）。"""
    try:
        df = ak.stock_comment_detail_scrd_focus_em(symbol=symbol)
        if df is not None and not df.empty:
            return df.sort_values("交易日", ascending=False).head(5)
    except Exception as e:
        logger.warning("[%s] 关注度拉取失败: %s", symbol, e)
    return None


def fetch_market_snapshot() -> dict:
    """拉取东方财富全市场快照（5186只），返回市场级统计+自选股机构数据。"""
    try:
        df = ak.stock_comment_em()
        if df is None or df.empty:
            return {}
        import pandas as pd
        focus = pd.to_numeric(df["关注指数"], errors="coerce")
        score = pd.to_numeric(df["综合得分"], errors="coerce")
        inst = pd.to_numeric(df["机构参与度"], errors="coerce")
        return {
            "total_stocks": len(df),
            "focus_avg": round(float(focus.mean()), 1),
            "focus_median": round(float(focus.median()), 1),
            "score_avg": round(float(score.mean()), 1),
            "score_median": round(float(score.median()), 1),
            "institution_avg": round(float(inst.mean()), 4),
        }
    except Exception as e:
        logger.warning("全市场快照拉取失败: %s", e)
        return {}


def fetch_market_stock_map() -> dict[str, dict]:
    """拉取全市场快照，提取每只股票的综合得分和机构参与度。"""
    try:
        df = ak.stock_comment_em()
        if df is None or df.empty:
            return {}
        import pandas as pd
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
        logger.info("全市场个股数据已获取: %d 只", len(result))
        return result
    except Exception as e:
        logger.warning("全市场个股数据获取失败: %s", e)
        return {}


def _calc_focus_trend(df: pd.DataFrame) -> tuple[float, str]:
    """计算近5日关注度均值及趋势方向。"""
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


def _conclusion_short(w: float, f: float) -> str:
    if w >= 80:
        return "强烈做多"
    if w >= 55:
        return "谨慎偏多" if f >= 65 else "做多良好"
    if w >= 45:
        if f >= 80: return "防范回调"
        if f >= 65: return "风险较大"
        if f >= 55: return "观望为主"
        return "等待确认"
    if w >= 20:
        return "抛压加剧" if f >= 65 else "减仓为主"
    return "坚决离场"


def _combined_grade(w: float, f: float, is_st: bool = False) -> tuple[str, str]:
    """综合评级: (图标, 结论文字)"""
    if is_st:
        return "❌", "ST股风险，建议规避"
    if w >= 80: return "✅", f"{_desire_level(w)} {_focus_level(f)} {_conclusion_short(w, f)}"
    if w >= 55: return "📈", f"{_desire_level(w)} {_focus_level(f)} {_conclusion_short(w, f)}"
    if w >= 45: return "💤", f"{_desire_level(w)} {_focus_level(f)} {_conclusion_short(w, f)}"
    if w >= 20: return "📉", f"{_desire_level(w)} {_focus_level(f)} {_conclusion_short(w, f)}"
    return "❌", f"{_desire_level(w)} {_focus_level(f)} {_conclusion_short(w, f)}"


def fetch_all(stocks: list[dict]) -> dict[str, Any] | None:
    """遍历所有股票，组装缓存数据。"""
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    entries: dict[str, dict] = {}

    total = len(stocks)
    for i, stock in enumerate(stocks, 1):
        code = stock["code"]
        name = stock["name"]
        is_st = name.startswith("*ST")

        logger.info("[%d/%d] %s(%s)...", i, total, name, code)

        desire_val: float | None = None
        desire_change: float | None = None
        desire_str: str | None = None
        focus_avg: float | None = None
        focus_trend: str | None = None

        # 参与意愿
        ddf = fetch_desire(code)
        if ddf is not None and not ddf.empty:
            row = ddf.iloc[0]
            try:
                desire_val = round(float(row.get("参与意愿", 0) or 0), 1)
                desire_change = round(float(row.get("参与意愿变化", 0) or 0), 1)
                desire_str = _desire_level(desire_val)
            except (ValueError, TypeError):
                pass

        # 关注度
        fdf = fetch_focus(code)
        if fdf is not None and not fdf.empty:
            focus_avg, focus_trend = _calc_focus_trend(fdf)

        # 综合评级
        w = desire_val or 50
        f_avg = focus_avg or 50
        _combined_grade(w, f_avg, is_st)  # keep for logging, results stored in cache metadata

        entries[code] = {
            "name": name,
            "desire": desire_val,
            "desire_level": desire_str,
            "desire_change": desire_change,
            "focus_avg": focus_avg,
            "focus_trend": focus_trend,
        }

        if i < total:
            time.sleep(1.5)

    result = {
        "fetched_at": fetched_at,
        "market": fetch_market_snapshot(),
        "stocks": entries,
    }

    # 从全市场快照补充机构参与度和综合得分
    try:
        market_map = fetch_market_stock_map()
        for code, entry in result["stocks"].items():
            extra = market_map.get(code, {})
            if extra.get("institution") is not None:
                entry["institution"] = extra["institution"]
            if extra.get("score") is not None:
                entry["score"] = extra["score"]
    except Exception:
        pass

    # 成功获取到数据才写入缓存
    success_count = sum(1 for e in entries.values() if e["desire"] is not None or e["focus_avg"] is not None)
    if success_count == 0:
        logger.warning("所有股票数据获取失败，不更新缓存")
        return None

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("已写入缓存: %s (%d/%d 只成功)", CACHE_PATH, success_count, total)
    return result


def _generate_brief_text(result: dict, session: str) -> str:
    """生成微信简讯（紧凑格式）。"""
    now = datetime.now().strftime("%H:%M")
    stocks_data = result["stocks"]
    lines = [f"💰 {now} 东方财富参与意愿（{session}）"]

    # 按意愿值降序排列
    sorted_codes = sorted(stocks_data.keys(),
                          key=lambda c: stocks_data[c].get("desire") or 0, reverse=True)

    for code in sorted_codes:
        data = stocks_data[code]
        name = data["name"]
        d = data.get("desire", "--")
        f = data.get("focus_avg", "--")
        lines.append(f"{name}｜意愿{d} 关注{f}")

    return "\n".join(lines)


def push_brief(result: dict, session: str) -> bool:
    """推送简讯到个人微信。"""
    try:
        from src.notification_sender.wechat_sender import WechatSender
        from src.config import get_config
        config = get_config()
        if not getattr(config, "wechat_webhook_url", None):
            logger.warning("微信 Webhook 未配置，跳过推送")
            return False
        text = _generate_brief_text(result, session)
        sender = WechatSender(config)
        ok = sender.send_to_wechat(text)
        if ok:
            logger.info("简讯已推送（%s）", session)
        return ok
    except Exception as e:
        logger.warning("简讯推送失败: %s", e)
        return False


def main():
    parser = argparse.ArgumentParser(description="东方财富评级数据获取器")
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

    # 日志摘要
    stocks_data = result["stocks"]
    for code, data in sorted(stocks_data.items()):
        d = data.get("desire", "--")
        f = data.get("focus_avg", "--")
        icon = data.get("icon", "?")
        logger.info("  %s %s(%s)  desire=%s  focus=%s", icon, data["name"], code, d, f)


if __name__ == "__main__":
    main()
