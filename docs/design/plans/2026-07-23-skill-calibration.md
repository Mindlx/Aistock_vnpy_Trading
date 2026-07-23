# 策略级准确率校准系统 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 建立策略级准确率追踪与自动校准闭环，使 18 个策略 YAML 的评分调整值基于实际回测数据动态校准。

**Architecture:** 基于已有 `analysis_history` 表，每日回算每个策略的准确率，低于阈值自动下调策略 YAML 的评分调整值，高于阈值自动上调。

**Tech Stack:** Python 3.10+, sqlite3, YAML

## Global Constraints

- 所有 YAML 修改使用 `yaml.safe_load` + 字符串替换（不破坏 `{@calibration}` 标签）
- 校准脚本必须支持 `--dry-run` 模式只输出不修改
- 最小样本量 30 条，低于此数的策略跳过校准
- 调整幅度每次仅一档，不跳级
- 写入 YAML 使用原子写入（临时文件 + rename）

---

### Task 1: 填充 `analysis_history.skill_id`

**Files:**
- Modify: `systems/MindLynx-Aistock/src/core/pipeline.py:111`
- Modify: `systems/MindLynx-Aistock/src/core/pipeline.py:520-524`

**Interfaces:**
- Consumes: `self.analysis_skills: list[str] | None` — 当前在 `__init__` 中设置，但调用方未传参
- Produces: `analysis_history.skill_id` 字段填入实际策略名（逗号分隔）而非 `"consensus"`

当前 `pipeline.py:111` 从 `__init__` 参数接收 `analysis_skills`。生产路径中该参数未传入，导致 `self.analysis_skills` 为 `None`，进而 `skill_id` 存为 `"consensus"`。

需要在 `pipeline.py` 中增加一个回退逻辑：当 `analysis_skills` 为 `None` 时，从当前激活的策略列表中解析。

- [ ] **Step 1: 阅读代码确认调用链**

阅读 `analyzer.py` 中 `Scheduler` 的实例化代码，确认 `analysis_skills` 参数的传值路径。

```bash
grep -n "analysis_skills\|Scheduler(" systems/MindLynx-Aistock/src/analyzer.py | head -20
```

预期输出显示调用链中 `analysis_skills` 的传递情况。

- [ ] **Step 2: 修改 `pipeline.py` 添加回退逻辑**

在 `pipeline.py:111` 后添加：

```python
# 如果外部未传入 analysis_skills，尝试从 config 获取默认激活技能
if self.analysis_skills is None:
    try:
        from src.agent.skills.defaults import get_default_active_skill_ids
        self.analysis_skills = get_default_active_skill_ids()
    except Exception:
        self.analysis_skills = []
```

- [ ] **Step 3: 编译验证**

```bash
python3 -m py_compile systems/MindLynx-Aistock/src/core/pipeline.py
```

预期输出：无错误。

- [ ] **Step 4: 提交**

```bash
git add systems/MindLynx-Aistock/src/core/pipeline.py
git commit -m "feat: 填充 analysis_history.skill_id 为实际策略名"
```

---

### Task 2: 创建校准脚本 `scripts/calibrate_skill_scores.py`

**Files:**
- Create: `scripts/calibrate_skill_scores.py`

**Interfaces:**
- Produces: `--dry-run` 模式打印校准计划但不修改文件；正常模式直接更新 `strategies/*.yaml`
- CLI: `python scripts/calibrate_skill_scores.py [--dry-run] [--min-samples 30]`

- [ ] **Step 1: 读取 `analysis_history` 按 `skill_id` 聚合准确率**

```python
import sqlite3, yaml, os, re, sys, json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

DB = "systems/MindLynx-Aistock/data/stock_analysis.db"
STRATEGIES_DIR = "systems/MindLynx-Aistock/strategies"

def compute_skill_accuracy(db_path: str, min_samples: int = 30, days: int = 90):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT ah.sentiment_score, ah.skill_id, sp.pct_chg
        FROM analysis_history ah
        JOIN stock_daily sp ON sp.code = ah.code
            AND sp.date = date(ah.created_at, '+1 day')
        WHERE ah.sentiment_score IS NOT NULL
          AND sp.pct_chg IS NOT NULL
          AND ah.created_at >= date('now', ?)
    """, (f'-{days} days',)).fetchall()
    conn.close()

    skill_stats = defaultdict(lambda: {"total": 0, "correct": 0})
    for r in rows:
        raw = (r["skill_id"] or "consensus").split(",")
        actual = 1 if r["pct_chg"] > 0 else (-1 if r["pct_chg"] < 0 else 0)
        if actual == 0:
            continue
        score = r["sentiment_score"]
        correct = (score >= 52 and actual > 0) or (score <= 48 and actual < 0) or (49 <= score <= 51)
        for sid in raw:
            sid = sid.strip()
            if sid:
                skill_stats[sid]["total"] += 1
                if correct:
                    skill_stats[sid]["correct"] += 1

    result = {}
    for sid, stats in skill_stats.items():
        if stats["total"] >= min_samples:
            result[sid] = {
                "accuracy": stats["correct"] / stats["total"],
                "total": stats["total"],
                "correct": stats["correct"],
            }
    return result
```

