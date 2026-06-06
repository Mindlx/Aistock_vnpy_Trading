#!/usr/bin/env python3
"""
===================================
东方财富个股评级 PDF 报告生成器
===================================

遍历全自选股，拉取东方财富评级（参与意愿 + 关注度），
生成格式化 PDF 报告并通过企业微信推送。

用法:
    cd systems/MindLynx-Aistock
    .venv/bin/python scripts/generate_rating_report.py

依赖:
    akshare, pandas, weasyprint, markdown2 (均在 .venv 中)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import akshare as ak
import pandas as pd

# ── 路径 ──────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # systems/MindLynx-Aistock
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_config, setup_env
from src.md2img import markdown_to_pdf
from src.notification_sender.wechat_sender import WechatSender

setup_env()
logger = logging.getLogger(__name__)

# ── 股票池 ────────────────────────────────────────────────
DEFAULT_STOCK_POOL = PROJECT_ROOT.parent.parent / "config" / "stock_pool.csv"

# ── 报告输出目录 ───────────────────────────────────────────
REPORTS_DIR = PROJECT_ROOT / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


# ── 评级等级判定（与 feature_bridge.py 保持同步） ───────────

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

def _combined_grade(w: float, f: float, is_st: bool = False) -> tuple[str, str]:
    """综合评级: (图标, 结论描述)"""
    if is_st:
        return "❌", "ST股风险，建议规避"
    if w >= 65 or (w >= 55 and f < 65):
        return "✅", f"意愿{_desire_level(w)}，关注{_focus_level(f)}，同步积极"
    if (w >= 55 and f >= 65) or (45 <= w < 55 and f < 65):
        return "📈", f"意愿{_desire_level(w)}，关注{_focus_level(f)}，谨慎偏多"
    if (45 <= w < 55 and f >= 65) or (35 <= w < 45 and f < 65):
        return "📉", f"意愿{_desire_level(w)}，关注{_focus_level(f)}，注意风险"
    return "❌", f"意愿{_desire_level(w)}，关注{_focus_level(f)}，危险信号"


def load_stock_pool(stock_pool_path: str | Path | None = None) -> list[dict]:
    """读取 stock_pool.csv，返回 [{code, name, market}] 列表。"""
    path = Path(stock_pool_path) if stock_pool_path else DEFAULT_STOCK_POOL
    if not path.exists():
        logger.error("股票池文件不存在: %s", path)
        sys.exit(1)

    df = pd.read_csv(path, dtype=str).fillna("")
    stocks = []
    for _, row in df.iterrows():
        stocks.append({
            "code": row["code"].strip(),
            "name": row["name"].strip(),
            "market": row.get("market", "").strip(),
        })
    logger.info("已加载 %d 只自选股", len(stocks))
    return stocks


def fetch_desire(symbol: str) -> pd.DataFrame | None:
    """拉取东方财富参与意愿数据（最新5条）。"""
    try:
        df = ak.stock_comment_detail_scrd_desire_em(symbol=symbol)
        if df is not None and not df.empty:
            return df.sort_values("交易日期", ascending=False).head(5)
    except Exception as e:
        logger.warning("[%s] 参与意愿拉取失败: %s", symbol, e)
    return None


def fetch_focus(symbol: str) -> pd.DataFrame | None:
    """拉取东方财富用户关注度数据（近20条）。"""
    try:
        df = ak.stock_comment_detail_scrd_focus_em(symbol=symbol)
        if df is not None and not df.empty:
            return df.sort_values("交易日", ascending=False).head(20)
    except Exception as e:
        logger.warning("[%s] 关注度拉取失败: %s", symbol, e)
    return None


def fmt_desire_trend(df: pd.DataFrame) -> str:
    """将参与意愿 DataFrame 格式化为紧凑描述文字。"""
    if df is None or df.empty:
        return "暂无数据"

    lines = []
    for _, row in df.iterrows():
        date = row.get("交易日期", "")
        desire = row.get("参与意愿", "")
        change = row.get("参与意愿变化", "")
        ma5 = row.get("5日平均参与意愿", "")
        ma5_chg = row.get("5日平均变化", "")

        # 趋势箭头
        arrow = "→"
        try:
            c = float(change) if change else 0
            arrow = "↑" if c > 5 else ("↓" if c < -5 else f"{'↗' if c > 0 else '↘' if c < 0 else '→'}")
        except (ValueError, TypeError):
            pass

        lines.append(
            f"| {date} | {desire} | {ma5} | {change} {arrow} | {ma5_chg} |"
        )
    return "\n".join(lines)


def fmt_focus_trend(df: pd.DataFrame) -> str:
    """将关注度 DataFrame 格式化为紧凑描述。"""
    if df is None or df.empty:
        return "暂无数据"

    # 计算趋势
    values = []
    for _, row in df.iterrows():
        try:
            values.append(float(row.get("用户关注指数", 0)))
        except (ValueError, TypeError):
            pass

    recent_5 = values[:5] if len(values) >= 5 else values
    avg_recent = sum(recent_5) / len(recent_5) if recent_5 else 0
    older_5 = values[5:10] if len(values) >= 10 else values[-5:]
    avg_older = sum(older_5) / len(older_5) if older_5 else 0
    trend_arrow = "↑" if avg_recent > avg_older else ("↓" if avg_recent < avg_older else "→")

    lines = [
        "| 日期 | 关注指数 | 5日趋势 |",
        "|------|---------|---------|",
    ]
    for i, (_, row) in enumerate(df.iterrows()):
        date = row.get("交易日", "")
        val = row.get("用户关注指数", "")
        marker = "◀" if i < 5 else ""
        lines.append(f"| {date} | {val} | {marker} |")

    summary = (
        f"近5日均值: {avg_recent:.1f} ｜ "
        f"前5日均值: {avg_older:.1f} ｜ "
        f"趋势: {trend_arrow}"
    )
    return "\n".join(lines) + f"\n\n{summary}"


def generate_report(stocks: list[dict]) -> str:
    """
    遍历所有股票生成东方财富评级报告 Markdown 文本。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# 📊 东方财富个股评级报告",
        f"",
        f"**生成时间**: {now} ｜ **股票数**: {len(stocks)} 只",
        f"**数据来源**: 东方财富 (data.eastmoney.com)",
        f"",
        f"---",
        f"",
    ]

    for i, stock in enumerate(stocks, 1):
        code = stock["code"]
        name = stock["name"]
        market = stock["market"]
        full_code = f"{code}.{'SH' if market == 'SH' else 'SZ'}"

        logger.info("[%d/%d] 正在拉取 %s(%s)...", i, len(stocks), name, code)
        lines.append(f"## {i}. {name}（{code}）")
        lines.append("")

        # ── 参与意愿 ──
        desire_df = fetch_desire(code)
        if desire_df is not None and not desire_df.empty:
            latest = desire_df.iloc[0]
            latest_val = latest.get("参与意愿", "?")
            latest_change = latest.get("参与意愿变化", 0)
            try:
                lc = float(latest_change) if latest_change else 0
                arrow = "🟢 上升" if lc > 5 else ("🔴 下降" if lc < -5 else ("🟡 微幅变动" if abs(lc) > 0 else "⚪ 持平"))
            except (ValueError, TypeError):
                arrow = ""

            ma5_val = latest.get("5日平均参与意愿", "?")
            lines.extend([
                f"### 参与意愿评分",
                f"",
                f"| 指标 | 数值 | 评估 |",
                f"|------|------|------|",
                f"| 最新参与意愿 | **{latest_val}** | {arrow} |",
                f"| 5日均值 | {ma5_val} | |",
                f"| 日变化 | {latest_change} | {'🟢 +' if lc > 0 else '🔴 ' if lc < 0 else ''}{lc if abs(lc) > 0 else 0} |",
                f"",
                f"**近5日走势**:",
                f"",
                f"| 日期 | 参与意愿 | 5日均值 | 日变化 | 5日均变化 |",
                f"|------|---------|---------|-------|----------|",
            ])
            lines.append(fmt_desire_trend(desire_df))
        else:
            lines.append("*参与意愿数据暂不可用*")

        lines.append("")

        # ── 用户关注度 ──
        focus_df = fetch_focus(code)
        if focus_df is not None and not focus_df.empty:
            lines.extend([
                f"### 用户关注度",
                f"",
                f"**近20日关注指数走势（◀ = 近5日）**:",
                f"",
            ])
            lines.append(fmt_focus_trend(focus_df))
        else:
            lines.append("*关注度数据暂不可用*")

        lines.append("")
        lines.append("---")
        lines.append("")

        # 控制请求频率，避免被封
        if i < len(stocks):
            time.sleep(1.5)

    # ── 全表汇总 ──
    lines.extend([
        f"## 📋 全表汇总",
        f"",
        f"| # | 代码 | 名称 | 最新参与意愿 | 近5日变化 | 关注度趋势 |",
        f"|---|------|------|------------|----------|-----------|",
    ])

    # 重新拉取一遍汇总数据（轻量展示）
    for i, stock in enumerate(stocks, 1):
        code = stock["code"]
        name = stock["name"]
        try:
            df = fetch_desire(code)
            if df is not None and not df.empty:
                latest = df.iloc[0]
                desire_val = latest.get("参与意愿", "?")
                try:
                    total_change = sum(
                        float(row.get("参与意愿变化", 0) or 0) for _, row in df.iterrows()
                    )
                    chg_str = f"{total_change:+.1f}"
                except (ValueError, TypeError):
                    chg_str = "?"
                desire_str = f"{desire_val}"
            else:
                desire_str = "N/A"
                chg_str = "N/A"
        except Exception:
            desire_str = "N/A"
            chg_str = "N/A"

        try:
            focus_df = fetch_focus(code)
            if focus_df is not None and not focus_df.empty:
                vals = []
                for _, row in focus_df.iterrows():
                    try:
                        vals.append(float(row.get("用户关注指数", 0)))
                    except (ValueError, TypeError):
                        pass
                recent_5 = vals[:5] if len(vals) >= 5 else vals
                older_5 = vals[5:10] if len(vals) >= 10 else vals[-5:]
                avg_r = sum(recent_5) / len(recent_5) if recent_5 else 0
                avg_o = sum(older_5) / len(older_5) if older_5 else 0
                trend = "↑" if avg_r > avg_o else ("↓" if avg_r < avg_o else "→")
            else:
                trend = "?"
        except Exception:
            trend = "?"

        lines.append(f"| {i} | {code} | {name} | {desire_str} | {chg_str} | {trend} |")

        if i < len(stocks):
            time.sleep(0.5)

    lines.extend([
        "",
        "---",
        "",
        "> ⚠️ 本报告仅供学习研究参考，不构成任何投资建议。",
        f"> 生成时间: {now}",
    ])

    return "\n".join(lines)


