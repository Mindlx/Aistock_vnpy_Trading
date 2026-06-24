"""
services/eastmoney — 东方财富数据服务

职责:
  fetcher.py    数据获取+缓存+推送+全市场快照存档
  research.py   全市场快照分析（IC/面板数据）
  config.py     路径常量

与子系统的关系:
  当前: 数据源 → ML prompt 上下文（非融合投票）
  未来: 如 c1skill 验证通过，可升级为独立子系统

依赖:
  fetcher.py 需 ML venv（systems/MindLynx-Aistock/.venv/）
  research.py 纯 pandas，任意 venv
"""
