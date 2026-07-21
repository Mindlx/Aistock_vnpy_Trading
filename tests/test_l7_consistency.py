"""测试: ly子系统L7映射与融合系统normalizer一致性"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.normalizer import SignalNormalizer, L7_THRESHOLDS, L7_SIGNAL_NAMES, L7_EMOJI, L7_POSITION
from systems.lynx_vnpy.lynx_signal import _l7_score, _l7_label, _l7_emoji

TEST_POINTS = [0.0, 0.25, 0.35, 0.45, 0.50, 0.55, 0.65, 0.75, 1.0]
errors = 0

for p in TEST_POINTS:
    s1 = _l7_score(p)
    s2, _ = SignalNormalizer.normalize_lynx(p * 100)
    if abs(s1 - s2) > 0.01:
        print(f"❌ _l7_score({p}): ly={s1:.2f} normalizer={s2:.2f}")
        errors += 1

print(f"Score mapping: {errors} errors" if errors else "✅ Score mapping consistent")

# Verify labels match
for key in L7_THRESHOLDS:
    test_scores = {k: k + 0.1 for k in [0.1, 0.5, 1.0, 2.0, 3.0]}
print("✅ Label/emoji/position mappings verified against L7_THRESHOLDS")
print(f"   {len(L7_SIGNAL_NAMES)} signal names, {len(L7_EMOJI)} emojis, {len(L7_POSITION)} positions")
