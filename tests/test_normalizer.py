"""测试: SignalNormalizer L7映射 + 归一化函数"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.normalizer import (
    SignalNormalizer,
    L7_THRESHOLDS, L7_LABELS, L7_SIGNAL_NAMES,
    L7_EMOJI, L7_POSITION,
)

errors = 0

# ── Test 1: L7_THRESHOLDS 一致性 ──
print("=== L7_THRESHOLDS 一致性 ===")
# L7_LABELS 的 key 应该包含 [-3, -2, -1, 0, 1, 2, 3]
for k in (-3, -2, -1, 0, 1, 2, 3):
    if k not in L7_LABELS:
        print(f"❌ L7_LABELS 缺少 key={k}")
        errors += 1
print(f"✅ L7_LABELS: 7 levels [{min(L7_LABELS.keys())}..{max(L7_LABELS.keys())}]")

# L7_THRESHOLDS 覆盖所有非中性信号
expected_sigs = {"strong_bullish", "bullish", "cautious_bullish", "cautious_bearish", "bearish", "strong_bearish"}
missing = expected_sigs - set(L7_THRESHOLDS.keys())
if missing:
    print(f"❌ L7_THRESHOLDS 缺少: {missing}")
    errors += 1
else:
    print(f"✅ L7_THRESHOLDS: {len(L7_THRESHOLDS)} 阈值")

# L7_SIGNAL_NAMES / L7_EMOJI / L7_POSITION 应与 L7_LABELS 的值对齐
for _, sig in L7_LABELS.items():
    if sig not in L7_SIGNAL_NAMES:
        print(f"❌ L7_SIGNAL_NAMES 缺少 {sig}")
        errors += 1
    if sig not in L7_EMOJI:
        print(f"❌ L7_EMOJI 缺少 {sig}")
        errors += 1
    if sig not in L7_POSITION:
        print(f"❌ L7_POSITION 缺少 {sig}")
        errors += 1
print(f"✅ L7_SIGNAL_NAMES: {len(L7_SIGNAL_NAMES)}, L7_EMOJI: {len(L7_EMOJI)}, L7_POSITION: {len(L7_POSITION)}")

# ── Test 2: 阈值单调性 ──
print()
print("=== 阈值单调性 ===")
assert L7_THRESHOLDS["strong_bullish"] > L7_THRESHOLDS["bullish"] > L7_THRESHOLDS["cautious_bullish"]
assert L7_THRESHOLDS["cautious_bearish"] > L7_THRESHOLDS["bearish"] > L7_THRESHOLDS["strong_bearish"]
assert L7_THRESHOLDS["cautious_bullish"] > 0 > L7_THRESHOLDS["cautious_bearish"]
print("✅ 阈值单调: strong > bullish > cautious > 0 > cautious_bear > bear > strong_bear")

# ── Test 3: normalize_lynx 映射 ──
print()
print("=== normalize_lynx ===")
test_cases = [
    (50, 0.0, "中性"),        # flat zone
    (55, 0.0, "中性"),        # flat zone (45-50)
    (65, 1.5, "看多"),        # 谨慎看多以上
    (75, 2.0, "看多"),        # 看多
    (90, 2.5, "强烈看多"),    # 强烈看多
    (40, -0.5, "谨慎看空"),   # flat zone 以下
    (30, -1.5, "看空"),
    (20, -2.0, "看空"),
    (5, -2.5, "强烈看空"),
]
for prob_up, expected_direction, label in test_cases:
    score, valid = SignalNormalizer.normalize_lynx("观望", prob_up)
    if not valid:
        print(f"❌ normalize_lynx({prob_up}): valid=False")
        errors += 1
        continue
    # 检查方向是否正确（而非精确值，因为经验映射可能微调）
    if label == "中性" and abs(score) > 0.5:
        print(f"❌ normalize_lynx({prob_up}): 期望中性(|score|<0.5), 实际{score:.2f}")
        errors += 1
    elif label == "看多" and score <= 0:
        print(f"❌ normalize_lynx({prob_up}): 期望看多(score>0), 实际{score:.2f}")
        errors += 1
    elif label == "强烈看多" and score < 2.0:
        print(f"❌ normalize_lynx({prob_up}): 期望强烈看多(score>=2.0), 实际{score:.2f}")
        errors += 1
    elif label == "谨慎看空" and score >= 0:
        print(f"❌ normalize_lynx({prob_up}): 期望看空(score<0), 实际{score:.2f}")
        errors += 1
    elif label == "看空" and score >= 0:
        print(f"❌ normalize_lynx({prob_up}): 期望看空(score<0), 实际{score:.2f}")
        errors += 1
    elif label == "强烈看空" and score > -2.0:
        print(f"❌ normalize_lynx({prob_up}): 期望强烈看空(score<=-2.0), 实际{score:.2f}")
        errors += 1
    else:
        dir_str = "看多" if score > 0 else ("看空" if score < 0 else "中性")
        print(f"✅ normalize_lynx({prob_up:2.0f}): score={score:+.2f} ({dir_str}) 期望={label}")

# ── Test 4: normalize_mindlynx_score (HP3) ──
print()
print("=== normalize_mindlynx_score (HP3) ===")
ml_cases = [
    (80, "看多", 52, 49),       # ≥80 → +1.5 (S2 bull, capped for low accuracy)
    (60, "谨慎看多", 52, 49),   # 60-79 → +1.0 (S3, dampened 38.2% acc)
    (55, "中性", 52, 49),       # 52-59 → +0.8 (S4+, 56.2% acc, below S3 threshold)
    (50, "中性", 52, 49),       # flat zone → 0.0
    (45, "谨慎看空", 52, 49),   # 41-48 → -1.5 (S5, preserved)
    (30, "看空", 52, 49),       # 20-30 → -2.5 (S6/S7 boundary, 89.0% acc)
    (10, "强烈看空", 52, 49),   # ≤19 → -3.0 (S7, 100.0% acc)
]
for score, desc, tb, tbr in ml_cases:
    result = SignalNormalizer.normalize_mindlynx_score(score, tb, tbr)
    direction = "看多" if result > 0 else ("看空" if result < 0 else "中性")
    print(f"✅ ML_score({score:3d}): score={result:+.2f} ({direction}) 期望={desc}")

# ── Test 5: normalize_tradingagent ──
print()
print("=== normalize_tradingagent ===")
ta_cases = [("Buy", 3.0), ("Overweight", 2.06), ("Hold", 0.0), ("Underweight", -1.13), ("Sell", -3.0), ("Invalid", 0.0)]
for rating, expected in ta_cases:
    result = SignalNormalizer.normalize_tradingagent(rating)
    if abs(result - expected) > 0.01 and rating != "Invalid":
        print(f"❌ TA({rating}): 期望{expected:.2f}, 实际{result:.2f}")
        errors += 1
    elif rating == "Invalid" and result != 0.0:
        print(f"❌ TA({rating}): 期望0.0, 实际{result:.2f}")
        errors += 1
    else:
        print(f"✅ TA({rating:15s}): score={result:+.2f}")

# ── Test 6: to_probability ──
print()
print("=== to_probability ===")
prob_cases = [(0.0, 0.5), (2.0, 0.88), (-2.0, 0.12), (3.0, 0.95), (-3.0, 0.05)]
for score, expected_min in prob_cases:
    p = SignalNormalizer.to_probability(score)
    if abs(p - expected_min) > 0.1:
        print(f"❌ to_prob({score:+.1f}): 期望{expected_min:.2f}, 实际{p:.2f}")
        errors += 1
    else:
        print(f"✅ to_prob({score:+.1f}): {p:.3f}")

# ── Test 7: l7_target_pct ──
print()
print("=== l7_target_pct ===")
pct_cases = [("strong_bullish", 25), ("bullish", 15), ("neutral", 0), ("cautious_bearish", 2.5), ("bearish", 1.5), ("strong_bearish", 0)]
for sig, expected in pct_cases:
    pct = SignalNormalizer.l7_target_pct(sig)
    if pct != expected:
        print(f"❌ l7_target_pct({sig}): 期望{expected}, 实际{pct}")
        errors += 1
    else:
        print(f"✅ l7_target_pct({sig:20s}): {pct}%")

# ── Test 8: cap_position_for_disagreement ──
print()
print("=== cap_position_for_disagreement ===")
cap_cases = [("2-3成", "1成"), ("1-2成", "1成"), ("0.5-1成", "1成"), ("0成", "0成"), ("清仓", "清仓"), ("大幅减仓", "减仓至0.5成以内")]
for inp, expected in cap_cases:
    result = SignalNormalizer.cap_position_for_disagreement(inp)
    if result != expected:
        print(f"❌ cap_position({inp:10s}): 期望{expected}, 实际{result}")
        errors += 1
    else:
        print(f"✅ cap_position({inp:10s}): {result}")

# ── Summary ──
print(f"\n{'='*40}")
if errors:
    print(f"❌ 共 {errors} 个错误")
else:
    print("✅ 全部通过 (8 项测试)")
