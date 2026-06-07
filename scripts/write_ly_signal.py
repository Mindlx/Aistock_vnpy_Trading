#!/usr/bin/env python3
"""
lynx 量化信号 → 准实时文件交换区

用法:
    python scripts/write_ly_signal.py

在 lynx-signal.service 的 ExecStartPost 中调用，
将 lynx RF 模型的信号归一化后写入 data/realtime/ly_signal.json，
让实时融合服务在 15:15（而非 15:30+）就能获取 ly 数据。

⚠️ 仅供学习和研究目的，不构成任何投资建议
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# lynx 子系统路径
LYNX_ROOT = PROJECT_ROOT / "systems" / "lynx_vnpy"
sys.path.insert(0, str(LYNX_ROOT))

from src.normalizer import SignalNormalizer

OUTPUT = PROJECT_ROOT / "data" / "realtime" / "ly_signal.json"


def main():
    try:
        import lynx_signal
    except ImportError as e:
        print(f"[write_ly_signal] 导入 lynx_signal 失败: {e}")
        return 1

    stock_codes = list(getattr(lynx_signal, "STOCK_CODES", []))
    if not stock_codes:
        print("[write_ly_signal] 无股票代码")
        return 1

    results_rf = {}
    results_lgb = {}
    for code in stock_codes:
        try:
            df = lynx_signal.fetch_daily_bars(code)
            if df is None or len(df) < 20:
                continue
            name = str(df.iloc[-1].get("股票名称", code))

            # RF模型
            df_feat = lynx_signal.compute_features(df)
            sig = lynx_signal.predict_signal(df_feat, code, name)
            if sig:
                prob_up = float(sig.get("prob_up", 50))
                signal_text = sig.get("signal", "")
                score, _ = SignalNormalizer.normalize_lynx(signal_text, prob_up)
                results_rf[code] = {"score": round(score, 3), "signal": signal_text, "prob_up": prob_up}

            # LGB模型（Alpha158）
            from vnpy_bridge.alpha_predictor import alpha_predict
            prob_lgb = alpha_predict(df, code)
            if prob_lgb is not None:
                prob_pct = prob_lgb * 100
                score_lgb, _ = SignalNormalizer.normalize_lynx("", prob_pct)
                results_lgb[code] = {"score": round(score_lgb, 3), "prob_up": round(prob_pct, 1)}
        except Exception as e:
            print(f"[write_ly_signal] {code}: {e}")
        time.sleep(1)

    # 写入 RF 信号
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    for prefix, results in [("ly_signal", results_rf), ("ly_alpha_signal", results_lgb)]:
        data = {"stocks": results, "updated_at": datetime.now().strftime("%Y-%m-%d"), "source": prefix}
        path = OUTPUT.parent / f"{prefix}.json"
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.rename(tmp, str(path))

    print(f"[write_ly_signal] RF {len(results_rf)}/{len(stock_codes)} → ly_signal.json")
    print(f"[write_ly_signal] LGB {len(results_lgb)}/{len(stock_codes)} → ly_alpha_signal.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
