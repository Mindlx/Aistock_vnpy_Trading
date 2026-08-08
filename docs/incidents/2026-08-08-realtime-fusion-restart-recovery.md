# Incident: realtime-fusion 重启后当天无盘中推送 + Persistent 补跑方案否决

- **日期**: 2026-08-08
- **提交**: 9aadf9b (冗余删除) + e9af87d (静默基线方案)
- **涉及文件**: `config/systemd/Aistock_vnpy_Trading-realtime-fusion.timer`, `src/realtime_fusion.py`, `scripts/run_daily.py`

## 一、现象

用户 (8/8) 检查 8/6、8/7 交易时间企业微信推送，发现盘中实时融合推送不连续：

| 日期 | 推送记录 |
|------|----------|
| 8/6 | 09:33 18 changes, 10:28 2 changes, 午盘 13:03 后无推送 |
| 8/7 | 09:33 18 changes, 然后直接停止 (10:27 系统重启) |

### 系统重启记录
- 8/7 10:27、8/8 08:39、8/8 09:31

## 二、根因分析

### Bug 3 (真实): realtime-fusion 重启后当天无盘中推送
- 架构: `.timer`(单点 09:33, Persistent=false) → `.service`(Type=simple 常驻 daemon, while True 每 300s 扫描)
- 链路: timer 只在 09:33 拉起 daemon → daemon 常驻运行。
- **缺陷**: 系统重启杀死 daemon 后，因 timer 是单点 09:33 且 `Persistent=false`，开机后不再补跑 → 当天盘中推送全部丢失。
- 8/7 实证: 09:33 推 18 changes 后，10:27 重启 → 当天后续无任何推送。

### 误判的 Bug 1 / Bug 2 (用户纠正)
- 初判"东方财富评级 PDF 推送失败" (run_daily.py 路径错误) 和"简讯消失"均为误判。
- **真相**: 存在独立 `eastmoney-rating-pdf.service` (7/23 创建)，WorkingDirectory 正确，`generate_rating_report.py` 自含简讯+PDF 推送，8/7 18:03 简讯+PDF 均推送成功 (PDF 连续 5 天齐全)。
- run_daily.py:727 路径错误确实存在，但只是冗余"双重保险"块，主推送不受影响。
- **教训**: 查推送问题要先查 journal 而非仅查 fusion-cron.log；简讯由独立服务推送，与数据获取服务 (fetcher.py) 无关。

## 三、第一版修复 (9aadf9b) 及用户否决

### 9aadf9b 内容
1. timer `Persistent=false` → `true`（用户否决）
2. 删除 run_daily.py:722-736 冗余 PDF 双重保险块 (保留在 9aadf9b)

### 用户否决 Persistent=true (m4306)
> "系统重启错过 09:33 触发点时，开机立即补跑 daemon 一次。这个修复是不对的，可能会引起正常重启后，漏跑信息炸屏。而漏跑的信息应该都是过时信息，很可能没必要重跑。而且还可能会引起系统llm推理排队堵塞"

**原因**: Persistent=true 开机立即补跑 → daemon 冷启动 `_last_scores` 为空 → `_should_push` 中 `last is None → return True` → 全部股票当"变化"推送 → 炸屏过时信号。且补跑可能挤占 LLM 推理队列。

## 四、最终方案 (e9af87d)

### 1. timer 改多点触发 (config/systemd/)
```
OnCalendar=Mon..Fri 09:33:00
OnCalendar=Mon..Fri 10:30:00
OnCalendar=Mon..Fri 11:00:00
OnCalendar=Mon..Fri 13:03:00
OnCalendar=Mon..Fri 14:00:00
Persistent=false
```
- 重启错过 09:33 后，后续任一点仍拉起 daemon。
- 不启用 Persistent，避免开机立即补跑推送过时信号。

### 2. daemon 冷启动静默基线 (src/realtime_fusion.py run_daemon)
- **09:33-09:45 开盘窗口启动** → 首轮正常推送全量基线 (每天开盘用户期望 18 条)。
- **其他交易时段启动 (重启恢复)** → 首轮只调 `scan_and_fuse()` 填充 `_last_scores` 基线、**不推送**；此后仅真实变化才推。
- 避免把重启前的过时信号当"变化"炸屏。

## 五、验证

### 决定性实验
- 10:30 重启恢复: `run_once=0 scan=1 push=0` (静默建基线不推送) ✓
- 09:33 开盘: `run_once=1` (正常推送基线) ✓
- 5 个触发点 `systemd-analyze calendar` 注册正确 (09:33/10:30/11:00/13:03/14:00 各 Next elapse 验证) ✓

### systemd 幂等性实验 (m4333)
- 对已 active 的 service 再 `systemctl --user start` → MainPID 不变 (52989)，日志仅 1 行 → **幂等 no-op，不会重复启动 daemon**
- 结论: 多点触发不会增加每日扫描次数 (仍由 daemon interval=300 控制，每天约 49 次)，不会重复拉起已运行的 daemon

### LLM 隔离确认
- `src/realtime_fusion.py` grep LLM/生成/prompt 零命中 → 只读文件交换区 JSON + 新浪行情 + 融合推送，**不调用 LLM 推理**，无滥用风险

### 回归
- pytest 137 passed
- run_daily --dry-run 无回归 (冗余块删除后推送逻辑正常)

## 六、经验教训

1. **查推送问题先查 journal**，不能只看 fusion-cron.log (pdf service StandardOutput=journal)。
2. **常驻 daemon 用 Persistent=true 是错误语义** — Persistent 适合"补跑错过的定时任务"，对常驻进程会导致冷启动全量误推。
3. **"有/没有"先实测** (铁律#1): 初判 Bug1/Bug2 前应先确认 eastmoney-rating-pdf.service 存在及其 journal 输出。
4. **用户对系统推送行为有强预期** (每天 09:33 开盘基线 18 条)，改动前需保留既有行为。
