# 数据源配置

三系统各自独立管理数据源，互不依赖。

---

## ly (lynx_vnpy) — 量化信号系统

| 数据需求 | 数据源 | 降级 |
|---------|--------|------|
| 日K线(OHLCV) | 新浪财经 API | 本地缓存(4h TTL) |

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
| 日K线(OHLCV) | akshare(东方财富) | baostock → yfinance |
| 技术指标 | akshare 自算(RSI/MACD/布林/ATR/均线) | — |
| 基本面 | akshare | yfinance |
| 财报(三表) | akshare | yfinance |
| 资金流向 | akshare(个股资金流) | — |
| 北向资金 | akshare(沪深港通) | — |
| 新闻 | 东方财富(EastMoney) | stock_info_news |
| 情绪(散户) | 东方财富(个股评级+关注度) | — |
| 全球宏观 | yfinance | alpha_vantage |

依赖: `akshare`, `baostock`, `yfinance`, `pandas`, `numpy`

---

## 数据安全

| 系统 | 缓存 | 限速保护 | 熔断 |
|------|------|---------|------|
| ly | ✅ parquet 4h TTL | 3次重试+退避 | — |
| ml | ✅ 文件缓存+内存缓存 | 2-5s 随机 sleep | ✅ circuit breaker |
| at | ✅ baostock 登录态 | 3次重试+退避 | — |
