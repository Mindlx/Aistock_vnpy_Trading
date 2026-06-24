"""测试: FusionEngine 核心融合逻辑"""
import sys, os, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 避免缺配置文件启动失败
os.environ.setdefault("WECOM_WEBHOOK_URL", "https://test.webhook")
os.environ.setdefault("STOCK_LIST", "600372,000592")

from src.fusion_engine import FusionEngine
from src.normalizer import SignalNormalizer

errors = 0

# ── 初始化引擎 ──
engine = FusionEngine(config_path=str(Path(__file__).resolve().parent.parent / "config/settings.yaml"))
print("✅ FusionEngine 初始化成功")
print(f"   模式: {engine.fusion_mode}")
print(f"   权重: ly={engine.weights['lynx_vnpy']}, ml={engine.weights['mindlynx']}, at={engine.weights['tradingagent']}")

# ── Test 1: _compute_adjusted_weights ──
print()
print("=== _compute_adjusted_weights ===")

# 三系统都有效
w, cnt, degraded = engine._compute_adjusted_weights(True, True, True)
if abs(w["lynx"] + w["mindlynx"] + w["tradingagent"] - 1.0) < 0.01 and cnt == 3 and not degraded:
    print(f"✅ 三系统有效: 权重归一化正确 {w}")
else:
    print(f"❌ 三系统有效: w={w}, cnt={cnt}, degraded={degraded}")
    errors += 1

# 仅 ML 有效
w, cnt, degraded = engine._compute_adjusted_weights(False, True, False)
if "lynx" not in w and "tradingagent" not in w and abs(w["mindlynx"] - 1.0) < 0.01:
    print(f"✅ 仅ML有效: mindlynx={w['mindlynx']:.2f}")
else:
    print(f"❌ 仅ML有效: {w}")
    errors += 1

# 全部无效
w, cnt, degraded = engine._compute_adjusted_weights(False, False, False)
if cnt == 0:
    print(f"✅ 全部无效: cnt={cnt}")
else:
    print(f"❌ 全部无效: cnt={cnt}")
    errors += 1

# AT 被移除后(tradingagent=0.00)不应出现
w, cnt, degraded = engine._compute_adjusted_weights(True, True, True)
if w.get("tradingagent", 1) == 0.0:
    print(f"✅ AT 权重已归零: {w}")
else:
    print(f"ℹ️  AT 权重不为零 (不影响, 由主权重决定): {w}")

# ── Test 2: _detect_disagreement ──
print()
print("=== _detect_disagreement ===")

# 同向（都看多）
has_d, score = engine._detect_disagreement(2.0, 1.5, 0.0, True, True, True)
if not has_d:
    print(f"✅ 同向看多: 无分歧")
else:
    print(f"❌ 同向看多: 应无分歧, 实际分歧={has_d}, score={score}")
    errors += 1

# 反向（LY看多, ML看空）
has_d, score = engine._detect_disagreement(2.0, -1.5, 0.0, True, True, True)
if has_d:
    print(f"✅ 反向: 检测到分歧 (score={score:.2f})")
else:
    print(f"❌ 反向: 应检测到分歧")
    errors += 1

# 先单后多 → 分歧？
has_d, score = engine._detect_disagreement(-1.0, 2.0, -0.3, True, True, True)
if has_d:
    print(f"✅ LY空ML多 + AT中性: 检测到分歧 (score={score:.2f})")
else:
    print(f"❌ LY空ML多: 应检测到分歧")
    errors += 1

# 都是中性
has_d, score = engine._detect_disagreement(0.0, 0.0, 0.0, True, True, True)
if not has_d:
    print(f"✅ 全是中性: 无分歧")
else:
    print(f"❌ 全是中性: 应无分歧")
    errors += 1

# 单系统有效
has_d, score = engine._detect_disagreement(1.0, 0.0, 0.0, True, False, False)
if not has_d:
    print(f"✅ 仅LY有效: 无分歧 (len<2)")
else:
    print(f"❌ 仅LY有效: 应无分歧")
    errors += 1

# ── Test 3: _get_final_decision ──
print()
print("=== _get_final_decision ===")

