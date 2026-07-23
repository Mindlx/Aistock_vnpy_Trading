# 三子系统上游管理指南

> **用途**: 主项目对 LY/ML/AT 子系统的优化代码，同步回独立上游 fork 的操作手册。
> **原则**: 凡是对子系统自身的代码优化，都可以（且应该）同步回 fork，保持上游可追踪。
> **最后更新**: 2026-07-23

---

## 三子系统上游仓库

| 子系统 | 本地代码位置 | 上游 fork | 原作者 |
|:-------|:------------|:----------|:-------|
| **ML** (MindLynx-Aistock) | `systems/MindLynx-Aistock/` | `github.com/Mindlx/MindLynx-Aistock.git` | `ZhuLinsen/daily_stock_analysis` |
| **LY** (lynx_vnpy) | `systems/lynx_vnpy/` | `github.com/Mindlx/lynx_vnpy.git` | `vnpy/vnpy`(上游) |
| **AT** (mind_TradingAgent) | `systems/mind_TradingAgent/` | `github.com/Mindlx/mind_TradingAgents.git` | `TauricResearch/TradingAgents`(上游) |

fork 源目录：`/home/bluekuma/workspace/{子系统目录}/`

---

## 适用场景

| 场景 | 示例 | 需要同步？ |
|:-----|:------|:---------:|
| 修复子系统 bug | `market_analyzer.py` label bug | ✅ |
| 给子系统加功能 | TTL 缓存、整点分析注入 | ✅ |
| 改子系统配置 | `.env` 变量调整 | ✅ |
| 改融合引擎代码 | `fusion_engine.py` | ❌ 不属于子系统 |
| 改测试代码 | `tests/test_fusion_engine.py` | ❌ 不属于子系统 |

---

## 上游合并策略（重要）

### 历史教训

| 日期 | 操作 | 结果 | 经验 |
|:----:|:-----|:-----|:-----|
| 2026-06-23 | 三子系统批量合并上游 (ML v3.21 + AT v0.3.0) | **31 处校准参数被覆盖**，ML 准确率 61%→55.9%，恢复耗时 3 天 | ❌ **禁止批量合并** |
| 2026-06-26 | 单 commit cherry-pick 新闻过滤 fix | 零冲突、零覆盖、零修复 | ✅ **推荐模式** |
| 2026-07-23 | TA 14 commits 合并 (v0.3.1) | 5 个自定义文件被 rsync --delete 删除，`trading_graph.py` policy 节点丢失 | ⚠️ **小批量合并仍需逐项验证** |

### 推荐策略

**上游合并的正确做法：单 commit 增量合入，不是批量合并。**

```
1. fork-merge-audit 扫描上游 commits
2. 挑出对 A 股融合系统有价值的具体 fix（SAFE 级别优先）
3. 逐个 cherry-pick，每个验证后再合入
4. 跳过 BLOCKED commits（冲突过多，风险大于收益）

ML (ZhuLinsen) 上游 271 commits: 跳过批量合并。
如果某个具体 bug fix 有用，单独 cherry-pick 那一个。
```

---

## 本地定制代码记录（2026-07-23 更新）

### ML 子系统 — 高风险文件

| 文件 | 改动内容 | 最后 commit |
|:-----|:---------|:------------|
| `src/market_analyzer.py` | TTL 缓存 + 整点分析注入 + label bug 修复 | 6ca68ce |
| `src/core/market_review.py` | 整点分析 + treemap 优化 | 6ca68ce |
| `src/config.py` | 根 .env 后备加载 | f623a87 |
| `src/core/factor_engine.py` | **14 因子**（原 12 + pattern_chart_elevated + concentration），turnover_sentiment 对数横截面改进，FACTOR_INTERPRETATIONS | 2026-07-23 |
| `src/core/factor_monitor.py` | factor 跟踪从 12→14 | 2026-07-23 |
| `src/agent/executor.py` | Legacy prompt 删除(−3600 chars) | 2026-07-23 |
| `src/agent/factory.py` | USE_COMPACT_TOOLS env 支持 | 2026-07-23 |
| `src/core/prompt_shared.py` | 动态加载 prompt_config（A/B 版本） | 2026-07-23 |
| `src/core/prompt_config.py` | **新增** A/B 版本配置中心 | 2026-07-23 |
| `src/agent/tools/analysis_tools.py` | 双底变体分类 + 84.4% 硬规则注入 | 2026-07-23 |
| `strategies/elevated_double_bottom.yaml` | **新增** 高中间峰双底策略 | 2026-07-23 |
| `strategies/chip_concentration.yaml` | **新增** 筹码集中度策略 | 2026-07-23 |
| `strategies/emotion_cycle.yaml` | 评分校准 +5/+10（基于 A3/A6 证伪） | 2026-07-23 |