def generate_summary_rows(stocks: list[dict]) -> list[dict]:
    """生成汇总行数据（供 run_daily.py 做简短微信通知时使用）。

    Returns:
        [{code, name, desire_val, desire_change, focus_trend, icon, conclusion}, ...]
    """
    rows = []
    for i, stock in enumerate(stocks):
        code = stock["code"]
        name = stock["name"]
        desire_val = focus_val = None
        desire_change_str = "?"
        focus_trend = "?"

        try:
            df = fetch_desire(code)
            if df is not None and not df.empty:
                latest = df.iloc[0]
                desire_val = float(latest.get("参与意愿", 0) or 0)
                try:
                    total_chg = sum(
                        float(row.get("参与意愿变化", 0) or 0) for _, row in df.iterrows()
                    )
                    desire_change_str = f"{total_chg:+.1f}"
                except (ValueError, TypeError):
                    desire_change_str = "?"
        except Exception:
            pass

        try:
            focus_df = fetch_focus(code)
            if focus_df is not None and not focus_df.empty:
                vals = []
                for _, row in focus_df.iterrows():
                    try:
                        vals.append(float(row.get("用户关注指数", 0)))
                    except (ValueError, TypeError):
                        pass
                recent_5 = vals[:5] if len(vals) >= 5 else vals
                older_5 = vals[5:10] if len(vals) >= 10 else vals[-5:]
                avg_r = sum(recent_5) / len(recent_5) if recent_5 else 0
                avg_o = sum(older_5) / len(older_5) if older_5 else 0
                focus_val = avg_r
                focus_trend = "↑" if avg_r > avg_o else ("↓" if avg_r < avg_o else "→")
        except Exception:
            pass

        w = desire_val or 50
        f = focus_val or 50
        is_st = name.startswith("*ST")
        icon, conclusion = _combined_grade(w, f, is_st)

        rows.append({
            "code": code,
            "name": name,
            "desire_val": f"{desire_val:.2f}" if desire_val else "--",
            "desire_change": desire_change_str,
            "focus_trend": focus_trend,
            "icon": icon,
            "conclusion": conclusion,
        })

        if i < len(stocks) - 1:
            time.sleep(0.5)

    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="东方财富个股评级 PDF 报告生成器",
    )
    parser.add_argument(
        "--stock-pool", type=str, default=None,
        help="股票池 CSV 路径（默认 config/stock_pool.csv）",
    )
    parser.add_argument(
        "--no-push", action="store_true",
        help="仅生成 PDF，不推送企业微信",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    now = datetime.now()
    date_str = now.strftime("%Y%m%d")

    logger.info("=" * 60)
    logger.info("东方财富个股评级报告生成器 启动")
    logger.info("时间: %s", now.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 60)

    # 1. 读取股票池
    stocks = load_stock_pool(args.stock_pool)
    logger.info("共 %d 只自选股", len(stocks))

    # 2. 生成 Markdown 报告
    logger.info("正在获取评级数据并生成报告...")
    md_content = generate_report(stocks)

    # 3. 保存 Markdown 到文件（备用）
    md_path = REPORTS_DIR / f"eastmoney_rating_{date_str}.md"
    md_path.write_text(md_content, encoding="utf-8")
    logger.info("Markdown 报告已保存: %s", md_path)

    # 4. 转换为 PDF
    logger.info("正在转换为 PDF...")
    pdf_data = markdown_to_pdf(md_content, font_size="14pt")
    if not pdf_data:
        logger.error("PDF 生成失败，请检查 weasyprint 安装")
        logger.info("Markdown 内容预览:\n%s", md_content[:500])
        sys.exit(1)

    pdf_filename = f"{date_str}东方财富评级报告.pdf"
    pdf_path = REPORTS_DIR / pdf_filename
    pdf_path.write_bytes(pdf_data)
    logger.info("PDF 已保存: %s (%d bytes)", pdf_path, len(pdf_data))

    # 5. 推送企业微信（除非 --no-push）
    if args.no_push:
        logger.info("--no-push 指定，跳过企业微信推送")
        logger.info("PDF 已保存至: %s", pdf_path)
    else:
        logger.info("正在推送至企业微信...")
        config = get_config()
        wechat_url = getattr(config, "wechat_webhook_url", None)
        if not wechat_url:
            logger.error("企业微信 Webhook 未配置")
            sys.exit(1)

        sender = WechatSender(config)
        ok = sender.send_to_wechat_file(pdf_data, pdf_filename)
        if ok:
            logger.info("✅ 评级报告已成功推送到企业微信: %s", pdf_filename)
        else:
            logger.error("❌ 企业微信推送失败")
            sys.exit(1)

    logger.info("=" * 60)
    logger.info("完成")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
