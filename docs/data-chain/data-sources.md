# 数据源配置与依赖审计

> 最后更新: 2026-06-23
> 审计范围: 三子系统 + 数据仓库全部 10+ 个数据源

---

## 一、可用数据源全景图

所有数据源按"封禁风险"分级：

| 数据源 | 协议 | 封禁风险 | 需要 Key | 提供的数据类型 |
|--------|------|---------|---------|--------------|
| **pytdx** | TCP 7709 | ⭐ 永不封 | 否 | OHLCV、实时行情、指数 |
| **Baostock** | TCP | ⭐ 永不封 | 否 | OHLCV、财务指标(ROE/EPS/营收)、基本面(行业/股本) |
| **腾讯行情** | HTTP | ⭐ 永不封 | 否 | 实时行情、PE/PB、市值、换手率、量比 |
| **新浪财经** | HTTP | ⭐⭐ 低 | 否 | OHLCV、三大报表(三表)、实时行情 |
| **Tushare Pro** | HTTP | ⭐⭐ 低 | Token(已配) | OHLCV、财务指标、资金流(需积分)、筹码、日线 |
| **FRED** | HTTP | ⭐⭐ 低 | 否 | 宏观经济数据 |
| **CNINFO巨潮** | HTTP | ⭐⭐ 低 | 否 | A股官方公告 |
| **CCTV新闻** | HTTP | ⭐ 永不封 | 否 | 市场新闻 |
| **YFinance** | HTTP | ⭐⭐ 低 | 否 | 港股/美股行情、基本面 |
| **Longbridge** | HTTP | ⭐⭐ 低 | Key(未配) | 港股/美股行情 |
| **Finnhub** | HTTP | ⭐⭐ 低 | Key(未配) | 美股行情 |
| **AlphaVantage** | HTTP | ⭐⭐ 低 | Key(未配) | 美股行情 |
| **TickFlow** | HTTP | ⭐⭐ 低 | Key(已配) | 大盘指数、板块数据 |
| **东方财富(EM)** | HTTP | 🔴 **高** | 否 | 资金流、财务、新闻、大宗交易、股东变化 |
| **百度股市通** | HTTP | ⭐ 极低 | 否 | 资金流替代（待证） |
| **同花顺 10jqka** | HTTP | ⭐⭐ 低 | 否 | 资金流、北向资金（待证） |

---

## 二、各子系统当前数据源

### ly (lynx_vnpy) — 量化信号系统

| 数据需求 | 数据源 | 降级 |
|---------|--------|------|
| 日K线(OHLCV) | 新浪财经 API | 数据仓库缓存(WarehouseReader, 24h TTL) |
| 实时价格 | 腾讯财经(qt.gtimg.cn) | — |
| Alpha158/LGB | 预计算因子(无外部API) | — |

**EM 依赖**: ❌ 无
**依赖库**: `requests`, `pandas`, `scikit-learn`, `joblib`

---

### ml (MindLynx-Aistock) — AI 分析系统

| 数据需求 | 首选 | 降级链 |
|---------|------|--------|
| 日K线/分钟线 | **pytdx**(TCP) → Tushare(已配Token) → Efinance(EM) → Akshare(EM→新浪→腾讯) → Baostock → YFinance |
| 实时行情 | 腾讯 → 新浪 → akshare_em | |
| 基本面/财报 | Tushare(已配Token) → Akshare(EM) | |
| 新闻 | EastMoney → CNINFO(巨潮公告) | |
| 资金流向 | Akshare(EM) → **无替代** | |
| 技术指标 | 本地计算(无EM) | |
| 指数行情 | TickFlow(已配Key) | |

**EM 依赖**: ⚠️ 部分（资金流独有）
**依赖库**: `akshare`, `efinance`, `tushare`, `pytdx`, `baostock`, `yfinance`

---

### at (mind_TradingAgent) — 多智能体系统 — 2026-06-23 Tushare集成