### AT 子系统 — 高风险文件

| 文件 | 改动内容 | 注意 |
|:-----|:---------|:-----|
| `agents/analysts/capital_flow_tracker.py` | **融合独有** — 资金流分析 | 上游无此文件，sync 时会被 --delete 删除 |
| `agents/analysts/policy_analyst.py` | **融合独有** — 政策分析 | 上游无此文件，sync 时会被 --delete 删除 |
| `agents/researchers/researcher.py` | **融合独有** — 研究员 agent | 上游无此文件，sync 时会被 --delete 删除 |
| `dataflows/warehouse.py` | **融合独有** — 数据仓库 | 上游无此文件，sync 时会被 --delete 删除 |
| `dataflows/xueqiu.py` | **融合独有** — 雪球数据 | 上游无此文件，sync 时会被 --delete 删除 |
| `graph/trading_graph.py` | **policy 工具节点** + `get_capital_flows` 导入 | 上游版本无此节点 |

> ⚠️ AT 的 5 个自定义文件在 2026-07-23 TA 合并时被 rsync --delete 删除，从 git 历史恢复。sync 后必须检查。

### LY 子系统

LY 当前无融合独有定制代码。`lynx_signal.py` 有少量本地调整（`_l7_score` 映射），sync_systems.sh 的保护机制已覆盖。

---

## 同步到 fork 的操作步骤

### 推送到 fork（我们的改动 → 上游 fork）

```bash
SUBSYSTEM="MindLynx-Aistock"  # 或 lynx_vnpy / mind_TradingAgent
SRC="systems/$SUBSYSTEM"
DST="/home/bluekuma/workspace/$SUBSYSTEM"

# 1. 列出差异
diff -rq "$SRC" "$DST" | grep -v "__pycache__\|\.pyc\|\.git\|\.venv"

# 2. 同步到上游 fork
rsync -a --delete \
    --exclude='__pycache__/' --exclude='*.pyc' \
    --exclude='.git/' --exclude='.venv/' --exclude='.env' \
    --exclude='data/' --exclude='logs/' \
    "$SRC/" "$DST/"

# 3. 提交并推送
cd "$DST"
git add -A
git commit -m "sync: 说明同步内容" --no-verify
git push origin HEAD
```

### 从上游拉取（上游原始仓库 → 我们的 fork）

```bash
# sync_systems.sh 会自动处理备份/恢复
bash scripts/sync_systems.sh

# sync 后必须检查定制文件是否完整
# ML: factor_engine.py / 策略YAML / prompt_config.py
# AT: capital_flow_tracker.py / policy_analyst.py / researcher.py / warehouse.py / xueqiu.py
```

### 合入上游单个 commit（推荐模式）

```bash
cd /home/bluekuma/workspace/MindLynx-Aistock  # 或对应子系统
git fetch upstream
git cherry-pick <commit-hash>
git push origin HEAD
bash scripts/sync_systems.sh  # 同步回本融合系统
```

---

## 注意事项

1. fork 仓库的 `pre-commit` hook 依赖 venv，未激活时会失败。始终用 `--no-verify` 跳过
2. `.env` 文件在 fork 仓库中被 gitignore，不提交
3. **AT 有 5 个融合独有文件**（`capital_flow_tracker.py` 等），rsync --delete 会删除它们。sync 后必须从 git 恢复或重新创建
4. 上游 sync（`sync_systems.sh`）后，需按高/中风险文件清单逐项检查并恢复
5. 批量合并上游（如 ML 的 271 commits）**强烈不建议**，风险远大于收益
