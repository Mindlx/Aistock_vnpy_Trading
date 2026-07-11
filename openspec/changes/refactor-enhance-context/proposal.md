## Why

`_enhance_context`（圈复杂度 87，390 行）是平铺式的上下文增强函数，包含 10+ 个独立的数据源注入块。所有逻辑在一个方法中，难以维护和测试。

## What Changes

1. 提取信号加载部分为 `_load_agent_signals`（圈复杂度 52 的 `_load_ly_signals` 已在别处，此处指上下文增强中的 LY/ML 信号读取）
2. 将实时行情/筹码/趋势的字典构建提取为独立步骤
3. `_enhance_context` 缩减为编排 ~100 行

## Impact

- `pipeline.py` — `_enhance_context` 从 390 行缩减 ~150 行
