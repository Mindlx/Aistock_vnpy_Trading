# c1skill 论证：准实时融合架构方案

> **审阅目标**: `docs/research/realtime-fusion-design.md`
> **方法论**: c1skill Stage 0 + Stage 1 + Stage 2 + Stage 4 + Stage 5

---

## Stage 0 — 原架构理解

### 当前融合时序

```
ly (T+1, 固定全天)        ───┐
ml (日终报告, 15:00后生成) ───┤──→ fusion.timer (15:30)  →  企业微信推送
at (16:00运行, 次日可用)   ───┘                              (昨日数据stale)
```

**关键问题**: 盘中 ml 的实时监控数据被浪费——它只触发自己的推送，不参与融合。at 的数据永远是昨日 stale 的。

### 方案设计意图

方案试图将融合从 T+1 提升到分钟级，利用：
- ml 的 15 分钟级盘中速报（方向性信号）
- at 的 50 分钟级多 Agent 更新（辩论共识）
- ly 的 T+1 固定信号（基线）

---

## Stage 1 — 声明与事实对照

| 方案声明 | 系统事实 | 判定 |
|---------|---------|------|
| ly 盘中不变 | RandomForest 模型使用日线特征，盘中无新特征输入 | ✅ **准确** |
| ml 有 15 分钟级盘中速报 | `--realtime-monitor-daemon` 运行中，但监测的是价格异动，非方向性信号 | ⚠️ **需验证** |
| at 50 分钟可完成一轮 | 10只×3-5min ≈ 40min，延时 5s 间隔，总 ~45min | ✅ **合理** |
| 文件交换零成本 | JSON 读写微秒级，无网络开销 | ✅ **准确** |
| 融合计算微秒级 | 一次加权平均 ≈ 几十条 CPU 指令 | ✅ **准确** |
| 每 5 分钟扫描一次足够 | 最快信号源 ml 是 15 分钟级，5 分钟扫描保证不丢 | ✅ **合理** |

### 需验证的关键假设

| 假设 | 验证方法 |
|------|---------|
| ml 的盘中速报真正包含可参与融合的方向性信号 | 查看 ml monitor daemon 的实际输出内容 |
| at 日间多次运行不超出 API 额度 | DeepSeek 价格：单次 ~100 次调用，3-4 次/日 = 300-400 次 |

---

## Stage 2 — 代码级证据验证

### 2.1 ml monitor daemon 实际输出什么？

验证 ml 实时监控的内容是否适合融合：

<analysis>
The claim is that ml's `--realtime-monitor-daemon` provides "15分钟级方向性信号" suitable for fusion. Let me check what the monitor daemon actually produces.
</analysis>

```bash
# ml 实时监控进程已运行 (from systemctl)
ExecStart=.../main.py --realtime-monitor-daemon
```

从 ml 的 help 输出看：
```
--realtime-monitor    启动盘中实时监控服务 (WebSocket + ATR止损 + 量价异动)
--event-monitor       启动事件驱动分析服务 (公告+互动易监控) 
```

**这些是风控事件，不是方向性信号**。ATR 止损是"跌破某个价位就卖出"，量价异动是"突然放量"——这些适合单独推送，但融合进 ly+at 的投票没有意义。

**方案的盘中速报应该来自 ml scheduler 的周期性分析**（而非 monitor daemon）。但 scheduler 的默认定时是日终运行（收盘后），盘中分析需要额外配置 `--daily-intel --daily-intel-slot midday` 这类参数。

### 2.2 at 日间多次运行的技术可行性

```python
# mind_agent_wrapper.py
def run_batch(self, stock_codes, trade_date, concurrency=1, delay_between=5):
    for i, code in enumerate(stock_codes):
        # TradingAgentsGraph.propagate()  →  LLM 8-12次调用
        final_state, signal = self._ta.propagate(yf_ticker, trade_date)
```

每次 `propagate()` 调用触发 8-12 次 LLM API 调用。10 只股票 = 80-120 次。3-4 次/日 = 240-480 次。

