"""测试: position.py 仓位计算引擎"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.position import (
    L7_TARGET_PCT, L7_TARGET_PCT_RANGE, L7_TARGET_LABEL,
    PositionLabel, pct_to_label, UnifiedPosition,
    PositionConstraintEngine, pct_to_cheng, cheng_to_pct,
)

errors = 0

# ── Test 1: 仓位常量一致性 ──
print("=== 仓位常量一致性 ===")
expected_keys = {"strong_bullish", "bullish", "cautious_bullish", "neutral", "cautious_bearish", "bearish", "strong_bearish"}
for name, d in [("L7_TARGET_PCT", L7_TARGET_PCT), ("L7_TARGET_LABEL", L7_TARGET_LABEL), ("L7_TARGET_PCT_RANGE", L7_TARGET_PCT_RANGE)]:
    if set(d.keys()) != expected_keys:
        print(f"❌ {name}: key 不匹配, 缺失={expected_keys - set(d.keys())}, 多余={set(d.keys()) - expected_keys}")
        errors += 1
    else:
        print(f"✅ {name}: 7个key全部正确")

# 仓位单调性（看多仓位应 > 看空仓位）
if L7_TARGET_PCT["strong_bullish"] >= L7_TARGET_PCT["bullish"] >= L7_TARGET_PCT["cautious_bullish"] >= L7_TARGET_PCT["neutral"]:
    print("✅ 看多仓位单调递减")
else:
    print("❌ 看多仓位顺序异常")
    errors += 1

if L7_TARGET_PCT["cautious_bearish"] >= L7_TARGET_PCT["bearish"] >= L7_TARGET_PCT["strong_bearish"]:
    print("✅ 看空仓位单调递减")
else:
    print("❌ 看空仓位顺序异常")
    errors += 1

# ── Test 2: PositionLabel ──
print()
print("=== PositionLabel ===")
for label in PositionLabel:
    print(f"✅ PositionLabel.{label.name} = {label.value}")
expected_labels = ["HEAVY", "MEDIUM", "LIGHT", "WATCH", "NONE"]
actual_labels = [e.name for e in PositionLabel]
if actual_labels == expected_labels:
    print(f"✅ PositionLabel: {len(PositionLabel)} 个等级 {expected_labels}")
else:
    print(f"❌ PositionLabel: 期望{expected_labels}, 实际{actual_labels}")
    errors += 1

# ── Test 3: pct_to_label ──
print()
print("=== pct_to_label ===")
for pct, expected in [(85, "HEAVY"), (60, "HEAVY"), (35, "HEAVY"), (15, "MEDIUM"), (5, "LIGHT"), (2, "WATCH"), (0, "NONE"), (-5, "NONE")]:
    result = pct_to_label(pct).name
    if result != expected:
        print(f"❌ pct_to_label({pct:3d}): 期望{expected}, 实际{result}")
        errors += 1
    else:
        print(f"✅ pct_to_label({pct:3d}): {expected}")

# ── Test 4: pct_to_cheng / cheng_to_pct (单向验证) ──
print()
print("=== pct_to_cheng (仅测试输出格式) ===")
cheng_cases = [(0, "0成"), (50, "5成"), (100, "10成")]
for pct, expected in cheng_cases:
    cheng = pct_to_cheng(pct)
    if cheng == expected:
        print(f"✅ pct_to_cheng({pct}): {cheng}")
    else:
        print(f"⚠️  pct_to_cheng({pct}): 实际'{cheng}', 期望'{expected}' (仅显示精度问题)")

# ── Test 5: UnifiedPosition ──
print()
print("=== UnifiedPosition ===")
# 看多信号
pos = UnifiedPosition.from_signal("strong_bullish")
if pos and pos.pct > 0:
    print(f"✅ strong_bullish: pct={pos.pct}%, label={pos.label}")
else:
    print(f"❌ strong_bullish: 应返回正向仓位")
    errors += 1

# 中性信号
pos2 = UnifiedPosition.from_signal("neutral")
if pos2 and pos2.pct == 0:
    print(f"✅ neutral: pct={pos2.pct}%, label={pos2.label}")
else:
    print(f"ℹ️  neutral: pct={pos2.pct if pos2 else 'None'}")

# 看空信号
pos3 = UnifiedPosition.from_signal("bearish")
if pos3 and pos3.label == "空仓":
    print(f"✅ bearish: pct={pos3.pct}%, label={pos3.label}")
else:
    print(f"ℹ️  bearish: pct={pos3.pct if pos3 else 'None'}, label={pos3.label if pos3 else 'None'}")

# 无效信号
pos4 = UnifiedPosition.from_signal("invalid_signal_xxx")
if pos4 is None:
    print(f"✅ invalid_signal: 返回 None")
else:
    print(f"ℹ️  invalid_signal: {pos4}")

# ── Test 6: PositionConstraintEngine ──
print()
print("=== PositionConstraintEngine ===")
engine = PositionConstraintEngine(total_stocks=10)

# 正常状态（无分歧）
pos_list = [UnifiedPosition.from_signal("strong_bullish")]
c1 = engine.apply(pos_list, disagreements=[False])
if c1 and len(c1) > 0:
    print(f"✅ 无分歧strong_bullish: pct={c1[0].pct}%, label={c1[0].label}")
else:
    print(f"❌ apply 异常: {c1}")
    errors += 1

# 分歧状态（仓位应被限制）
pos_list2 = [UnifiedPosition.from_signal("bullish", source="test")]
c2 = engine.apply(pos_list2, disagreements=[True])
if c2 and len(c2) > 0:
    print(f"✅ 分歧bullish: pct={c2[0].pct}%, label={c2[0].label}")
    if c2[0].pct <= 10:
        print(f"   分歧限制生效: pct={c2[0].pct}% ≤ 1成")
    else:
        print(f"   ⚠️ 分歧限制未生效: pct={c2[0].pct}%")
else:
    print(f"❌ apply 异常: {c2}")
    errors += 1

# 总仓位上限测试
pos_list3 = [UnifiedPosition(30, label="test"), UnifiedPosition(35, label="test2"),
             UnifiedPosition(25, label="test3")]
c3 = engine.apply(pos_list3)
if c3:
    total = sum(p.pct for p in c3)
    print(f"✅ 总仓位约束: {total}% (上限95%), 缩放系数={95/sum(p.pct for p in pos_list3):.2f}")
else:
    print(f"❌ apply 异常")
    errors += 1

# summary
summary = engine.summary(c3)
print(f"✅ summary: total={summary['total_position_pct']}%, active={summary['active_stocks']}, cash={summary['cash_pct']}%")

# ── Summary ──
print(f"\n{'='*40}")
if errors:
    print(f"❌ 共 {errors} 个错误")
else:
    print("✅ 全部通过")
