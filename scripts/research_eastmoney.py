"""
东方财富全市场快照 — 批量分析（已迁移到 services/eastmoney/research.py）。

保留此文件作为向后兼容入口。新开发请使用 services/eastmoney/ 路径。
"""
from pathlib import Path
import sys

_SERVICE_PATH = Path(__file__).resolve().parent / "services" / "eastmoney" / "research.py"
if _SERVICE_PATH.exists():
    import runpy
    runpy.run_path(str(_SERVICE_PATH), run_name="__main__")
else:
    print(f"ERROR: {_SERVICE_PATH} 不存在，请检查 services/eastmoney/ 目录完整性")
    sys.exit(1)