| 数据需求 | 首选 | 降级 |
|---------|------|------|
| 日K线(OHLCV) | akshare(EM) → **pytdx(TCP)** → **Tushare(付费)** → baostock | |
| 技术指标 | 自算(RSI/MACD/布林/ATR) — OHLCV来自pytdx | — |
| 基本面 | **Tushare(fina_indicator+daily_basic)** → akshare(EM) | |
| 三表(财报) | akshare(同花顺ths) — **非EM** | — |
| 资金流向 | akshare(EM) — **无替代**(见数据仓库Tushare) | |
| 北向资金 | akshare(EM) — **无替代** | |
| 新闻 | akshare(EM) | stock_info_news |
| 全球宏观 | yfinance | alpha_vantage |

**EM 依赖**: ⚠️ 大幅降低（OHLCV/基本面已走Tushare，仅资金流/新闻仍EM）
**commit**: `2901524`

### 自选股池管理

目前有 **18 只自选股**，集中配置在 `config/stock_pool.csv`（单源配置），其余位置自动从 CSV 读取：

| # | 文件 | 读取方式 | 作用域 |
|:---:|------|---------|--------|
| 1 | **`config/stock_pool.csv`** | 编辑 CSV（格式: `代码,名称,SH\|SZ`） | **唯一手工维护点** |
| 2 | `src/mind_stock_config.py` | 自动读 CSV → `A_SHARE_MARKET_MAP` | TA 模块 + 部分服务 |
| 3 | `systems/MindLynx-Aistock/.env` | `STOCK_LIST` + `STOCK_GROUP_[1-3]`（已同步 CSV） | ML 子系统 |
| 4 | `systems/lynx_vnpy/lynx_signal.py` | 自动读 CSV → `STOCK_CODES` + `STOCK_NAMES` | 量化信号系统 |
| 5 | `services/alpha158_service.py` | 自动读 CSV → `STOCK_CODES` | Alpha158 因子服务 |
| 6 | `services/ml_factor_service.py` | 自动读 CSV → `STOCK_CODES` | ML 因子服务 |
| 7 | `services/data_warehouse/config.py` | 优先读 CSV，env `STOCK_LIST` 仅作覆盖 | 数据仓库 |
| 8 | `scripts/backtest_lgb.py` | 自动读 CSV → `STOCK_CODES` | LGB 回测 |

> `.env` 两处（项目根 + `systems/MindLynx-Aistock/`）已与 CSV 同步。
> 增减股票只需编辑 `config/stock_pool.csv`，其余位置自动适配。

---

## 三、数据仓库 (Data Warehouse) — 降级链一览

`services/data_warehouse/` 的 Fetchers 当前降级链状态（2026-06-23, Tushare 已集成）：

| Fetcher | 当前降级链 | EM? |
|---------|-----------|:---:|
| **DailyFetcher** | pytdx(TCP) → Sina → **Tushare** → akshare_em → efinance | ⚠️ 末位 |
| **RealtimeFetcher** | 腾讯(批量) → Sina | ❌ 无EM |
| **FinancialFetcher** | **Tushare**(fina_indicator) → akshare_em | ⚠️ 后备 |
| **CapitalFlowFetcher** | **Tushare**(moneyflow) → akshare_em | ⚠️ 后备 |
| **NewsFetcher** | akshare_em(新闻) → CNINFO(公告) | 🔴 首选 |
| **FundamentalsFetcher** | **Tushare**(stock_basic+daily_basic) → akshare_em | ⚠️ 后备 |

> **改造结果** (commit 2d88a9a, 7cb2d55): Tushare 替换 EM 作为财务/资金流/基本面的首选源。EM 日请求从 ~50 降至 ~12 次。

---

## 四、EastMoney 依赖分级与替代策略（2026-06-23 Oracle验证）

### 🔴 必须用 EM（无免费替代）
| 数据 | 使用场景 | 缓解策略 |
|------|---------|---------|
| 资金流(主力/大单/散户) | ML整点分析、AT辩论 | 盘前一次拉满缓存 |
| 北向资金 | 大盘复盘 | 盘前一次 |
| 大宗交易 | AT分析 | 非关键，可选跳过 |
| 股东变化 | AT分析 | 非关键，可选跳过 |
| 个股新闻 | ML推送、AT分析 | 盘前一次 + CNINFO公告兜底 |

