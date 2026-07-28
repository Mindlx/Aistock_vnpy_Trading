"""评分校准 — v5.0 映射，基于 1048 条回测数据。

独立于项目根目录的 normalizer.py，避免跨 src/ 包导入冲突。

用法:
    from src.calibration import calibrate_score
    score = calibrate_score(100)  # → 75
"""


def calibrate_score(raw: int | None) -> int | None:
    """raw 0-100 → v5.0 calibrated 0-100（用于推送/前端/监控显示）

    L7 映射基于 1048 条回测数据（2026-07-27 c1test T+1 口径）。
    """
    if raw is None:
        return None
    s = int(raw)
    if s <= 19:
        l7 = -2.5    #  80.0% acc, 20 samples
    elif s <= 39:
        l7 = -1.5    #  65.4% acc, 491+20 samples
    elif s <= 49:
        l7 = -2.0    #  75.4% acc, 281 samples
    elif s <= 51:
        l7 = 0.0     #   0.0% acc, 16 samples (flat zone)
    elif s <= 59:
        l7 = 0.5     #  34.8% acc, 141 samples
    elif s <= 79:
        l7 = 1.0     #  54.5% acc, 99 samples
    else:
        l7 = 1.5     # extrapolated, no data

    calibrated = 50 + round(l7 * 50 / 3.0)
    return max(0, min(100, calibrated))
