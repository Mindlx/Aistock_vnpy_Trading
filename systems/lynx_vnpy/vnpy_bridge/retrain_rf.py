#!/usr/bin/env python3
"""
RF模型自动重训 — 删除超过7天的模型文件，触发 predict_signal 懒加载重训

用法:
    python systems/lynx_vnpy/vnpy_bridge/retrain_rf.py

逻辑:
    systems/lynx_vnpy/models/*_model.pkl 超过7天 → 删除 → 下次调用时自动重训
"""
import sys, os, datetime
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_PROJ))

MODEL_DIR = _PROJ / "systems/lynx_vnpy/models"
MAX_AGE_DAYS = 7


def main():
    now = datetime.datetime.now()
    print(f"[retrain_rf] {now.isoformat()}")
    print(f"   模型目录: {MODEL_DIR}")

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
