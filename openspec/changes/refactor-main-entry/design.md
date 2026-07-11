## Context

`main()`（534 行，复杂度 81）包含多种运行模式（回测/监控/复盘/调度/单次），按 mode if-branch 组织。

## Decisions

拆分 3 个大块为辅助函数：
1. `_init_main_environment(args)` → 初始化日志/配置/验证
2. `_start_web_if_enabled(config, args)` → Web 服务器启动
3. `_run_schedule_mode(config, args, stock_codes)` → 定时任务（含后台任务/整点分析/周情报调度）

每个模式分支自身已足够内聚（有 return），不作为提取目标。
