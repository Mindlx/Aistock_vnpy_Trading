"""
===================================
API v1 Endpoints 模块初始化
===================================

职责：
1. 声明所有 endpoint 路由模块
"""

from api.v1.endpoints import (
    agent,
    alerts,
    analysis,
    auth,
    backtest,
    health,
    history,
    portfolio,
    stocks,
    system_config,
    usage,
)

__all__ = [
    "health",
    "analysis",
    "history",
    "stocks",
    "backtest",
    "system_config",
    "auth",
    "agent",
    "usage",
    "portfolio",
    "alerts",
]
