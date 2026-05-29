#!/usr/bin/env python3
"""
C1 Phase 2 — 经验信号相关性诊断脚本

计算 9 个 OHLCV 衍生信号的实际成对相关性 (Spearman ρ)，
按市场体制分层，输出有效广度 (Grinold-Kahn BR)。

用法:
    python scripts/compute_signal_correlation.py           # 全量自选股
    python scripts/compute_signal_correlation.py --json    # JSON 输出
    python scripts/compute_signal_correlation.py --plot    # 生成热力图 (需 matplotlib)

输出:
    - 全样本相关性矩阵
    - 体制分层相关性矩阵 (trending / range-bound / volatile)
    - 有效广度 BR = N/(1+(N-1)ρ̄) 及体制分层 BR
    - Grinold-Kahn 信息比率增益上限

参考:
    Grinold & Kahn, Active Portfolio Management (2nd ed), Ch.6 & Technical Appendix
    docs/data_coherence_c1_appendix.md
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from numpy import corrcoef

# 确保 src/ 在路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── 9 个 C1 目标信号的定义 ──────────────────────────────────────────
# 每个信号: (名称, 分组, 计算函数)
# 分组: trend / volatility / position / volume


def compute_signals(close: np.ndarray, volume: np.ndarray, high: np.ndarray, low: np.ndarray) -> dict[str, np.ndarray]:
    """从 OHLCV 数组计算 9 个信号。每个信号返回与 close 等长的数组（前导 NaN）。"""
    n = len(close)
    signals: dict[str, np.ndarray] = {}

    # ── 趋势组 ──
    # 1. momentum_reversal: 过去 21 天的回报（越高 → 反转概率越大）
    rev = np.full(n, np.nan)
    for i in range(21, n):
        rev[i] = (close[i] - close[i - 21]) / close[i - 21]
    signals["momentum_reversal"] = rev

    # 2. momentum_spread: 5 天回报 − 20 天回报
    spread = np.full(n, np.nan)
    for i in range(20, n):
        ret5 = (close[i] - close[i - 5]) / close[i - 5] if i >= 5 else 0
        ret20 = (close[i] - close[i - 20]) / close[i - 20]
        spread[i] = ret5 - ret20
    signals["momentum_spread"] = spread

    # 3. consecutive_direction: (涨天数 − 跌天数) / 10
    consec = np.full(n, np.nan)
    for i in range(10, n):
        ups = sum(1 for j in range(i - 9, i + 1) if close[j] > close[j - 1])
        downs = 10 - ups
        consec[i] = (ups - downs) / 10
    signals["consecutive_direction"] = consec

    # 4. MA20 斜率 (regime 趋势代理)
    ma20_slope = np.full(n, np.nan)
    for i in range(40, n):
        ma20 = np.mean(close[i - 20 : i])
        ma20_prev = np.mean(close[i - 40 : i - 20])
        if ma20_prev > 0:
            ma20_slope[i] = (ma20 - ma20_prev) / ma20_prev
    signals["ma20_slope"] = ma20_slope

    # ── 波动/风险组 ──
    # 5. low_volatility: −年化标准差（取负值以保持方向一致，低波动 = 正值）
    lowvol = np.full(n, np.nan)
    for i in range(20, n):
        rets = np.diff(close[i - 20 : i + 1]) / close[i - 20 : i]
        lowvol[i] = -np.std(rets) * np.sqrt(252)
    signals["low_volatility"] = lowvol

    # 6. volatility_ratio: 短期标准差 / 长期标准差
    volratio = np.full(n, np.nan)
    for i in range(20, n):
        rets_short = np.diff(close[i - 5 : i + 1]) / close[i - 5 : i] if i >= 5 else [0]
        rets_long = np.diff(close[i - 20 : i + 1]) / close[i - 20 : i]
        std_s = np.std(rets_short) if len(rets_short) > 1 else 0
        std_l = np.std(rets_long) if len(rets_long) > 1 else 1e-9
        volratio[i] = std_s / (std_l + 1e-9)
    signals["volatility_ratio"] = volratio

    # 7. max_effect: −最大日回报
    maxeff = np.full(n, np.nan)
    for i in range(20, n):
        rets = np.diff(close[i - 20 : i + 1]) / close[i - 20 : i]
        maxeff[i] = -np.max(np.abs(rets))
    signals["max_effect"] = maxeff

    # ── 位置组 ──
    # 8. price_position: (close − 60d min) / (60d max − 60d min)
    pos = np.full(n, np.nan)
    for i in range(60, n):
        window = close[i - 60 : i + 1]
        rng = np.max(window) - np.min(window)
        pos[i] = (close[i] - np.min(window)) / rng if rng > 0 else 0.5
    signals["price_position"] = pos

    # ── 成交量组 ──
    # 9. turnover_sentiment: 近期成交量 / 20 日平均成交量
    #    非直接收盘价衍生，但仍与价格数据共享时间结构
    to_sent = np.full(n, np.nan)
    for i in range(20, n):
        avg_vol = np.mean(volume[i - 20 : i])
        to_sent[i] = volume[i] / avg_vol if avg_vol > 0 else 1.0
    signals["turnover_sentiment"] = to_sent

    return signals


SIGNAL_GROUPS: dict[str, list[str]] = {
    "trend": ["momentum_reversal", "momentum_spread", "consecutive_direction", "ma20_slope"],
    "volatility": ["low_volatility", "volatility_ratio", "max_effect"],
    "position": ["price_position"],
    "volume": ["turnover_sentiment"],
}


def classify_regime(close_40: np.ndarray) -> str:
    """基于 MA20 斜率对 40 天窗口进行体制分类。"""
    if len(close_40) < 40:
        return "unknown"
    ma20 = np.mean(close_40[-20:])
    ma20_prev = np.mean(close_40[-40:-20])
    slope = (ma20 - ma20_prev) / ma20_prev if ma20_prev > 0 else 0
    if slope > 0.005:
        return "trending_up"
    elif slope < -0.005:
        return "trending_down"
    else:
        return "range_bound"


def _rankdata(a: np.ndarray) -> np.ndarray:
    """Rank transform: assign average rank to ties (emulates scipy.stats.rankdata)."""
    n = len(a)
    order = np.argsort(a)
    ranks = np.empty(n, dtype=float)
    i = 0
    while i < n:
        j = i
        while j < n and a[order[j]] == a[order[i]]:
            j += 1
        rank_val = (i + j - 1) / 2.0 + 1
        for k in range(i, j):
            ranks[order[k]] = rank_val
        i = j
    return ranks


def compute_pairwise_correlation(
    signal_dict: dict[str, np.ndarray], signal_names: list[str]
) -> tuple[np.ndarray, float]:
    """计算信号间的成对 Spearman ρ。返回 (矩阵, 平均 |ρ|)。"""
    n_signals = len(signal_names)
    corr_matrix = np.eye(n_signals)
    valid_pairs = 0
    total_rho = 0.0

    for i in range(n_signals):
        for j in range(i + 1, n_signals):
            a = signal_dict.get(signal_names[i])
            b = signal_dict.get(signal_names[j])
            if a is None or b is None:
                continue
            # 仅使用两个信号均有效的重叠索引
            mask = ~np.isnan(a) & ~np.isnan(b)
            if mask.sum() < 30:
                continue
            # Spearman ρ = Pearson correlation on rank-transformed data
            a_ranked = _rankdata(a[mask])
            b_ranked = _rankdata(b[mask])
            rho = corrcoef(a_ranked, b_ranked)[0, 1]
            if np.isnan(rho):
                continue
            rho = float(rho)
            corr_matrix[i][j] = rho
            corr_matrix[j][i] = rho
            total_rho += abs(rho)
            valid_pairs += 1

    if valid_pairs == 0:
        return corr_matrix, 0.0

    avg_abs_rho = total_rho / valid_pairs
    return corr_matrix, avg_abs_rho


def effective_breadth(n_signals: int, avg_rho: float) -> float:
    """Grinold-Kahn 有效广度: BR = N / (1 + (N-1)ρ̄)"""
    if avg_rho >= 1.0:
        return 1.0
    return n_signals / (1 + (n_signals - 1) * avg_rho)


def main():
    parser = argparse.ArgumentParser(description="C1 信号相关性诊断")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--plot", action="store_true", help="生成相关性热力图 (需 matplotlib)")
    parser.add_argument("--stocks", type=str, default="", help="指定股票代码，逗号分隔 (默认: 从配置读取)")
    args = parser.parse_args()

    # ── 加载配置 ──
    from src.config import get_config

    config = get_config()
    stock_codes = [s.strip() for s in args.stocks.split(",") if s.strip()] if args.stocks else config.stock_list
    if not stock_codes:
        print("错误: 未找到自选股列表", file=sys.stderr)
        sys.exit(1)

    print(f"股票池: {len(stock_codes)} 只 ({', '.join(stock_codes[:5])}{'...' if len(stock_codes) > 5 else ''})")

    # ── 加载数据并计算信号 ──
    from src.storage import get_db
    from sqlalchemy import text

    db = get_db()
    all_signals: dict[str, np.ndarray] = {}
    regime_samples: dict[str, list[dict[str, np.ndarray]]] = defaultdict(list)

    with db.session_scope() as session:
        for code in stock_codes:
            rows = session.execute(
                text("SELECT close, volume, high, low FROM stock_daily WHERE code=:code ORDER BY date"),
                {"code": code},
            ).fetchall()
            if not rows or len(rows) < 60:
                print(f"  {code}: 数据不足 ({len(rows)} 行)，跳过")
                continue

            close_arr = np.array([float(r[0]) for r in rows], dtype=float)
            volume_arr = np.array([float(r[1]) for r in rows], dtype=float)
            high_arr = np.array([float(r[2]) for r in rows], dtype=float)
            low_arr = np.array([float(r[3]) for r in rows], dtype=float)

            sigs = compute_signals(close_arr, volume_arr, high_arr, low_arr)

            # 累积全量信号
            for name, arr in sigs.items():
                if name not in all_signals:
                    all_signals[name] = arr
                else:
                    all_signals[name] = np.concatenate([all_signals[name], arr])

            # 按体制分层采样（每只股票取最后 40 天作为一次体制样本）
            regime = classify_regime(close_arr[-40:])
            if regime != "unknown":
                regime_samples[regime].append(sigs)

            print(f"  {code}: {len(rows)} 行, regime={regime}")

    signal_names = sorted(all_signals.keys())
    if len(signal_names) < 3:
        print("错误: 信号数量不足", file=sys.stderr)
        sys.exit(1)

    # ── 全样本相关性 ──
    corr_all, avg_rho_all = compute_pairwise_correlation(all_signals, signal_names)
    br_all = effective_breadth(len(signal_names), avg_rho_all)

    # ── 体制分层 ──
    regime_results: dict[str, dict] = {}
    for regime, samples in regime_samples.items():
        if len(samples) < 2:
            continue
        # 合并该体制下所有样本的信号
        merged: dict[str, np.ndarray] = {}
        for sigs in samples:
            for name, arr in sigs.items():
                if name not in merged:
                    merged[name] = arr
                else:
                    merged[name] = np.concatenate([merged[name], arr])
        corr_r, avg_r = compute_pairwise_correlation(merged, signal_names)
        br_r = effective_breadth(len(signal_names), avg_r)
        regime_results[regime] = {
            "samples": len(samples),
            "avg_abs_rho": round(avg_r, 4),
            "effective_breadth": round(br_r, 2),
            "ir_gain_vs_single": round(np.sqrt(br_r), 3),
            "correlation_matrix": [[round(float(corr_r[i][j]), 3) for j in range(len(signal_names))] for i in range(len(signal_names))],
        }

    # ── 输出 ──
    if args.json:
        output = {
            "stock_count": len(stock_codes),
            "signal_names": signal_names,
            "signal_groups": SIGNAL_GROUPS,
            "all_sample": {
                "avg_abs_rho": round(avg_rho_all, 4),
                "effective_breadth": round(br_all, 2),
                "ir_gain_vs_single": round(np.sqrt(br_all), 3),
                "correlation_matrix": [[round(float(corr_all[i][j]), 3) for j in range(len(signal_names))] for i in range(len(signal_names))],
            },
            "by_regime": regime_results,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*70}")
        print(f"C1 信号相关性诊断报告")
        print(f"{'='*70}")
        print(f"股票池: {len(stock_codes)} 只")
        print(f"信号数量: {len(signal_names)}")
        print(f"信号分组: {', '.join(f'{k}({len(v)})' for k, v in SIGNAL_GROUPS.items())}")

        print(f"\n── 全样本 ──")
        print(f"平均 |ρ|: {avg_rho_all:.4f}")
        print(f"有效广度 (Grinold-Kahn BR): {br_all:.2f} (原始 N={len(signal_names)})")
        print(f"IR 增益上限 vs 单信号: {np.sqrt(br_all):.3f}× (独立假设下应为 √{len(signal_names)}={np.sqrt(len(signal_names)):.1f}×)")

        print(f"\n{'信号':<25}", end="")
        for s in signal_names:
            print(f"{s[:8]:>8}", end="")
        print()
        for i, si in enumerate(signal_names):
            print(f"{si:<25}", end="")
            for j in range(len(signal_names)):
                print(f"{corr_all[i][j]:>8.3f}", end="")
            print()

        print(f"\n── 体制分层 ──")
        regime_labels = {"trending_up": "📈 上升趋势", "trending_down": "📉 下降趋势", "range_bound": "📊 区间震荡"}
        for regime, data in sorted(regime_results.items()):
            label = regime_labels.get(regime, regime)
            print(f"\n{label} (样本数: {data['samples']})")
            print(f"  平均 |ρ|: {data['avg_abs_rho']:.4f}")
            print(f"  有效广度: {data['effective_breadth']:.2f}")
            print(f"  IR 增益上限: {data['ir_gain_vs_single']:.3f}×")

        # 关键发现
        print(f"\n── 关键发现 ──")
        print(f"1. 全样本 ρ̄ = {avg_rho_all:.3f} → 有效广度 ≈ {br_all:.1f}")
        if avg_rho_all > 0.8:
            print(f"   ⚠️  ρ̄ > 0.8: 信号高度冗余，{len(signal_names)} 个信号等效于约 {br_all:.1f} 个独立信号")
            print(f"   ⚠️  系统架构暗示 {len(signal_names)}× 信息，实际增益仅约 {np.sqrt(br_all):.2f}×")
        elif avg_rho_all > 0.5:
            print(f"   ⚡  ρ̄ > 0.5: 信号中度相关，仍有显著冗余")
        else:
            print(f"   ✅  ρ̄ ≤ 0.5: 信号多样化程度较高")

        # 体制分层关键发现
        for regime, data in sorted(regime_results.items()):
            label = regime_labels.get(regime, regime)
            if data["avg_abs_rho"] > 0.8:
                print(f"3. {label}: ρ̄={data['avg_abs_rho']:.3f} → 信号几乎完全冗余 (BR≈{data['effective_breadth']:.1f})")
            elif data["avg_abs_rho"] < 0.5:
                print(f"4. {label}: ρ̄={data['avg_abs_rho']:.3f} → C1 影响最小，信号提供真正的多样化价值")

    # ── 热力图 ──
    if args.plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig, axes = plt.subplots(1, min(len(regime_results) + 1, 4), figsize=(5 * min(len(regime_results) + 1, 4), 5))
            if len(regime_results) == 0:
                axes = [axes]
            axes = np.atleast_1d(axes)

            # 全样本
            im = axes[0].imshow(corr_all, cmap="RdBu_r", vmin=-1, vmax=1)
            axes[0].set_title(f"All (ρ̄={avg_rho_all:.3f}, BR={br_all:.1f})")
            axes[0].set_xticks(range(len(signal_names)))
            axes[0].set_yticks(range(len(signal_names)))
            axes[0].set_xticklabels([s[:8] for s in signal_names], rotation=45, ha="right", fontsize=7)
            axes[0].set_yticklabels([s[:15] for s in signal_names], fontsize=7)

            # 体制分层
            for ax_idx, (regime, data) in enumerate(sorted(regime_results.items()), start=1):
                if ax_idx >= len(axes):
                    break
                mat = np.array(data["correlation_matrix"])
                axes[ax_idx].imshow(mat, cmap="RdBu_r", vmin=-1, vmax=1)
                label = regime_labels.get(regime, regime)
                axes[ax_idx].set_title(f"{label} (ρ̄={data['avg_abs_rho']:.3f}, BR={data['effective_breadth']:.1f})")
                axes[ax_idx].set_xticks(range(len(signal_names)))
                axes[ax_idx].set_yticks(range(len(signal_names)))
                axes[ax_idx].set_xticklabels([s[:8] for s in signal_names], rotation=45, ha="right", fontsize=7)
                axes[ax_idx].set_yticklabels([])

            plt.tight_layout()
            out_path = Path(__file__).parent.parent / "reports" / "c1_signal_correlation.png"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            plt.savefig(out_path, dpi=150, bbox_inches="tight")
            print(f"\n热力图已保存: {out_path}")
        except Exception as exc:
            print(f"\n热力图生成失败: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
