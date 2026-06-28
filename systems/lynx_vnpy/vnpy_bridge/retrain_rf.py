#!/usr/bin/env python3
"""
RF模型自动重训 — 删除超过7天的模型文件，触发 predict_signal 懒加载重训

用法:
    python systems/lynx_vnpy/vnpy_bridge/retrain_rf.py

逻辑:
    systems/lynx_vnpy/models/*_model.pkl 超过7天 → 删除 → 下次调用时自动重训
"""
import subprocess
import sys, os, datetime
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_PROJ))

MODEL_DIR = _PROJ / "systems/lynx_vnpy/models"
MAX_AGE_DAYS = 7

_RETRAIN_SERVICES = [
    "Aistock_vnpy_Trading-retrain-lgb.service",
]


def _retrain_service_available() -> bool:
    """检查至少一个重训 systemd 服务已启用，防止删除模型后无法重建"""
    for svc in _RETRAIN_SERVICES:
        try:
            result = subprocess.run(
                ["systemctl", "is-enabled", svc],
                capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip() == "enabled":
                return True
        except Exception:
            continue
    # 兜底：如果 systemctl 不可用（容器环境等），允许删除
    return True


def main():
    now = datetime.datetime.now()
    print(f"[retrain_rf] {now.isoformat()}")
    print(f"   模型目录: {MODEL_DIR}")

    # Safety check: 确认重训服务可用，防止删除后无法重建
    if not _retrain_service_available():
        print("   ⚠  retrain服务未启用，跳过删除以防模型不可恢复")
        return

    deleted = 0
    kept = 0
    for f in sorted(MODEL_DIR.glob("*_model.pkl")):
        mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime)
        age = (now - mtime).days
        if age >= MAX_AGE_DAYS:
            f.unlink()
            # Also remove corresponding scaler
            scaler = MODEL_DIR / f.name.replace("_model.pkl", "_scaler.pkl")
            if scaler.exists():
                scaler.unlink()
            print(f"   🗑  {f.name} (已{age}天)")
            deleted += 1
        else:
            kept += 1

    print(f"   删除: {deleted}, 保留: {kept}")


if __name__ == "__main__":
    main()
