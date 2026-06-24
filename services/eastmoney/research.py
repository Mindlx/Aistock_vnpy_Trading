"""
东方财富全市场快照 — 批量分析脚本。

用法:
    # 查看数据积累情况
    .venv/bin/python services/eastmoney/research.py --status

    # 全维度分析（需 ≥20 个交易日）
    .venv/bin/python services/eastmoney/research.py --analyze

    # 导出自选股面板数据
    .venv/bin/python services/eastmoney/research.py --export-panel
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = PROJECT_ROOT / "data" / "research" / "eastmoney_snapshot"
REPORT_DIR = PROJECT_ROOT / "reports" / "eastmoney"
OUR_CODES = [
    "001390", "300652", "600372", "605368", "000592",
    "603189", "603557", "688202", "601801", "300676",
    "603127", "000999",
]


def load_all_snapshots() -> list[dict]:
    summaries = sorted(SNAPSHOT_DIR.glob("snapshot_*_summary.json"))
    records = []
    for p in summaries:
        try:
            records.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"  ⚠️  {p.name}: 读取失败 {e}")
    return records


def load_snapshot_csv(date_str: str) -> pd.DataFrame | None:
    path = SNAPSHOT_DIR / f"snapshot_{date_str}.csv"
    if not path.exists():
        return None
    return pd.read_csv(path, dtype={"代码": str, "名称": str})


def cmd_status():
    records = load_all_snapshots()
    print(f"东方财富快照存档: {SNAPSHOT_DIR}")
    print(f"总天数: {len(records)}\n")

    if not records:
        print("⚠️  暂无存档数据（首次 fetch 后将自动创建）")
        return

    print(f"{'日期':>10s}  {'股票数':>8s}  {'关注均值':>8s}  {'得分均值':>8s}  {'成本偏离%':>10s}")
    print("-" * 50)
    for r in records:
        print(f"{r['date']:>10s}  {r['total_stocks']:>8d}  {r.get('focus_avg', '-') or '-':>8}  "
              f"{r.get('score_avg', '-') or '-':>8}  {r.get('cost_deviation_avg', '-') or '-':>10}")

    print(f"\n自选股覆盖: {records[0].get('our_stocks', 0)}/{len(OUR_CODES)} 只")
    print(f"状态: {'✅ 数据充足' if len(records) >= 20 else f'⏳ 还需 {20 - len(records)} 个交易日'}")


def cmd_analyze():
    records = load_all_snapshots()
    if len(records) < 20:
        print(f"⚠️  数据不足：当前 {len(records)} 天，需要 ≥20 天")
        return

    print(f"=== 东方财富全维度 IC 分析 ===")
    print(f"覆盖: {records[0]['date']} ~ {records[-1]['date']} ({len(records)} 天)\n")

    field_pairs = [
        ("关注指数", "涨跌幅"), ("综合得分", "涨跌幅"),
        ("机构参与度", "涨跌幅"), ("cost_deviation_pct", "涨跌幅"),
        ("score_x_focus", "涨跌幅"),
    ]
    corr_sum = {f1: [] for f1, _ in field_pairs}
    for r in records:
        df = load_snapshot_csv(r["date"])
        if df is None:
            continue
        for f1, f2 in field_pairs:
            if f1 in df.columns and f2 in df.columns:
                corr_sum[f1].append(df[f1].corr(df[f2]))

    for f1, _ in field_pairs:
        vals = corr_sum.get(f1, [])
        if vals:
            avg = sum(vals) / len(vals)
            print(f"  {f1:15s} vs 涨跌幅: 日均 IC = {avg:+.4f}")
        else:
            print(f"  {f1:15s}: 无数据")

    print("\n⚠️  本报告为截面相关性分析，非时序预测能力验证。")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


def cmd_export_panel():
    records = load_all_snapshots()
    if not records:
        print("⚠️  暂无存档数据")
        return

    rows = []
    for r in records:
        df = load_snapshot_csv(r["date"])
        if df is None:
            continue
        ours = df[df["代码"].isin(OUR_CODES)].copy()
        ours["date"] = r["date"]
        rows.append(ours)

    if not rows:
        print("无自选股数据")
        return

    panel = pd.concat(rows, ignore_index=True)
    out_path = REPORT_DIR / "our_stocks_panel.csv"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    panel.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"自选股面板已导出: {out_path}")
    print(f"  维度: {panel['date'].nunique()} 天 × {panel['代码'].nunique()} 只股票")


def main():
    parser = argparse.ArgumentParser(description="东方财富全市场快照分析")
    parser.add_argument("--status", action="store_true", help="数据积累状态")
    parser.add_argument("--analyze", action="store_true", help="全维度 IC 分析 (需 ≥20 天)")
    parser.add_argument("--export-panel", action="store_true", help="导出自选股面板数据")
    args = parser.parse_args()

    if args.status:
        cmd_status()
    elif args.analyze:
        cmd_analyze()
    elif args.export_panel:
        cmd_export_panel()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