### ✅ 可不走 EM（已有替代源）
| 数据 | 替代源 | 状态 |
|------|--------|------|
| OHLCV K线 | pytdx TCP | ✅ 已为首选 |
| 实时行情 | 腾讯 HTTP | ✅ 已配，提升优先级 |
| PE/PB/市值 | 腾讯 fields[39][46][45] | ⚠️ 代码未提取 |
| ROE/EPS/营收增长 | Baostock query_profit_data | ⚠️ 代码未集成 |
| 毛利率/净利率 | Baostock query_profit_data | ⚠️ 代码未集成 |
| 资产负债率 | Baostock query_balance_data | ⚠️ 代码未集成 |
| 个股基本信息 | Baostock query_stock_basic | ⚠️ 代码未集成 |
| 三表(资产负债表/利润表/现金流量表) | 新浪财经 HTTP API | ⚠️ 代码未集成 |
| ETF数据 | Tushare | ⚠️ 代码未集成 |
| 指数行情 | TickFlow(已配Key) | ⚠️ 仅大盘复盘 |
| 公告 | CNINFO 直连 | ✅ 已有 |

### 已配置但未充分利用的数据源

| 数据源 | Token/Key 状态 | 用途 |
|--------|--------------|------|
| **Tushare Pro** | ✅ TUSHARE_TOKEN 已配置 | 财务指标、OHLCV、资金流(需积分) |
| **TickFlow** | ✅ TICKFLOW_API_KEY 已配置 | 指数/板块数据(仅大盘复盘) |
| **Longbridge** | ❌ 未配置凭据 | 港美股(暂不需要) |
| **Finnhub** | ❌ 未配置 API Key | 美股(暂不需要) |
| **AlphaVantage** | ❌ 未配置 API Key | 美股(暂不需要) |

---

## 五、计划改造路线

| 优先级 | 改动 | 文件 | 效果 |
|--------|------|------|------|
| **P0** | RealtimeFetcher 改腾讯首选 | `fetchers.py` | EM 实时行情请求归零 |
| **P0** | FinancialFetcher 加 Baostock 首选 | `fetchers.py` | EM 财务请求归零 |
| **P0** | PE/PB 用腾讯字段[39][46]取代 EM | `fetchers.py` | EM PE/PB 请求归零 |
| **P1** | 盘前预热（08:00 拉 EM 独有数据） | `scheduler.py` | EM 请求集中到盘前 |
| **P1** | Baostock 基本面替换 `stock_individual_info_em` | `fetchers.py` | EM 基本面请求归零 |
| **P2** | 新浪三表 HTTP 直连 | `fetchers.py` | EM 三表请求归零 |
| **P2** | 熔断器(403/429 自动跳过 EM) | `limiter.py` | 封禁期不浪费重试 |

**目标**: EM 日请求从 ~350 次降至 **~20-40 次**（仅资金流+新闻盘前一次），远低于 ~200 次封禁线。

---

## 六、令牌桶配置

| API 源 | 速率 | 桶容量 | 基础退避 | 熔断 |
|--------|------|--------|---------|------|
| eastmoney | 15/min | 15 | 1.0s | ❌ (计划加) |
| sina | 60/min | 60 | 2.0s | ❌ |
| tencent | 120/min | 120 | 0.5s | ❌ |
| cninfo | 30/min | 30 | 1.0s | ❌ |
| tushare | 50/min | 50 | 1.0s | ❌ |

## 七、数据湖缓存 TTL

| 数据类型 | TTL | 刷新策略 |
|---------|-----|---------|
| daily_ohlcv | 24h | 15:30 工作日统一批量(1次/天) |
| realtime_quotes | 5min | 盘中每5分钟轮询 |
| financial_indicators | 24h | 16:00 工作日(Baostock TCP, 0 EM) |
| capital_flows | 24h | 盘前 08:00 一次(EM), 盘中不刷新 |
 | news_events | 1h | EventMonitor 实时写入 + 盘前 EM 补一次 |
| fundamentals | 7d | 周一 09:00(Baostock TCP, 0 EM) |

---

## 八、已知限制

### 新闻摘要缺失

数据源（东方财富免费 API `ak.stock_news_em`）不返回文章正文/摘要，`news_events.summary` 字段值与 `title` 相同。

入库时 `summary == title` 置空，前端 `snippet` 不渲染。

**待解决**：需要接入有摘要的数据源（Tushare 新闻接口、或爬取文章全文），或填充 `intelligence_items` 表。
