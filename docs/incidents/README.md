# 事故与故障留痕（incidents）

> 记录生产事故/异常事件：现象 → 根因 → 修复 → 验证 → 防护。
> 最后更新: 2026-10-07

| 日期 | 事件 | 文档 |
|------|------|------|
| 2026-10-03 | `analysis_history` 'fusion' 行污染（ML 回测口径循环） | `2026-10-03-fusion-row-contamination.md` |
| 2026-10-02 | 数据单位不一致（volume=股 / amount=元 + tushare 字段索引 bug） | `2026-10-02-data-units-alignment.md` |
| 2026-10-02 | 日期格式污染（capital_flows + ohlcv_cache 排序失真） | `2026-10-02-date-format-pollution.md` |
| 2026-08-08 | realtime-fusion 重启恢复 | `2026-08-08-realtime-fusion-restart-recovery.md` |
| 2026-08-06 | 午间 fusion 信号陈旧 | `2026-08-06-noon-fusion-stale.md` |
| 2026-07-23 | 前端文件被删 | `2026-07-23-frontend-deletion.md` |