**DeepSeek API 成本估算**:
- 输入 token: 每只 ~4000 tokens × 10 次调用 × 10 只 = ~400K tokens
- 输出 token: 每只 ~500 tokens × 10 次调用 × 10 只 = ~50K tokens
- 总计/轮: ~450K tokens
- 3 轮/日: ~1.35M tokens
- 按 DeepSeek 定价: ~$0.5/1M tokens → ~$0.68/日
- **月成本 ~$14** — 完全可行

### 2.3 文件交换的健壮性

```python
# 写入时如使用 write+rename 可避免脏读
# 方案中未明确要求，但建议：
temp = path + '.tmp'
with open(temp, 'w') as f: json.dump(data, f)
os.rename(temp, path)  # 原子操作
```

**需在方案中补充**: 要求写入者使用 `write + rename` 原子写入模式。

---

## Stage 4 — 反方论据

### 4.1 方案可能高估的内容

#### 声明 1: "ml 提供 15 分钟级方向性信号"

**反方论据**: `--realtime-monitor-daemon` 的实际输出是**风控事件**（价格异动、ATR 止损），不是方向性的趋势判断信号。这些事件无法被 ly（T+1 模型）和 at（50 分钟前）投票。

如果需要 ml 提供 15 分钟级的方向性信号，需要：
- 要么利用 ml scheduler 的 `--daily-intel` 功能（但它是午间/晚间/盘前时段，不完全是 15 分钟级）
- 要么新增一个轻量级盘中分析脚本，每 15 分钟读取最新行情+快速 LLM 判断
- **前者已有但频率不够，后者需要新开发**

| 立场 | 证据 |
|------|------|
| 方案声称 | ml monitor daemon 提供 15 分钟级方向性信号 |
| 反方证据 | monitor 输出的是风控事件（ATR/量价异动），非方向性判断 |

**认定**: ⚠️ 方案需要明确 ml 的方向性信号来源，不能假定 monitor daemon 的输出适合融合。

#### 声明 2: "每 5 分钟扫描足够捕获所有变化"

**反方论据**: 如果多个信号源在 5 分钟内先后更新，扫描只能捕获最后一次变化。比如 ml 在 10:02 更新，at 在 10:04 更新，10:05 的扫描只看到两者都变了，会触发融合——但中间 ml 的单独变化被漏掉。

这是**设计选择**而非 bug——我们只关心"融合后的最新状态"，不关心中间状态。但如果用户希望"每次信号变化都推送"，5 分钟扫描就不够。

| 立场 | 证据 |
|------|------|
| 方案声称 | 5 分钟扫描足够 |
| 反方证据 | 可能丢失信号源之间的中间状态 |

**认定**: 🟢 LOW——可接受。融合关注的是最新状态，非历史轨迹。

### 4.2 方案可能低估的内容

#### 声明 3: "盘中融合的价值"

**反方论据**: 方案低估了 ly 的局限性。ly 是 T+1 固定信号，如果 ly 当日方向错误（比如模型看多但实际大跌），盘中融合会被 ly 的固定信号拖累。在没有 ly 实时修正能力的情况下，盘中融合的质量受限于最弱的信号源。

**但反过来看**: 如果 ml 和 at 都强烈看空而 ly 看多，融合会输出偏空（因为 0.35+0.35=0.7 > 0.3 的权重）。这已经比纯 ly 信号好——盘中融合的价值正在于此。

**认定**: 🟡 MEDIUM——盘中融合在信号分歧时最有价值，一致时与日终融合无差异。

#### 声明 4: "保留日终融合作为权威版本"

**反方论据**: 两个版本（盘中 vs 日终）可能导致用户困惑——早上收到"谨慎看多"，下午变成"看空"，收盘又变成"中性"。用户该信哪个？

**建议**: 盘中融合推送时明确标注"盘中参考"，日终融合标注"日终确认"。并在推送消息中显示数据时效：

```
📊 盘中融合速报 (参考)
皖新传媒: 谨慎看多
📡 ly:昨日  ml:3分钟前  at:12分钟前
```

vs

```
📊 日终融合报告 (权威)
皖新传媒: 中性/持有
📡 基于三系统今日完整数据
```

---

## Stage 5 — 修复方案评估

### 5.1 方案中的问题

