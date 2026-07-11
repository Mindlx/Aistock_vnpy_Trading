"""
三系统输出数据加载适配器（零侵入版）

兼容层: 所有 Loader 类已拆分到 src/loaders/ 包。
保留此文件确保已有 import 路径不变。
"""
from src.loaders import *  # noqa: F401, F403
