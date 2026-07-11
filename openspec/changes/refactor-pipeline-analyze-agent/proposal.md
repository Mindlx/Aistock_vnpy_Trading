## Why

`StockAnalysisPipeline._analyze_with_agent`（圈复杂度 105，525 行）是 MindLynx 子系统的核心分析编排函数，调用了 34 个不同的服务和方法。所有逻辑堆叠在一个方法中：数据准备、子 agent 调度、LLM 调用、结果解析、仓位计算、持久化、日志。认知负载过高，修改任一环节都有破坏其他逻辑的风险。

## What Changes

1. 将 `_analyze_with_agent` 拆分为 4 个独立的步骤方法：
   - 上下文准备（市场数据/新闻/知识库）
   - 子 agent 执行与 LLM 调用
   - 结果解析与决策映射
   - 持久化与通知

2. 保持 `_analyze_with_agent` 作为编排入口，内部委托 4 个步骤
3. 不改动返回格式、外部接口

## Capabilities

### New Capabilities
- `agent-orchestration`: 子 agent 分析编排，拆分 `_analyze_with_agent` 为多步骤

### Modified Capabilities
- 无（纯重构，不改接口）

## Impact

- `systems/MindLynx-Aistock/src/core/pipeline.py` — `_analyze_with_agent` 从 525 行减为 ~60 行编排，新增 4 个私有方法
- 所有外部调用者不受影响
