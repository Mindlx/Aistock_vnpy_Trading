## 1. 提取环境初始化

- [ ] 1.1 创建 `_init_main_environment(args)` — 日志/配置/验证
- [ ] 1.2 创建 `_start_web_if_enabled(config, args)` — Web 服务启动

## 2. 提取定时任务模式

- [ ] 2.1 创建 `_run_schedule_mode(config, args, stock_codes)` — 后台任务 + 整点分析 + 周情报

## 3. 简化 main

- [ ] 3.1 `main()` 缩减为阶段调用 + mode dispatch
- [ ] 3.2 语法验证
- [ ] 3.3 提交
