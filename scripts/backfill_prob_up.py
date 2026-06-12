#!/usr/bin/env python3
"""
回填历史 prob_up 到 CSV，用于 flat zone 校准分析。

遍历 fusion JSON 历史文件，对每个日期+股票，
从 stock_daily 加载当日之前的日K线，重跑 RF+LGB 模型获取 prob_up。
追加写入 data/realtime/prob_up_log.csv。
"""
from __future__ import annotations

import csv
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

LYNX_ROOT = PROJECT_ROOT / "systems" / "lynx_vnpy"
sys.path.insert(0, str(LYNX_ROOT))

LOG_PATH = PROJECT_ROOT / "data" / "realtime" / "prob_up_log.csv"


def get_fusion_dates() -> list[str]:
    """从 fusion JSON 文件名获取历史日期列表（升序）。"""
    glob_path = PROJECT_ROOT / "data" / "fusion_output" / "fusion_*.json"
    dates = []
    for f in sorted(Path(PROJECT_ROOT / "data" / "fusion_output").glob("fusion_*.json")):
        date_str = f.stem.replace("fusion_", "")
        dates.append(date_str)
    return dates


def load_stock_pool() -> list[dict[str, str]]:
    """读取股票池。"""
    csv_path = PROJECT_ROOT / "config" / "stock_pool.csv"
    if not csv_path.exists():
        print(f"[backfill] 股票池不存在: {csv_path}")
        return []
    stocks = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f, skipinitialspace=True)
        for row in reader:
            code = (row.get("code") or "").strip()
            name = (row.get("name") or "").strip()
            if code:
                stocks.append({"code": code, "name": name})
    return stocks


def get_stock_data_up_to(code: str, end_date: str, db_path: str) -> list[dict]:
    """从 stock_analysis.db 的 stock_daily 表读取指定日期前的 OHLCV 数据。"""
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume, amount, pct_chg "
            "FROM stock_daily WHERE code=? AND date <= ? ORDER BY date",
            (code, end_date),
        ).fetchall()
        conn.close()
        return [{
            "date": r[0], "open": r[1], "high": r[2], "low": r[3],
            "close": r[4], "volume": r[5], "amount": r[6], "pct_chg": r[7],
        } for r in rows]
    except Exception as e:
        print(f"[backfill] DB 查询失败 {code}/{end_date}: {e}")
        return []


def _to_features_df(raw_data: list[dict]) -> "pd.DataFrame":
    """将 stock_daily 原始数据转换为 compute_features 可用的 DataFrame。"""
    import pandas as pd
    df = pd.DataFrame(raw_data)
    if df.empty:
        return df

    # 重命名列以匹配 lynx_signal.compute_features 的期望
    df = df.rename(columns={
        "date": "日期", "open": "开盘", "close": "收盘",
        "high": "最高", "low": "最低", "volume": "成交量",
        "amount": "成交额", "pct_chg": "涨跌幅",
    })
    # 补充 振幅、换手率、股票名称（用0填充即可——compute_features 不依赖它们）
    df["振幅"] = 0.0
    df["换手率"] = 0.0
    df["股票名称"] = ""
    return df


def append_csv(date: str, code: str, name: str,
               prob_rf: float | None, prob_lgb: float | None,
               l7_rf: float | None, l7_lgb: float | None):
    """追加一行到 prob_up CSV。"""
    header_needed = not LOG_PATH.exists()
    with open(LOG_PATH, "a") as f:
        if header_needed:
            f.write("date,stock_code,stock_name,prob_up_rf,prob_up_lgb,prob_up_ensemble,l7_score_rf,l7_score_lgb\n")
        prf = f"{prob_rf:.1f}" if prob_rf else ""
        plgb = f"{prob_lgb:.1f}" if prob_lgb is not None else ""
        ensemble = ""
        if prob_rf and prob_lgb is not None:
            ensemble = f"{(prob_rf + prob_lgb) / 2:.1f}"
        sr = f"{l7_rf:.2f}" if l7_rf else ""
        sl = f"{l7_lgb:.2f}" if l7_lgb is not None else ""
        f.write(f"{date},{code},{name},{prf},{plgb},{ensemble},{sr},{sl}\n")


def main():
    print("=" * 60)
    print("prob_up 历史回填脚本 启动")
    print("=" * 60)

    # 1. 读取股票池
    stocks = load_stock_pool()
    if not stocks:
        return 1
    print(f"股票池: {len(stocks)} 只")

    # 2. 获取历史日期
    dates = get_fusion_dates()
    print(f"历史日期: {len(dates)} 天 ({dates[0]} ~ {dates[-1]})")

    # 3. 确定 DB 路径
    db_path = str(PROJECT_ROOT / "systems" / "MindLynx-Aistock" / "data" / "stock_analysis.db")
    if not Path(db_path).exists():
        db_path = str(PROJECT_ROOT / "data" / "stock_analysis.db")
    print(f"DB 路径: {db_path}")

    # 4. 逐日逐股处理
    import lynx_signal
    from src.normalizer import SignalNormalizer

    total = len(stocks) * len(dates)
    done = 0
    for date in dates:
        for stock in stocks:
            code = stock["code"]
            name = stock["name"]
            done += 1

            # 从 DB 加载该日期前的数据
            raw = get_stock_data_up_to(code, date, db_path)
            if len(raw) < 20:
                print(f"  [{done}/{total}] {code} 数据不足 ({len(raw)}行)，跳过")
                continue

            df = _to_features_df(raw)
            try:
                df_feat = lynx_signal.compute_features(df)
            except Exception as e:
                print(f"  [{done}/{total}] {code}/{date} feature 计算失败: {e}")
                continue
            if df_feat is None or len(df_feat) < 2:
                continue

            # RF 模型预测
            rf_prob = None
            rf_l7 = None
            try:
                sig = lynx_signal.predict_signal(df_feat, code, name)
                if sig:
                    rf_prob = float(sig.get("prob_up", 0))
                    rf_l7, _ = SignalNormalizer.normalize_lynx(
                        sig.get("signal", ""), rf_prob
                    )
            except Exception as e:
                print(f"  [{done}/{total}] {code}/{date} RF 预测失败: {e}")

            # LGB 模型预测（alpha_predict）
            lgb_prob = None
            lgb_l7 = None
            try:
                from vnpy_bridge.alpha_predictor import alpha_predict
                prob = alpha_predict(df, code)
                if prob is not None:
                    lgb_prob = prob * 100
                    lgb_l7, _ = SignalNormalizer.normalize_lynx("", lgb_prob)
            except Exception as e:
                print(f"  [{done}/{total}] {code}/{date} LGB 预测失败: {e}")

            # 写入 CSV
            if rf_prob or lgb_prob is not None:
                append_csv(date, code, name, rf_prob, lgb_prob, rf_l7, lgb_l7)
                probs = f"RF={rf_prob:.1f}" if rf_prob else "-"
                probs += f" LGB={lgb_prob:.1f}" if lgb_prob is not None else ""
                print(f"  [{done}/{total}] {date} {name}({code}) {probs} ✓")

            time.sleep(0.3)

    # 5. 统计结果
    if LOG_PATH.exists():
        with open(LOG_PATH) as f:
            lines = f.readlines()
        print(f"\n✅ 完成！CSV 共 {len(lines) - 1} 行 (不含表头)")
    else:
        print("\n⚠️ CSV 未生成")

    return 0


if __name__ == "__main__":
    sys.exit(main())