- [ ] **Step 2: 实现 YAML 评分值校准逻辑**

评分调整值的格式为 `sentiment_score +/-N`（如 `sentiment_score +14`、`sentiment_score -12`）。

校准规则：

| 准确率 | 操作 |
|:------:|:-----|
| < 40% | 正向值归零（改为 `+0`），负向保留 |
| 40-50% | 正向值降一档：+14→+10, +10→+8, +8→+5, +5→+3, +3→+2, +2→+1, +1→+0 |
| 50-70% | 不变 |
| 70-80% | 正向值升一档：+0→+2, +2→+3, +3→+5, +5→+8, +8→+10 |
| > 80% | 不变，记录"已验证" |

```python
ADJUST_BANDS = [
    (0.70, 0.80, 1),    # 上调一档
    (0.40, 0.50, -1),   # 下调一档
    (0.00, 0.40, -999), # 归零
]
UPGRADE = [0, 2, 3, 5, 8, 10, 12, 14, 15, 17, 20, 22, 23]
DOWNGRADE = [0, 2, 3, 5, 8, 10, 12, 14, 15, 17, 20, 22, 23]

def _clamp_value(value: int, direction: int) -> int:
    """将评分调整值按方向调整一档。direction=1 上调, =-1 下调, =-999 归零。"""
    if direction == -999:
        return 0
    try:
        idx = UPGRADE.index(value)
    except ValueError:
        return value
    new_idx = max(0, min(len(UPGRADE) - 1, idx + direction))
    return UPGRADE[new_idx]

def calibrate_yaml(filepath: str, skill_id: str, accuracy: float) -> dict:
    """修改 YAML 文件中的 sentiment_score 调整值。返回 {'changed': bool, 'changes': [...]}"""
    with open(filepath) as f:
        content = f.read()
    original = content

    direction = 0
    for lower, upper, adj in ADJUST_BANDS:
        if lower <= accuracy < upper:
            direction = adj
            break

    if direction == 0:
        return {"changed": False, "changes": []}

    changes = []
    # 匹配 "sentiment_score +N" 和 "sentiment_score -N"
    pattern = r'(sentiment_score\s*)([+-])(\d+)'
    def replacer(m):
        prefix = m.group(1)
        sign = m.group(2)
        val = int(m.group(3))
        if sign == '-':
            return m.group(0)  # 负向不调整
        new_val = _clamp_value(val, direction)
        if new_val != val:
            changes.append(f"{prefix}{sign}{val} → {prefix}+{new_val}")
            return f"{prefix}+{new_val}"
        return m.group(0)

    content = re.sub(pattern, replacer, content)
    if content == original:
        return {"changed": False, "changes": []}
    return {"changed": True, "changes": changes, "new_content": content}
```

- [ ] **Step 3: 实现文件写入（原子写入）**

```python
def apply_changes(filepath: str, new_content: str) -> bool:
    """原子写入 YAML 文件."""
    tmp = filepath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_content)
    os.replace(tmp, filepath)
    return True
```

- [ ] **Step 4: 实现 main 函数（含 --dry-run）**

```python
def main():
    import argparse
    parser = argparse.ArgumentParser(description="校准策略评分调整值")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不修改文件")
    parser.add_argument("--min-samples", type=int, default=30, help="最小样本量")
    parser.add_argument("--days", type=int, default=90, help="回看天数")
    args = parser.parse_args()

    accuracy_data = compute_skill_accuracy(DB, args.min_samples, args.days)
    if not accuracy_data:
        print("无足够数据，跳过校准")
        return

    print(f"\n{'策略ID':<28} {'准确率':>8} {'样本':>6} {'操作':>10}")
    print("-" * 54)

    total_changed = 0
    for sid, data in sorted(accuracy_data.items(), key=lambda x: -x[1]["accuracy"]):
        acc = data["accuracy"]
        total = data["total"]
        
        # 找到对应 YAML
        yaml_path = None
        for f in Path(STRATEGIES_DIR).glob("*.yaml"):
            with open(f) as yf:
                if yaml.safe_load(yf).get("name") == sid:
                    yaml_path = str(f)
                    break
        
        if not yaml_path:
            print(f"{sid:<28} {acc:>7.1%} {total:>6} {'无对应YAML':>10}")
            continue

        result = calibrate_yaml(yaml_path, sid, acc)
        if result["changed"]:
            total_changed += 1
            changes_str = "; ".join(result["changes"][:3])
            print(f"{sid:<28} {acc:>7.1%} {total:>6} {'✅':>10} {changes_str}")
            if not args.dry_run:
                apply_changes(yaml_path, result["new_content"])
        else:
            print(f"{sid:<28} {acc:>7.1%} {total:>6} {'—':>10}")

    print(f"\n共校准 {total_changed} 个策略" if not args.dry_run else f"\n预览: 将校准 {total_changed} 个策略")
```

