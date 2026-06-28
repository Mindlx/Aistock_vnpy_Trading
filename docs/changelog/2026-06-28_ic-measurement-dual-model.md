# LY 双模型 IC 加权修复 + 全面代码审查 — 2026-06-28

## 背景

前期 c1skill 分析指出 `predict_ensemble()` 对 RF 和 LGB 使用算术平均可能导致信号互相稀释（问题 D），判决为"先测量"。测量后确认算术平均为净伤害，执行修复。

## 修复范围

### 双模型 IC 加权修复

| 改动 | 位置 | 说明 |
|------|------|------|
| 等权→IC加权 | `systems/lynx_vnpy/lynx_signal.py:506-524` | LGB_w=0.91, RF_w=0.09 |
| IC 测量脚本 | `scripts/measure_dual_model_ic.py` | 170 样本，5/29~6/26 |
| RF 模型诊断 | `scripts/diagnose_rf_model.py` | 当前 vs 上游模型对比 |

**IC 对比：**
| 指标 | 算术平均(旧) | IC加权(新) | 改善 |
|------|:----------:|:----------:|:----:|
| Pearson IC | 0.144 | **0.218** | +51% |
| 方向准确率 | 53.5% | **65.3%** | +11.8pp |

### 全面代码审查（3 步骤）

| 步骤 | 内容 | 结果 |
|------|------|------|
| 1. 审查 | 自动+手动扫描 LY/ML/Fusion/AT 四区域 | 找到 3 处修复 + 2 处确认不修 |
| 2. c1skill 论证 | 5 阶段论证（Stage 0-7） | 全部批准 |
| 3. 执行 | 5 处 except→带日志 | 编译通过 ✅ |

**修复的 except-pass 问题：**

| 位置 | 问题 | 修复 |
|------|------|------|
| `services/data_warehouse/fetchers.py:560` | bare `except: pass` | → 特定异常 + logger.warning |
| `src/mind_agent_wrapper.py:86,149` | `except ImportError: pass` | → logger.debug |
| `src/mind_agent_wrapper.py:551,564,574` | `except (ValueError, TypeError): pass` | → logger.debug |

### 子代理 GPU 负载分配优化

| 配置 | 文件 | 说明 |
|------|------|------|
| GPU0(27B): explore, quick, unspecified-low, writing | `oh-my-openagent.json` | 高频/轻量走 27B |
| GPU1(35B): hephaestus, sisyphus-junior, visual-engineering, artistry | `oh-my-openagent.json` | 高强度走 35B |
| DeepSeek 云: 其余 agent 不变 | `oh-my-openagent.json` | API 调用不动 |

### 自动代码审查系统

| 项目 | 位置 | 说明 |
|------|------|------|
| 审查脚本 | `scripts/auto_code_review.sh` | GPU 空闲(<30%)触发 |
| systemd service | `config/systemd/aistock-code-review.service` | oneshot 执行 |
| systemd timer | `config/systemd/aistock-code-review.timer` | 每 15 分钟检查 |

## 文件变更清单

```
M  systems/lynx_vnpy/lynx_signal.py           # 双模型 IC 加权
M  src/mind_agent_wrapper.py                   # 3 处 except 加日志
M  services/data_warehouse/fetchers.py         # 1 处 except 加日志
A  scripts/measure_dual_model_ic.py             # IC 测量脚本
A  scripts/diagnose_rf_model.py                 # RF 模型诊断
A  scripts/auto_code_review.sh                  # 自动代码审查
A  config/systemd/aistock-code-review.service   # systemd service
A  config/systemd/aistock-code-review.timer     # systemd 定时器
A  data/ic_measurement.csv                      # IC 原始数据
A  docs/changelog/2026-06-28_ic-measurement-dual-model.md  # 本文
```

## 遗留

- 双模型 IC 滚动监测脚本（Phase 2）未实施
- RF 上游模型(5/29)在部分股票上表现优于当前模型，后续可评估替换
