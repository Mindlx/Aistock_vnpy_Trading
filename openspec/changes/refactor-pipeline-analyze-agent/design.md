## Context

`_analyze_with_agent`（圈复杂度 105，525 行）包含 5 个可分离的阶段，所有逻辑耦合在一个方法中。输入参数 8 个，调用 34 个外部服务。

## Goals / Non-Goals

**Goals:**
- 拆分为 5 个私有方法，每个聚焦一个阶段
- `_analyze_with_agent` 缩减为 ~30 行编排逻辑
- 不改动任何输入/输出签名

**Non-Goals:**
- 不改动 `AnalysisResult` 数据结构
- 不改动外部服务调用方式

## Decisions

**拆分方案：**

| 新方法 | 来源行 | 职责 | 预估行数 |
|--------|--------|------|---------|
| `_build_agent_context` | 1314–1563 | 组装 initial_context（15 个数据源） | ~200 |
| `_build_agent_message` | 1564–1643 | 构建 LLM 消息（prompt + 提示词） | ~80 |
| `_process_agent_result` | 1647–1698 | 转换结果 + 因子锚定 + 完整性 | ~50 |
| `_post_process_agent` | 1699+ | 持久化 + 通知 | ~100 |

## Risks / Trade-offs

- 数据新鲜度/相关性提示等 prompt 文本量大但逻辑简单，拆出后更易维护
- 各阶段通过 `initial_context` 和局部变量传递数据，不需要引入新类
