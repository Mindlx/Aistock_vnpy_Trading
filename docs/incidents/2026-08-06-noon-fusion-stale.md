# 事故报告：午盘大盘复盘注入 TA 独占融合信号（"强烈看多"泛滥）

> **日期**: 2026-08-06
> **严重程度**: HIGH — 午盘大盘复盘自选股建议出现大量"强烈看多"信号，决策信号失真
> **根因**: 融合文件交换区无新鲜度机制，午盘复盘读取了 ML 整点分析（11:00）之前由 TA timer 生成的融合文件
> **修复**: 11:30 融合刷新 timer + `generated_by` 来源标记 + 读取端新鲜度校验

---

## 现象

8/6 午盘大盘复盘（11:51 生成）自选股建议段显示 6 只股票"信号强烈看多"
（600372/605368/000592/601801/300676/000060），与全天报告（15:49，读 14:41 混合版）
"融合信号+1.70看多" 形成鲜明反差——同一只 600372 中航机载，午盘"强烈看多"，全天"看多"。

## 时间线（8/6）

| 时间 | 事件 |
|:----:|:------|
| 10:10 | TA.timer 启动 `run_daily.py --run-ta`（18 只 LLM 推理，约 18 分钟）|
| 10:28 | TA 完成后 run_daily 继续执行融合 → 写入 `fusion_2026-08-06.json`（**此时 ML 整点分析 11:00 尚未运行**）|
| 10:28 | ML 当日 analysis_history 无记录 → `mindlynx_valid=False` 全部 → `compute_adjusted_weights` 把 TA 权重重归一化为 100% → `fusion_score = TA score` |
| 11:00 | ML 整点分析（scheduler，16 只），11:03 首条落库，约 11:10-11:12 完成 |
| 11:45 | 大盘复盘 `_load_stock_pool_data` glob `fusion_{today}*.json` → 读到 10:28 的 TA 独占文件 |
| 11:51 | 午盘报告显示 6 只"强烈看多"（TA Buy=3.0 的股票，与 at_signal.json 完全吻合）|
| 12:12/14:41 | fusion-eval.timer 覆盖写混合融合文件 |
| 15:49 | 全天报告读 14:41 混合版 → 正常 |

## 根因分析

### 架构缺口（偶然，非刻意）

融合文件交换区 `data/fusion_output/fusion_{date}.json` 是当日融合决策的单一事实源，
有**三个写入方**（TA timer 10:28、fusion-eval 12:12/14:41、日终 18:00），
但**读取方（大盘复盘）对文件的新鲜度/覆盖度没有任何校验**。这是"只约束写、不约束读"的缺口。

核心矛盾：`fusion_{date}.json` 固定文件名 + in-place 覆盖，消费方无法感知文件是哪个时序阶段写的。
10:28 文件本身"对早上是对的"（当时 TA 是唯一有新鲜数据的系统），
错在 11:45 的消费者仍在使用它——**信息集严格小于复盘时刻可获得的信息集**。

### 附带缺陷

- `run_daily.py:358/364` `_get_stock_accuracy_discount` 引用未定义的全局 `logger`
  （`from src.logger import FusionLogger` 只导入从未实例化）→ 每次 fusion-eval 写
  stock_analysis.db 桥接失败 "name 'logger' is not defined"（与 2026-07 的
  notification.py docstring import bug 同类）。
- `scripts/deploy-systemd.sh` 因 `config/systemd/aistock-ic-monitor.service` 在磁盘上
  被替换为软链（指向 `~/.config/systemd/user/` 同名文件），`cp` 报"同一文件" +
  `set -euo pipefail` 中止整个部署 → **systemd 配置变更一直静默未生效**。

## 修复方案（c1skill 8 阶段论证后定案）

| Phase | 改动 | 文件 | 风险 |
|:-----:|:-----|:-----|:----:|
| 1 | 增加 `OnCalendar=Mon..Fri 11:30:00`（ML 整点分析 11:10 完成后刷新融合）| `config/systemd/Aistock_vnpy_Trading-fusion-eval.timer` | 零风险 |
| 2 | 融合 JSON 增加 `generated_by` 来源标记（ta_pre_ml / fusion_eval / scheduled）| `scripts/run_daily.py` | 低 |
| 3 | 读取端新鲜度校验：`generated_at` < 今日 ML full 最新时间 → 标注告警 | `systems/MindLynx-Aistock/src/market_analyzer.py` | 低 |
| 4 | 修复未定义 `logger` | `scripts/run_daily.py` | 零风险 |
| 5 | 修复 deploy 脚本 cp 同文件中止 | `scripts/deploy-systemd.sh` | 零风险 |

### 证据（决定性实验）

ML 完成后重跑 `run_daily.py --fusion-only`（用 8/6 当日数据）：

```
600372 中航机载: mindlynx_valid=true mind=1.00 ta=3.00 → fusion=1.70 看多
ML 有效覆盖: 16/18（仅 华大基因/*ST网达 不在 ML 池保持 TA-only，符合预期）
```

对比午盘事故时的 TA 独占：同一只股票 fusion=3.0 强烈看多 → 混合后 1.70 看多，失真消除。

## 验证

- 新鲜度校验 4 场景全通过：ta_pre_ml 标记告警 / 新鲜文件静默 / 过期文件告警 / 旧格式静默
- 真实实例：`_get_latest_ml_analysis_time` 返回当日最新 full=14:11，18:00 真实文件无告警
- `pytest tests/` 135 passed，2 failed 为**预存失败**（clean HEAD 亦失败，与本次无关）
- timer 已部署并加载：`systemctl --user list-timers` 显示 2026-08-07 11:30 待触发

## 经验教训

1. **文件交换区的写入方时序必须被消费方感知**——固定文件名 + 覆盖写 = 消费方无法判断新鲜度。
2. **"午盘读 TA 独占"并非 LLM 编造，而是数据时序错配**——LLM 如实转述了陈旧融合文件。
3. `set -euo pipefail` 的部署脚本对软链/同文件 cp 无防护，会静默中止后续所有部署——部署后必须验证文件实际更新。
