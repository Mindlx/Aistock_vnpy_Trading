# 数据源配置

三系统各自独立管理数据源，互不依赖。**数据仓库服务层 (`services/data_warehouse/`) 统一协调所有外部 API 调用，缓存优先、限流保护。** 详见 [`data-warehouse-implementation.md`](data-warehouse-implementation.md)。

---

## ly (lynx_vnpy) — 量化信号系统

| 数据需求 | 数据源 | 降级 |
|---------|--------|------|
| 日K线(OHLCV) | 新浪财经 API | 统一缓存(SQLite, 24h TTL) |

依赖: `requests`, `pandas`, `scikit-learn`, `joblib`

---

## ml (MindLynx-Aistock) — AI 分析系统

| 数据需求 | 首选 | 降级链 |
|---------|------|--------|
| 日K线/分钟线 | efinance(东方财富) | akshare → tushare(需token) → pytdx(通达信) → baostock → yfinance |
| 实时行情 | WebSocket | HTTP 15s 轮询(akshare/腾讯/新浪) |
| 基本面/财报 | tushare(需token) | akshare |
| 新闻 | EastMoney | Cninfo(巨潮) |
| 资金流向 | akshare | — |

依赖: `akshare`, `efinance`, `tushare`, `pytdx`, `baostock`, `yfinance`, `websocket`

---

## at (mind_TradingAgent) — 多智能体系统

| 数据需求 | 首选 | 降级 |
|---------|------|------|
| 日K线(OHLCV) | yfinance | akshare(东方财富) → baostock |
| 技术指标 | 自算(RSI/MACD/布林/ATR/均线) | — |
| 基本面 | akshare | yfinance |
| 财报(三表) | akshare | yfinance |
| 资金流向 | akshare(个股资金流) | — |
| 北向资金 | akshare(沪深港通) | — |
| 新闻 | 东方财富(EastMoney) | stock_info_news |
| 情绪(散户) | 东方财富(个股评级+关注度) | — |
| 全球宏观 | yfinance | alpha_vantage |

依赖: `akshare`, `baostock`, `yfinance`, `pandas`, `numpy`

---

## 数据仓库服务层 (Data Warehouse)

`services/data_warehouse/` 模块包取代了原有的统一缓存，提供：

### 跨进程令牌桶限流

| API 源 | 速率 | 重试策略 |
|--------|------|---------|
| EastMoney | **15 次/分钟** | 3次指数退避 1s→2s→4s, ±30% 抖动 |
| Sina | 60 次/分钟 | 3次退避, 2s 基础 |
| Tencent | 120 次/分钟 | 3次退避, 0.5s 基础 |
| CNINFO | 30 次/分钟 | 3次退避, 1s 基础 |
| TuShare | 50 次/分钟 | 3次退避, 1s 基础 |

### 数据湖缓存 TTL

| 数据类型 | TTL | 刷新策略 |
|---------|-----|---------|
| daily_ohlcv | 24h | 15:30 工作日统一批量(1次/天) |
| realtime_quotes | 5min | 盘中每5分钟轮询 |
| financial_indicators | 24h | 16:00 工作日 |
| capital_flows | 24h | 16:30 工作日 |
| news_events | 1h | EventMonitor 实时写入 + 每小时定时刷新 |
| fundamentals | 7d | 周一09:00 |

对比旧统一缓存: 新增限流协调(令牌桶)、多数据类型(财务/资金流/新闻)、跨进程共享(SQLite WAL)。

---

## 数据安全

| 系统 | 缓存 | 限速保护 | 熔断 |
|------|------|---------|------|
| ly | ✅ 数据仓库(WarehouseReader → parquet) | 仓库令牌桶 + 3次退避 | — |
| ml | ✅ 数据仓库(WarehouseReader + 原有DB) | 仓库令牌桶 + 2-5s jitter | ✅ circuit breaker |
| at | ✅ 数据仓库(WarehouseReader → akshare) | 仓库令牌桶 + 3次退避 | — |
| fusion | ✅ 数据仓库(SQLite WAL) | 仓库令牌桶 | — |
