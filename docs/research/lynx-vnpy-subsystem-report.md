# lynx_vnpy 子系统应用备忘报告

> 最后更新: 2026-06-07
> 上游: [vnpy/vnpy](https://github.com/vnpy/vnpy) (MIT License)
> 说明: Aistock_vnpy_Trading/systems/lynx_vnpy/ 保留了 vnpy 完整库代码。本报告记录我们当前的使用方式、未利用能力、以及未来可拓展方向。

---

## 一、当前使用方式

### 1.1 我们的代码路径

整个ly子系统的业务逻辑集中在 `lynx_signal.py`（599行），它是一个完全独立的自建脚本，没有调用任何 vnpy 库代码：

```
数据获取 → 特征工程 → 模型训练/加载 → 信号推理 → 推送
  ↑           ↑            ↑            ↑          ↑
Sina API   手写15个     sklearn      pkl文件     WeComNotifier
HTTP请求    TA指标      RandomForest 读写
```

### 1.2 我们实际使用的能力清单

| 模块 | 用途 | 说明 |
|------|------|------|
| `lynx_signal.py` | 自建的信号生成全流程 | 数据→特征→模型→推理→推送 |
| `models/*.pkl` | 10只股票各自RF模型+scaler | 每只单独训练，保存为pickle |
| `lynx_vnpy/trader/datafeed.py` | ❌ 未使用 | 已自建Sina API数据获取 |
| `lynx_vnpy/alpha/` | ❌ 未使用 | 因子库和研究框架 |
| `lynx_vnpy/trader/` | ❌ 未使用 | 交易引擎和网关 |

**结论**: 目前对 vnpy 库代码的利用率为 **0%**。所有能力自建。

---

## 二、vnpy 完整代码库解析

### 2.1 Alpha 因子研究框架

位于 `lynx_vnpy/alpha/`，是 vnpy 的核心研究能力。

**因子数据集**

| 文件 | 因子数 | 来源 | 特点 |
|------|--------|------|------|
| `datasets/alpha_101.py` | 101 | WorldQuant | 经典数学表达式，从量价关系合成 |
| `datasets/alpha_158.py` | 158 | Qlib (微软) | K线形态+价格变化+时序统计，含多窗口参数 |

Alpha101 的因子表达式示例：
```
alpha1: cs_rank(ts_argmax(pow1(returns_expr, 2.0), 5)) - 0.5
alpha2: -1 × ts_corr(cs_rank(delta(log(volume),2)), cs_rank((close-open)/open), 6)
```

Alpha158 含6大类因子，每类覆盖5个窗口（5/10/20/30/60天）：
- K线形态（kmid/klen/kup/klow/ksft 等10个）
- 价格变化（open/high/low/vwap 相对close比率）
- 时序统计（ROC/MA/STD/Beta/RSQR/Resi 等10个×5窗口=50个）
- 极值统计（max/min/quantile/rank/RSV 等7个×5窗口=35个）
- 量价相关（corr/cord 等2个×5窗口=10个）
- 涨跌统计（cntp/cntn/cntd 等3个×5窗口=15个）

**对比我们手写的15个指标**：

| 维度 | vnpy Alpha101+Alpha158 | 我们的lynx_signal.py |
|------|----------------------|---------------------|
| 因子数量 | 259个 | 15个 |
| 覆盖类别 | 数学运算/时序统计/横截面/量价相关 | 仅基础TA指标 |
| 窗口多样性 | 5个窗口(5/10/20/30/60) | 固定窗口(14/20等) |
| 横截面因子 | 有（cs_rank等跨股票运算） | 无（仅单股票时序） |
| 因子表达式 | 声明式，加一行即可加因子 | 手写pandas代码，需改函数 |

**研究实验室 (AlphaLab)**

`lab.py` (480行) 提供完整的研究工作流：

```
AlphaLab(save_path)
├── save_bar_data(bars)       → 保存K线到parquet
├── compute_component()       → 计算因子成分（可并行）
├── compute_dataset()         → 组合成训练数据集
├── train_model()             → 训练模型（Lasso/LGB/MLP/RF）
├── compute_signal()          → 生成交易信号
├── load_component()          → 加载已有因子成分
├── load_dataset()            → 加载已有数据集
├── load_model()              → 加载已有模型
└── load_signal()             → 加载已有信号
```

**ML模型套件**

| 模型 | 文件 | 特点 |
|------|------|------|
| Lasso | `model/models/lasso_model.py` | 线性模型，可解释性强，适合因子选择 |
| LightGBM | `model/models/lgb_model.py` | 树模型，梯度提升，精度通常高于RF |
| MLP | `model/models/mlp_model.py` | 神经网络，可捕捉非线性关系 |
| RF | （vnpy默认RandomForest） | 但我们用的是sklearn RF，非vnpy封装 |

**策略回测引擎**

`strategy/backtesting.py` (944行) 完整回测引擎：
- 多股票持仓管理
- 佣金/滑点/印花税建模
- 交易费用计算
- 参数优化（含遗传算法）
- Plotly可视化报告
- 收益率/夏普/最大回撤/Alpha/Beta等指标

附带一个实盘示例策略 `strategies/equity_demo_strategy.py`（101行）：
- top_k持仓上限、n_drop轮出数量、min_days最短持有期
- cash_ratio仓位利用率、commission费率参数
- on_bars/on_trade回调驱动

**数据处理管线**

`dataset/processor.py` 提供标准因子处理：
- `process_drop_na` — 删除缺失值
- `process_fill_na` — 填充缺失值
- `process_cs_norm` — 横截面归一化（z-score）
- `process_robust_zscore_norm` — 鲁棒z-score（去极值）
- `process_cs_rank_norm` — 横截面rank归一化
- `process_replace_inf` — 无穷值替换
- `process_ts_norm` — 时序归一化
- `process_cs_fill_na` — 横截面填充
- `process_drop_feature` — 删除指定因子

我们已经在 `factor_engine.py` 中实现了 `winsorize_mad()` 去极值（ad4aefa），但vnpy的 `process_robust_zscore_norm` 已内含此能力。

### 2.2 事件驱动交易引擎

位于 `lynx_vnpy/trader/engine.py`（838行），完整的交易主引擎：

**MainEngine 核心能力**

```
MainEngine(event_engine)
├── add_gateway()           → 注册券商网关
├── connect_gateway()       → 连接交易接口
├── subscribe()             → 订阅行情
├── send_order()            → 发送订单
├── cancel_order()          → 撤销订单
├── load_position()         → 加载持仓
├── load_account()          → 加载账户
├── load_contract()         → 加载合约
└── ...
```

**对象模型** (`trader/object.py`)：
- `TickData` — 逐笔行情
- `BarData` — K线数据（支持任意周期）
- `OrderData` — 订单
- `TradeData` — 成交
- `PositionData` — 持仓
- `AccountData` — 账户
- `ContractData` — 合约信息

**网关接口** (`trader/gateway.py`)：
- `BaseGateway` — 券商网关基类
- `on_tick/on_trade/on_order/on_position` — 事件回调

**事件引擎** (`event/engine.py`, 245行)：
- 事件驱动的核心基础设施
- `Event`/`EVENT_TICK`/`EVENT_ORDER`/`EVENT_TRADE`/`EVENT_POSITION` 等
- 支持异步事件分发

**参数优化** (`trader/optimize.py`, 250行)：
- 网格搜索
- 遗传算法优化（基于DEAP库）
- 多进程并行计算

### 2.3 其他基础设施

| 模块 | 文件 | 功能 |
|------|------|------|
| `chart/` | item/axis/manager/base/widget | K线图表控件（PyQtGraph） |
| `rpc/` | client/server/common | 远程过程调用，支持分布式部署 |
| `trader/ui/` | mainwindow/widget/qt | Qt5图形界面（主窗口/控件集） |
| `trader/setting.py` | - | 全局配置管理 |
| `trader/locale/` | build_hook | 国际化支持 |

---

## 三、当前与 vnpy 的差距分析

### 3.1 因子开发效率

我们现在加一个因子，需要：
1. 打开 `lynx_signal.py`
2. 在 `compute_features()` 函数中加几行pandas计算
3. 把新列名加入特征列表
4. 检查nan值处理
5. 重新训练所有10只股票的RF模型
6. 重新OOS验证

vnpy的AlphaDataset方式：
1. 在因子类中写一行表达式：`self.add_feature("my_factor", "公式")`
2. 因子自动展开、自动加入数据集
3. `AlphaLab.train_model()` 自动重训
4. 因子回测/IC分析自动出报告

**效率差距**: 手写15个指标约需60行pandas代码。如果改用vnpy的Alpha158，光是K线形态类有10个因子、时序窗口有5个，共50个时序统计因子——声明式一行一个，约15行代码就有50个因子。

### 3.2 模型多样性

目前只用 RandomForest（sklearn）。vnpy 内置了 Lasso/LGB/MLP 模板，换模型只需要改一行：

```python
# 当前
from sklearn.ensemble import RandomForestClassifier
model = RandomForestClassifier(...)

# vnpy方式
from lynx_vnpy.alpha.model.models.lgb_model import LGBModel
model = LGBModel()
lab.train_model(dataset, model, segment)
```

### 3.3 回测深度

vnpy的 `BacktestingEngine`（944行）支持：
- 多股票并行持仓
- 真实交易成本建模（佣金/印花税/滑点）
- 复权价格、涨跌停处理
- 夏普/最大回撤/信息比等指标
- Plotly可视化

我们的 `backtest.py`（1127行）当前是简版T+1方向匹配，不建模交易成本、不管理多股票仓位。两者各有所长——vnpy的回测适合策略级模拟，我们的适合融合系统准确性评估。

### 3.4 交易执行

这是最大的差距，但也是**故意的不做**：

| 维度 | vnpy | 我们 |
|------|------|------|
| 订单执行 | 完整（经纪商网关） | 无（决策信号止步） |
| 持仓管理 | 实时仓位跟踪 | 无 |
| 券商连接 | CTP/IB等标准接口 | 无 |
| 账户管理 | 资金/保证金/风控 | 无 |

**这是设计选择，不是缺陷**。Aistock_vnpy_Trading 的定位是"信号融合决策平台"，不是"自动化交易系统"。实盘执行需要券商接口、资金管理、合规风控，超出了当前项目的范围。

---

## 四、可拓展方向（优先级评估）

### P1 — 因子研究升级

| 方向 | 难度 | 收益 | 说明 |
|------|------|------|------|
| 用Alpha158替代手写15个TA指标 | 中 | 高 | 259个因子→横截面归一化→模型对比，用现成框架可零成本获得因子池 |
| RF→LGB模型切换测试 | 低 | 中 | 换一行代码即可对比，且因子研究中的ICIR权重暗示size_factor等有预测力 |
| 多模型集成（RF+LGB+MLP） | 中 | 高 | vnpy内置三种模型模板，多模型投票可能提升OOS稳定性 |

**现状**: 手写的15个TA指标对于10只股票的规模目前够用。但如果未来要扩大股票池或验证因子有效性，Alpha158是现成的研究平台。

### P2 — 回测增强

| 方向 | 难度 | 收益 | 说明 |
|------|------|------|------|
| 引入交易成本建模 | 低 | 中 | backtest.py追加commission/slippage不影响融合逻辑 |
| vnpy BacktestingEngine复用 | 高 | 中 | 需要适配Polars数据结构，当前backtest.py用SQLite方向匹配是不同范式 |

### P3 — 交易执行接入

**不推荐在当前项目范围内做**。理由：
- 需要券商接口（CTP/IB），开户+调试+合规成本高
- 需要资金管理（仓位分配、风险敞口计算）
- 需要执行算法（拆单、TWAP/VWAP）
- 与"零侵入、信号融合"的项目定位冲突

---

## 五、文件结构速查

```
systems/lynx_vnpy/
│
├── lynx_signal.py              # 599行 — 当前使用的信号生成主程序（自建，不调vnpy）
│
├── lynx_vnpy/                  # vnpy 完整库代码（保留未用）
│   ├── alpha/                  # Alpha因子研究框架
│   │   ├── lab.py              #   480行 — 研究实验室
│   │   ├── dataset/            #   因子数据集
│   │   │   ├── datasets/
│   │   │   │   ├── alpha_101.py   # WorldQuant 101因子
│   │   │   │   └── alpha_158.py   # Qlib 158因子
│   │   │   ├── template.py     #   AlphaDataset基类
│   │   │   ├── processor.py    #   数据处理管线
│   │   │   ├── ta_function.py  #   技术分析函数
│   │   │   ├── cs_function.py  #   横截面函数
│   │   │   ├── ts_function.py  #   时序函数
│   │   │   ├── math_function.py#   数学函数
│   │   │   └── utility.py     #   工具函数
│   │   ├── model/              #   ML模型
│   │   │   ├── template.py     #   模型基类
│   │   │   └── models/         #   Lasso / LGB / MLP 实现
│   │   ├── strategy/           #   策略回测
│   │   │   ├── template.py     #   策略基类
│   │   │   ├── backtesting.py  #   944行 — 回测引擎
│   │   │   └── strategies/     #   示例策略
│   │   └── logger.py           #   日志
│   │
│   ├── trader/                 # 交易引擎
│   │   ├── engine.py           #   838行 — MainEngine
│   │   ├── gateway.py          #   网关基类
│   │   ├── object.py           #   数据对象模型
│   │   ├── datafeed.py         #   数据源接口
│   │   ├── optimize.py         #   250行 — 参数优化（网格+遗传算法）
│   │   ├── setting.py          #   配置
│   │   └── ui/                 #   Qt5图形界面
│   │
│   ├── event/engine.py         # 245行 — 事件驱动引擎
│   ├── rpc/                    # 远程过程调用
│   └── chart/                  # K线图表
│
└── models/                     # 10只股票的RF模型.pkl + scaler.pkl
    ├── 001390_model.pkl
    ├── 001390_scaler.pkl
    ├── ...
    └── 603557_scaler.pkl
```

---

## 六、融合场景下的利用策略（分阶段方案）

从融合系统实际需求出发，vnpy的利用不应是全盘迁移或追求代码复用率，而是**定向提升ly信号质量**，间接提升30%权重的融合输入。

### 6.1 利用价值排序

```
                    对融合的贡献
                    ────────────
因子系统化          最高 — 直接提升ly信号质量，影响30%融合权重
模型多样性           中高 — 多模型集成可能缓解OOS过拟合(46.7%)
回测研究闭环         中 — 离线分析工具，不做代码改动也有价值
独立因子信号通道     中低 — 需改变融合引擎结构
交易执行             无 — 与融合系统定位冲突
```

### 6.2 四阶段方案

#### Phase 0 — 诊断（离线研究，不改生产代码）

**目标**: 用vnpy的因子研究能力回答"提升ly信号是否有意义"

**步骤**:
1. 将现有OHLCV数据转换为vnpy AlphaDataset所需的Polars格式
2. 用Alpha158计算158个因子
3. 运行IC分析（vnpy内置alphalens集成）
4. 对比现有15个TA指标与158个因子的IC分布
5. 产出: `factor-comparison-report.md`，数据驱动的决策依据

**期望产出**: 如果因子IC显著→推进Phase 1；如果IC接近噪音→ly维持现状

**风险**: 零（离线分析，不碰生产代码）

**工期**: ~2周

#### Phase 1 — 因子替换（改生产代码）

**目标**: 用vnpy的因子引擎替换手写 `compute_features()`，提升ly信号质量

**改动范围**:
- 修改 `lynx_signal.py` 的 `compute_features()` 函数
- 数据流: Sina API → Polars DataFrame → Alpha158表达式计算(158因子) → pandas转换 → 现有RF训练/推理
- 仅特征层变化，模型、推送、融合引擎代码不变

**风险**:
- 需要添加Polars运行时依赖
- Polars→pandas转换有性能开销（10只股票可忽略）
- 可能回归（158个因子不一定比15个精心挑选的指标好，Phase 0决定是否推进）

**工期**: 1-2天

#### Phase 2 — 多模型集成（可选）

**目标**: 用vnpy的LGB/MLP模型模板替代或并行RF

**改动**:
- 引入 `LGBModel` 与RF并行
- RF+LGB投票输出概率
- 对比单一RF的OOS准确率

**前提**: Phase 1确认因子升级有效

#### Phase 3 — 独立因子信号通道（远期）

**目标**: vnpy因子引擎作为独立信号源接入融合系统

**路径**:
1. 新建 `services/vnpy_factor_service.py`（类似 `ml_factor_service.py` 模式）
2. 每5分钟用vnpy因子引擎计算全因子得分，写 `alpha_signal.json` 到文件交换区
3. realtime_fusion 以额外信号或ly子信号形式接入

**前提**: Phase 1+2均确认有效

### 6.3 关键决策点

```
[Phase 0 IC分析]
    ├─ 因子有预测力 → Phase 1（因子替换）
    │                    ├─ OOS提升 → Phase 2（多模型集成）
    │                    │                ├─ 有效 → Phase 3（独立通道）
    │                    │                └─ 无效 → 停止，RF保留
    │                    └─ OOS未提升 → 停止，恢复手写15指标
    │
    └─ 因子无预测力 → 维持现状，考虑降低ly权重
```

### 6.4 为什么不建议的其他方向

| 方向 | 不建议理由 |
|------|-----------|
| 全量vnpy alpha pipeline替代lynx_signal | 改造大、风险高，一个脱轨的Polars表达式可能阻塞整个日终融合 |
| 直接用vnpy BacktestingEngine替代backtest.py | 回测范式不同（策略回测vs方向匹配），强行替换收益有限 |
| 引入trade引擎 | 与融合系统"无执行层"定位冲突 |
| 接入vnpy GUI | 无实用价值 |

### 6.5 总结

```
当前: lynx_signal.py 自建15TA指标+RF → 融合30%权重
                              ↓
Phase 0: 用vnpy因子框架做IC诊断 → 决定是否推进
Phase 1: Alpha158替代手写特征 → 提升因子质量
Phase 2: LGB+RF多模型集成 → 提升预测稳定性
Phase 3: 独立因子信号 → 扩展融合输入
```

```
                    我们现在             vnpy已提供
                    ─────────           ──────────
因子开发            手写pandas           Alpha101+158 + 表达式系统
模型                sklearn RF          Lasso / LGB / MLP / RF 模板
数据处理            手动计算             processor管线（归一化/去极值/rank）
回测                T+1方向匹配         多股票回测 + 成本建模 + 可视化
交易执行            无                  券商网关 + 订单管理 + 持仓跟踪
事件驱动            无                  EventEngine
分布式              无                  RPC
GUI                无                  Qt5界面
```

**核心结论**: 
1. 当前自建的 `lynx_signal.py` 对于10只股票的规模够用，没有迫切需要迁移到vnpy框架
2. 如果要做因子研究升级（扩大候选因子池、验证因子有效性），vnpy的Alpha101+Alpha158是现成的零成本起点
3. 交易执行层面（券商/订单/仓位）暂不需要引入，但代码库就躺在目录里，有需要随时可用