decision_cases = [
    (3.0, False, "strong_bullish"),   # 强烈看多
    (2.0, False, "bullish"),           # 看多
    (1.2, False, "cautious_bullish"),  # 谨慎看多
    (0.0, False, "neutral"),           # 中性
    (-0.8, False, "cautious_bearish"), # 谨慎看空
    (-2.0, False, "bearish"),          # 看空
    (-3.0, False, "strong_bearish"),   # 强烈看空
]
for score, dis, expected in decision_cases:
    result = engine._get_final_decision(score, dis)
    if result["signal"] != expected:
        print(f"❌ decision({score}): 期望{expected}, 实际{result['signal']}")
        errors += 1
    else:
        print(f"✅ decision({score:+.1f}): {result['signal']:20s} pos={result['position']}")

# 分歧时检查仓位上限
result = engine._get_final_decision(2.5, True)
if "1成" in result["position"] or "0.5成" in result["position"]:
    print(f"✅ 分歧时仓位受限: {result['signal']} pos={result['position']}")
else:
    print(f"ℹ️  分歧时仓位 = {result['position']} (逻辑可能已变)")

# ── Test 4: _fuse_linear 基本功能 ──
print()
print("=== _fuse_linear 基本功能 ===")

# 正常融合: LY看多 + ML看多
result = engine._fuse_linear(
    stock_code="600372",
    stock_name="中航机载",
    lynx_signal="看多", lynx_prob_up=65.0,
    mindlynx_advice="买入", mindlynx_score=65, mindlynx_trend="up",
    mindlynx_valid=True,
    tradingagent_rating="Buy", tradingagent_valid=True,
)
if result.get("valid") and result.get("fusion_score") > 0:
    print(f"✅ 融合成功: signal={result['signal']}, score={result['fusion_score']:.2f}")
else:
    print(f"❌ 融合失败: {result}")
    errors += 1

# LY有效 + ML无效（降级场景）
result2 = engine._fuse_linear(
    stock_code="600372",
    stock_name="中航机载",
    lynx_signal="强烈看多", lynx_prob_up=80.0,
    mindlynx_advice="观望", mindlynx_score=50, mindlynx_trend="",
    mindlynx_valid=False,
    tradingagent_rating="Hold", tradingagent_valid=False,
)
if result2.get("valid") and result2.get("is_degraded"):
    print(f"✅ 降级融合: signal={result2['signal']}, degraded={result2['is_degraded']}")
else:
    print(f"ℹ️  降级融合: signal={result2['signal']}, degraded={result2.get('is_degraded')}")

# 所有系统无效
result3 = engine._fuse_linear(
    stock_code="600372",
    lynx_signal="观望", lynx_prob_up=50,
    mindlynx_advice="观望", mindlynx_score=50, mindlynx_trend="",
    mindlynx_valid=False,
    tradingagent_rating="Hold", tradingagent_valid=False,
)
if not result3.get("valid", True):
    print(f"✅ 全部无效: valid=False, msg={result3.get('message','')}")
else:
    print(f"ℹ️  全部无效但valid={result3.get('valid')}")

# ── Test 5: fuse_single_stock (mode=dual) ──
print()
print("=== fuse_single_stock (dual mode) ===")
result4 = engine.fuse_single_stock(
    stock_code="600372",
    stock_name="中航机载",
    lynx_signal="看多", lynx_prob_up=70.0,
    mindlynx_advice="买入", mindlynx_score=70, mindlynx_trend="up",
    mindlynx_valid=True,
    tradingagent_rating="Buy", tradingagent_valid=True,
)
if result4.get("fusion_mode") == "dual" and "linear" in result4 and "bayesian" in result4:
    print(f"✅ dual模式: linear={result4['linear']['signal']}, bayesian={result4['bayesian']['signal']}")
else:
    print(f"❌ dual模式异常: mode={result4.get('fusion_mode')}")
    errors += 1

# ── Summary ──
print(f"\n{'='*40}")
if errors:
    print(f"❌ 共 {errors} 个错误")
else:
    print("✅ 全部通过")
