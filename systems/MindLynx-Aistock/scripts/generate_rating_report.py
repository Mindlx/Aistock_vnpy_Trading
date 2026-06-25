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
FUSION_ROOT = PROJECT_ROOT.parent.parent  # Aistock_vnpy_Trading
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

def _conclusion_short(w: float, f: float) -> str:
    """统一结论文字（仅结论，不含意愿/关注前缀）。"""
    if w >= 80:
        return "强烈做多"
    if w >= 55:
        if f >= 65:
            return "谨慎偏多"
        return "做多良好"
    if w >= 45:
        if f >= 80:
            return "防范回调"
        if f >= 65:
            return "风险较大"
        if f >= 55:
            return "观望为主"
        return "等待确认"
    if w >= 20:
        if f >= 65:
            return "抛压加剧"
        return "减仓为主"
    return "坚决离场"

def _combined_grade(w: float, f: float, is_st: bool = False) -> tuple[str, str]:
    """
    综合评级: (图标) — 结论文字统一由意愿+关注+结论模板生成。

    分级依据 — 参与意愿 w 反映多空倾向，关注指数 f 反映拥挤程度：
      ✅ +3 强烈做多  w≥80
      📈 +2/+1 做多  55≤w<80
      💤  0 中性     45≤w<55
      📉 -2/-1 看空  20≤w<45
      ❌ -3 强烈看空  w<20
    """
    if is_st:
        return "❌", "ST股风险，建议规避"
    if w >= 80:
        return "✅", f"意愿{_desire_level(w)} 关注{_focus_level(f)} {_conclusion_short(w,f)}"
    if w >= 55:
        return "📈", f"意愿{_desire_level(w)} 关注{_focus_level(f)} {_conclusion_short(w,f)}"
    if w >= 45:
        return "💤", f"意愿{_desire_level(w)} 关注{_focus_level(f)} {_conclusion_short(w,f)}"
    if w >= 20:
        return "📉", f"意愿{_desire_level(w)} 关注{_focus_level(f)} {_conclusion_short(w,f)}"
    return "❌", f"意愿{_desire_level(w)} 关注{_focus_level(f)} {_conclusion_short(w,f)}"


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


def _load_cache() -> dict:
    """读取 services/eastmoney 写入的共享缓存（含机构/得分/主力成本等）。"""
    cache_path = FUSION_ROOT / "data" / "realtime" / "eastmoney_rating.json"
    if not cache_path.exists():
        return {}
    try:
        import json
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("共享缓存读取失败: %s (不影响主要功能)", e)
        return {}