| # | 问题 | 严重程度 | 建议修正 |
|---|------|---------|---------|
| 1 | ml 方向性信号来源不明确 | 🔴 **HIGH** | 明确用 `--daily-intel --slot midday` 还是新增 15 分钟轮询脚本 |
| 2 | 文件写入未指定原子操作 | 🟡 MEDIUM | 要求所有写入者使用 write+rename |
| 3 | 盘中 vs 日终结果冲突处理 | 🟡 MEDIUM | 明确版本优先级和标注方式 |
| 4 | 首次启动时 ly 信号未初始化 | 🟢 LOW | 日终融合运行后自动生成 ly_signal.json |

### 5.2 建议的修正

#### 修正 1: 明确 ml 方向性信号来源

ml 需要两个独立的数据通道，不能混用：

| 通道 | 来源 | 用途 | 更新频率 |
|------|------|------|---------|
| 风控事件 | `--realtime-monitor-daemon` | 独立推送，不参与融合 | 实时 |
| **方向性信号** | **新增 `scripts/ml_15min_signal.py`** | **参与融合** | **15 分钟** |

新增的 `scripts/ml_15min_signal.py`：

```python
"""
每 15 分钟运行一次:
1. 从 Sina API 获取最新行情
2. 计算技术指标 (RSI/MACD/ATR)
3. 轻量级趋势判断（非完整 LLM 分析）
4. 写入 data/realtime/ml_signal.json
"""
```

这样 ml 的方向性信号来源就是独立、可控的。不依赖 ml 主系统的 daemon。

#### 修正 2: 新增盘中融合定时器

```ini
# systemd: Aistock_vnpy_Trading-realtime-fusion.timer
[Timer]
OnCalendar=Mon..Fri 09:35:00
OnCalendar=Mon..Fri 09:40:00
...
OnCalendar=Mon..Fri 14:55:00
# 每 5 分钟一次，从 09:35 到 14:55
```

但这种写法太冗长。更好的方式：

```ini
[Timer]
OnCalendar=Mon..Fri 09:35/5:00
Persistent=false
```

systemd 的 `OnCalendar` 支持 `09:35/5:00` 表示从 09:35 开始每 5 分钟一次直到 14:55。**但需要验证 systemd 是否支持这种写法**。否则用多个 OnCalendar 行：

```ini
[Timer]
OnCalendar=Mon..Fri 09:35:00
OnCalendar=Mon..Fri 09:40:00
... 共 64 行
```

或者简单点：**用 Python 内部循环 + sleep**，直接作为 service 运行：

```ini
[Service]
Type=simple
ExecStart=.venv/bin/python src/realtime_fusion.py --daemon
Restart=always
```

`--daemon` 模式下自己维护 5 分钟循环，比 systemd 定时器更可控。

#### 修正 3: 信号变化阈值细化

当前方案: `|ΔL7| > 0.3` 才推送。

建议细化:
```python
# L7 范围是 [-3, +3]，0.3 相当于 5%
# 不同信号区间的推送阈值应有差异:
# 中性区 (±0.5): 变化 > 0.3 才推（避免中性区频繁跳动）
# 方向区 (|score| > 1.0): 变化 > 0.5 才推（方向已明确，微小变化不重要）
# 临界区 (0.5 < |score| < 1.0): 变化 > 0.2 就推（可能即将变方向）
```

### 5.3 修正后的方案

```
ml 15min方向性信号 ──→ data/realtime/ml_signal.json ──┐
at 50min           ──→ data/realtime/at_signal.json ──┤
ly T+1             ──→ data/realtime/ly_signal.json ──┤
                                                       │
                                            src/realtime_fusion.py
                                            (Type=simple, --daemon 自循环)
                                                       │
                                             变化 > 阈值?
                                               ↓
                                          企业微信推送
```

**实施顺序**:

| 优先级 | 任务 | 工作量 | 前提 |
|--------|------|--------|------|
| P0 | 新建 `scripts/ml_15min_signal.py` | 2h | — |
| P0 | 新建 `src/realtime_fusion.py` | 4h | — |
| P1 | ly/at 信号输出接入 | 1h | P0 |
| P2 | systemd realtime-fusion service | 1h | P0+P1 |
| P3 | TA 日间多次触发改造 | 1h | P1 |
| P4 | 阈值调优 + 测试 | 2h | P0-P3 |
