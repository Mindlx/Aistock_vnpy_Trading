# 工具清单与适用场景

> 最后更新：2026-06-26

---

## 一、代码审查工具

| 工具 | 类型 | 优势 | 劣势 | 适用场景 |
|------|------|------|------|---------|
| **Sisyphus Agent 审查** | AI 深度审查 | 理解业务语义，能发现逻辑错误、数据链断裂、设计缺陷 | 成本高（token），速度慢 | 合入后深度审查、架构评审、数据流验证 |
| **Semgrep** | 静态代码分析 | 秒级扫描，自定义规则，290+ 预置规则 | 全伪阳性（对已投产项目），不理解业务语义 | 合入前快速检查、`@calibration` 保护校验 |
| **review-work (5 Agent)** | AI 并行审查 | 5 维度覆盖（目标/代码/安全/QA/上下文），可发现真 bug | 配置重，耗时 2-5 分钟 | 重大变更后全量审查 |
| **LSP** | 实时语法检查 | 零成本，IDE 集成 | 只查类型和语法，不查逻辑 | 每日开发，即时反馈 |

### 推荐组合流程

```
合入前:   semgrep --config=.semgrep/  →  5秒，防 @calibration 被覆盖
合入后:   review-work (5 Agent)       →  5分钟，深度审查
日常:     LSP 实时提示
```

---

## 二、代码分析工具

| 工具 | 用途 | 安装 | 使用频率 |
|------|------|------|:--------:|
| **gitnexus-impact-analysis** | 修改前评估波及范围 | 已内置 | 每次修改前 |
| **gitnexus-debugging** | 追踪 bug 调用链 | 已内置 | 按需 |
| **git log / git blame** | 追溯代码历史 | git 自带 | 每日 |
| **python -m py_compile** | 语法检查 | 已安装 | 每次修改后 |

---

## 三、数据验证工具

| 工具 | 用途 | 位置 |
|------|------|------|
| `scripts/backtest.py check` | 回测数据匹配验证 | `scripts/backtest.py` |
| `scripts/backtest.py report` | 回测报告生成 | `scripts/backtest.py` |
| `scripts/scan_calibration_assets.py` | 扫描 `@calibration` 标记 | `scripts/scan_calibration_assets.py` |
| `scripts/verify_data_chains.py`（规划中） | 数据链完整性验证 | — |

---

## 四、合并上游相关

| 工具/流程 | 用途 | 位置 |
|-----------|------|------|
| `fork-merge-audit` SKILL.md | 完整方法论（7 阶段） | `~/.agents/skills/fork-merge-audit/SKILL.md` |
| `fork_merge_audit.py` | 自动化扫描脚本 | `~/.agents/skills/fork-merge-audit/fork_merge_audit.py` |
| `docs/protected_files.txt` | 受保护文件清单 | `docs/protected_files.txt` |
| `.semgrep/calibration-protect.yml` | `@calibration` 保护规则 | `.semgrep/calibration-protect.yml` |

---

## 五、工具选择决策树

```
需要做什么？
  │
  ├─ 合入上游前快速检查？ → Semgrep（秒级）
  │
  ├─ 合入后深度审查？ → review-work 5 Agent（5分钟）
  │
  ├─ 改代码前评估影响范围？ → gitnexus-impact-analysis
  │
  ├─ 查 bug 调用链？ → gitnexus-debugging
  │
  ├─ 验证回测数据完整性？ → scripts/backtest.py check
  │
  ├─ 检查 @calibration 标记是否被覆盖？ → semgrep --config=.semgrep/
  │
  ├─ 扫描所有校准资产？ → scripts/scan_calibration_assets.py
  │
  └─ 审查策略/参数校准？ → 人工（Sisyphus Agent）
```

---

## 六、关于 AI 审查 vs 工具审查

| 对比项 | AI Agent（Sisyphus） | 静态工具（Semgrep/CodeQL） |
|--------|:-------------------:|:--------------------------:|
| 发现逻辑错误（用错变量名） | ✅ | ❌ |
| 发现数据链断裂（跨文件） | ✅ | ❌ |
| 理解校准资产含义 | ✅ | ❌ |
| 发现伪阳性 | ✅ 自动过滤 | ❌ 全部标记 |
| 秒级扫描 | ❌ | ✅ |
| 自定义规则保护 | ❌ | ✅ |

**结论**：两者互补。工具的强项是低成本自动化（秒级），AI 的强项是深度理解（发现真 bug）。推荐组合使用。