def generate_report(stocks: list[dict]) -> str:
    """
    生成东方财富评级报告 Markdown（紧凑格式）。
    每只股票浓缩为 1-2 行，一览表为核心输出。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cache = _load_cache()
    market = cache.get("market", {}) if cache else {}
    stock_cache = cache.get("stocks", {}) if cache else {}

    lines = [
        f"# 📊 东方财富个股评级报告",
        f"",
        f"**生成时间**: {now} ｜ **自选股**: {len(stocks)} 只",
        f"",
    ]

    # ── 市场概况（来自共享缓存） ──
    if market and market.get("total_stocks"):
        lines.extend([
            f"### 市场概况",
        ])
        parts = [f"覆盖 {market['total_stocks']} 只 A 股"]
        if market.get("focus_avg"): parts.append(f"关注均值 {market['focus_avg']}")
        if market.get("score_avg"): parts.append(f"综合得分均值 {market['score_avg']}")
        if market.get("institution_avg"): parts.append(f"机构参与度均值 {market['institution_avg']:.4f}")
        lines.append("｜".join(parts) + "")
        lines.append("")

    # ── 单只股票紧凑行 ──
    for i, stock in enumerate(stocks, 1):
        code = stock["code"]
        name = stock["name"]

        lines.append(f"### {i}. {name}（{code}）")

        # 基础数据（每只都要拉）
        desire_df = fetch_desire(code)
        focus_df = fetch_focus(code)

        # 基础指标行
        metrics = []

        # 参与意愿
        if desire_df is not None and not desire_df.empty:
            latest = desire_df.iloc[0]
            dv = latest.get("参与意愿", "?")
            dc = latest.get("参与意愿变化", 0)
            try:
                dc_f = float(dc) if dc else 0
                arrow = "🟢" if dc_f > 5 else ("🔴" if dc_f < -5 else "🟡")
            except (ValueError, TypeError):
                dc_f = 0
                arrow = ""
            metrics.append(f"意愿 {dv} {arrow}{dc_f:+.0f}")

        # 关注度
        if focus_df is not None and not focus_df.empty:
            vals = []
            for _, r in focus_df.iterrows():
                try: vals.append(float(r.get("用户关注指数", 0)))
                except: pass
            if vals:
                avg_r = sum(vals[:5]) / min(len(vals[:5]), 1)
                avg_o = sum(vals[5:10]) / min(len(vals[5:10]), 1) if len(vals) >= 10 else avg_r
                trend = "↑" if avg_r > avg_o else ("↓" if avg_r < avg_o else "→")
                metrics.append(f"关注 {avg_r:.0f} {trend}")

        # 共享缓存数据
        sc = stock_cache.get(code, {})
        _score = sc.get("score")
        if _score is not None:
            metrics.append(f"综合 {_score}")
        _inst = sc.get("institution")
        if _inst is not None:
            metrics.append(f"机构 {_inst:.4f}")

        if metrics:
            lines.append("  " + "  |  ".join(metrics))
        else:
            lines.append("  *数据暂不可用*")

        lines.append("")

    # ── 一览表 ──
    lines.extend([
        f"## 一览表",
        f"",
        f"| # | 名称 | 意愿 | 意愿变化 | 关注 | 机构参与度 | 综合得分 | 排名 | 关注趋势 |",
        f"|---|------|:----:|:--------:|:----:|:----------:|:--------:|:----:|:--------:|",
    ])

    for i, stock in enumerate(stocks, 1):
        code = stock["code"]
        name = stock["name"]
        sc = stock_cache.get(code, {})

        # 意愿
        d_val, d_chg = "—", "—"
        try:
            ddf = fetch_desire(code)
            if ddf is not None and not ddf.empty:
                r = ddf.iloc[0]
                d_val = str(r.get("参与意愿", "—"))
                try:
                    d_chg = f"{float(r.get('参与意愿变化', 0) or 0):+.0f}"
                except: pass
        except: pass

        # 关注趋势
        focus_trend = "—"
        try:
            fdf = fetch_focus(code)
            if fdf is not None and not fdf.empty:
                vals = []
                for _, r in fdf.iterrows():
                    try: vals.append(float(r.get("用户关注指数", 0)))
                    except: pass
                if vals:
                    r5 = sum(vals[:5]) / min(len(vals[:5]), 1)
                    o5 = sum(vals[5:10]) / min(len(vals[5:10]), 1) if len(vals) >= 10 else r5
                    focus_trend = "↑" if r5 > o5 else ("↓" if r5 < o5 else "→")
        except: pass

        score = sc.get("score", "—")
        inst = sc.get("institution", "—")
        rank = sc.get("rank", "—")
        if inst != "—":
            inst = f"{inst:.4f}"

        lines.append(
            f"| {i} | {name} | {d_val} | {d_chg} | — | {inst} | {score} | {rank} | {focus_trend} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "> 数据来源: 东方财富(data.eastmoney.com) ｜ 仅供参考，不构成投资建议",
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
        short_line = _conclusion_short(w, f)

        rows.append({
            "code": code,
            "name": name,
            "desire_val": f"{desire_val:.2f}" if desire_val else "--",
            "focus_val": f"{focus_val:.1f}" if focus_val else "--",
            "desire_change": desire_change_str,
            "focus_trend": focus_trend,
            "icon": icon,
            "conclusion": conclusion,
            "short_line": short_line,
        })

        if i < len(stocks) - 1:
            time.sleep(0.5)

    return rows


def generate_brief_text(rows: list[dict]) -> str:
    """生成简短微信推送文本（简讯，不带PDF附件指引）。"""
    now = datetime.now().strftime("%H:%M")
    lines = [f"💰 {now} 东方财富评级"]

    # 按图标排序: ✅→📈→💤→📉→❌
    _ICON_ORDER = {"✅": 0, "📈": 1, "💤": 2, "📉": 3, "❌": 4}
    rows = sorted(rows, key=lambda r: _ICON_ORDER.get(r.get("icon", ""), 99))

    for r in rows:
        icon = r["icon"]
        name = r["name"]
        dv = r.get("desire_val", "--")
        fv = r.get("focus_val", "--")
        short_line = r.get("short_line", "")
        lines.append(f"{icon} **{name}**｜{dv}/{fv}｜{short_line}")

    lines.append("")
    lines.append("📎 详情见PDF报告")

    return "\n".join(lines)


def save_wf_full_history(stocks: list[dict], log_path: Path) -> int:
    """
    保存参与意愿和关注指数的完整历史（含5日趋势）到 CSV。
    每次运行约写入 10股×(5条意愿+20条关注)=250行，加速数据积累。
    """
    import csv
    header_needed = not log_path.exists()
    written = 0
    try:
        with open(log_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if header_needed:
                writer.writerow(["date", "stock_code", "stock_name", "type",
                                 "value_date", "value", "ma5", "change", "ma5_change"])
            for stock in stocks:
                code = stock["code"]
                name = stock["name"]
                # 参与意愿（返回5条，含5日趋势）
                try:
                    df = fetch_desire(code)
                    if df is not None and not df.empty:
                        for _, row in df.iterrows():
                            writer.writerow([
                                datetime.now().strftime("%Y-%m-%d"), code, name,
                                "desire",
                                row.get("交易日期", ""),
                                row.get("参与意愿", ""),
                                row.get("5日平均参与意愿", ""),
                                row.get("参与意愿变化", ""),
                                row.get("5日平均变化", ""),
                            ])
                            written += 1
                except Exception:
                    pass
                time.sleep(0.3)
                # 关注指数（返回约20条）
                try:
                    df = fetch_focus(code)
                    if df is not None and not df.empty:
                        for _, row in df.iterrows():
                            writer.writerow([
                                datetime.now().strftime("%Y-%m-%d"), code, name,
                                "focus",
                                row.get("交易日", ""),
                                row.get("用户关注指数", ""),
                                "", "", "",
                            ])
                            written += 1
                except Exception:
                    pass
                time.sleep(0.3)
    except Exception as e:
        logger.warning("完整历史 CSV 写入失败: %s", e)
    return written


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
    pdf_data = markdown_to_pdf(md_content, font_size="18pt")
    if not pdf_data:
        logger.error("PDF 生成失败，请检查 weasyprint 安装")
        logger.info("Markdown 内容预览:\n%s", md_content[:500])
        sys.exit(1)

    pdf_filename = f"{date_str}东方财富评级报告.pdf"
    pdf_path = REPORTS_DIR / pdf_filename
    pdf_path.write_bytes(pdf_data)
    logger.info("PDF 已保存: %s (%d bytes)", pdf_path, len(pdf_data))

    # ══════════════════════════════════════════════════════════════
    # 记录 w/f 到 CSV（供未来回测校准 flat zone 阈值）
    # 目的：收集参与意愿(w)和关注指数(f)的历史数据，与次日涨跌幅
    # 对比，验证当前阈值分档（80/65/55/45/20）是否合理。
    # 详见 docs/research/eastmoney-rating-backtest.md
    # ══════════════════════════════════════════════════════════════
    summary_rows = []
    try:
        summary_rows = generate_summary_rows(stocks) or []
        wf_log = FUSION_ROOT / "data" / "realtime" / "eastmoney_wf_log.csv"
        header_needed = not wf_log.exists()
        with open(wf_log, "a") as f:
            if header_needed:
                f.write("date,stock_code,stock_name,willingness,focus,icon,conclusion,l7_level\n")
            for r in summary_rows:
                lvl = ""
                try:
                    wv = float(r.get("desire_val", 0))
                    if wv >= 80: lvl = "+3"
                    elif wv >= 55: lvl = "+2/+1"
                    elif wv >= 45: lvl = "0"
                    elif wv >= 20: lvl = "-2/-1"
                    else: lvl = "-3"
                except (ValueError, TypeError):
                    pass
                f.write(f"{date_str},{r['code']},{r['name']},{r.get('desire_val','')},"
                        f"{r.get('focus_val','')},{r['icon']},{r['conclusion']},{lvl}\n")
        logger.info("w/f 已追加到 %s (%d 条)", wf_log, len(summary_rows))
    except Exception as e:
        logger.warning("w/f CSV 写入失败: %s", e)

    # ═══ 保存完整5日历史（加速数据积累，每次约250行） ═══
    try:
        hist_log = FUSION_ROOT / "data" / "realtime" / "eastmoney_wf_history.csv"
        n = save_wf_full_history(stocks, hist_log)
        logger.info("w/f 完整历史已追加到 %s (%d 行)", hist_log, n)
    except Exception as e:
        logger.warning("w/f 完整历史写入失败: %s", e)

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

        # 5a. 先推送简讯文本
        if summary_rows:
            try:
                brief_text = generate_brief_text(summary_rows)
                text_ok = sender.send_to_wechat(brief_text)
                if text_ok:
                    logger.info("✅ 简讯已推送 (%d 只股票)", len(summary_rows))
                else:
                    logger.warning("简讯推送失败，继续推送 PDF")
            except Exception as e:
                logger.warning("简讯推送失败: %s", e)

        # 5b. 再推送 PDF 详情
        ok = sender.send_to_wechat_file(pdf_data, pdf_filename)
        if ok:
            logger.info("✅ 评级报告 PDF 已成功推送: %s", pdf_filename)
        else:
            logger.error("❌ 评级报告 PDF 推送失败")
            sys.exit(1)

    logger.info("=" * 60)
    logger.info("完成")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
