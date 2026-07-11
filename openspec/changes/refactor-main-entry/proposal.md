## Why

`main()`（534 行，圈复杂度 81）是 MindLynx 子系统的入口函数，承担了参数解析、环境初始化、Web 服务启动、分析调度、进程管理等职责。所有逻辑耦合在一个函数中，增加新调度模式或启动选项时风险高。

## What Changes

1. 提取 Web 服务启动段为 `_start_web_server(config, args)` 辅助函数
2. 提取分析调度主循环为 `_run_analysis_loop(config, args)` 辅助函数
3. 提取环境初始化段（日志/配置/验证）为 `_init_environment(args)` 辅助函数
4. `main()` 缩减为依次调用 3 个阶段，约 100 行

## Impact

- `main.py` — `main()` 从 534 行缩减 ~100 行编排
- 新增 3 个模块级辅助函数
- 不改动任何外部接口行为