- [ ] **Step 5: 编译验证**

```bash
python3 -m py_compile scripts/calibrate_skill_scores.py
```

预期输出：无错误。

- [ ] **Step 6: 冒烟测试（--dry-run 模式）**

```bash
python3 scripts/calibrate_skill_scores.py --dry-run
```

预期输出：显示各策略的准确率和校准计划（如果数据不足则显示"无足够数据"）。

- [ ] **Step 7: 提交**

```bash
git add scripts/calibrate_skill_scores.py
git commit -m "feat: 策略评分自动校准脚本"
```

---

### Task 3: 集成到 scheduler 每日 20:30

**Files:**
- Modify: `services/data_warehouse/scheduler.py`

- [ ] **Step 1: 在 `run_forever()` 中添加 20:30 触发**

找到 `scheduler.py` 中 `run_forever()` 方法的 20:30 附近（现有 `diagnose-agreement` 定时块），添加：

```python
# 20:30: 校准策略评分调整值
if hour == 20 and minute == 30 and now - last_calibrate > 3600:
    if weekday <= 5:
        logger.info("[Scheduler] 触发: 策略评分校准")
        import subprocess
        result = subprocess.run(
            [sys.executable, "scripts/calibrate_skill_scores.py", "--min-samples", "30"],
            capture_output=True, text=True, timeout=120,
        )
        logger.info("[Scheduler] 校准完成:\n%s", result.stdout)
        if result.stderr:
            logger.warning("[Scheduler] 校准错误:\n%s", result.stderr)
        last_calibrate = now
```

在文件顶部添加 `last_calibrate = 0` 到时间追踪变量列表。

- [ ] **Step 2: 编译验证**

```bash
python3 -m py_compile services/data_warehouse/scheduler.py
```

预期输出：无错误。

- [ ] **Step 3: 提交**

```bash
git add services/data_warehouse/scheduler.py
git commit -m "feat: scheduler 每日20:30自动运行策略校准"
```

---

### Task 4: 推送校准报告

**Files:**
- Create: `scripts/calibrate_skill_scores.py` 中已有 `main()` 输出，追加 WeCom 推送

- [ ] **Step 1: 在校准脚本末尾追加推送**

在 `main()` 函数的最后，如果 `total_changed > 0` 且非 `--dry-run`，调用 WeCom 推送：

```python
if total_changed > 0 and not args.dry_run:
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from src.wecom_notifier import WeComNotifier
        notifier = WeComNotifier()
        msg = f"### 策略评分自动校准\n\n共校准 {total_changed} 个策略\n\n"
        for sid, data in sorted(accuracy_data.items(), key=lambda x: -x[1]["accuracy"]):
            acc = data["accuracy"]
            emoji = "🟢" if acc > 0.70 else ("🟡" if acc > 0.50 else "🔴")
            msg += f"{emoji} {sid}: {acc:.1%} (n={data['total']})\n"
        notifier.send_markdown(msg)
    except Exception as exc:
        logger.warning("推送校准报告失败: %s", exc)
```

- [ ] **Step 2: 编译验证**

```bash
python3 -m py_compile scripts/calibrate_skill_scores.py
```

预期输出：无错误。

- [ ] **Step 3: 提交**

```bash
git add scripts/calibrate_skill_scores.py
git commit -m "feat: 校准结果推送 WeCom"
```

---

### 验证

```bash
# 1. 编译全部改动
python3 -m py_compile systems/MindLynx-Aistock/src/core/pipeline.py
python3 -m py_compile scripts/calibrate_skill_scores.py
python3 -m py_compile services/data_warehouse/scheduler.py

# 2. Dry-run 校准
python3 scripts/calibrate_skill_scores.py --dry-run

# 3. 全量编译检查
for f in \
    systems/MindLynx-Aistock/src/core/pipeline.py \
    scripts/calibrate_skill_scores.py \
    services/data_warehouse/scheduler.py; do
    python3 -m py_compile "$f" && echo "  ✅ $f" || echo "  ❌ $f"
done
```
