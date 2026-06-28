#!/usr/bin/env bash
# ============================================================
# IC 滚动监测 — 每周自动测量 RF/LGB IC，动态更新权重
#
# 触发：systemd timer 每周五 19:30
# 逻辑：
#   1. 读取 prob_up_log.csv 最新 N 条记录
#   2. 匹配 stock_daily 实际涨跌数据
#   3. 计算 RF / LGB / Ensemble 的 Pearson IC
#   4. 如果新权重与当前权重偏差 > 0.05，更新 lynx_signal.py
#   5. 生成监测报告
# ============================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPORT_DIR="${PROJECT_DIR}/data/reports/ic-monitor"
TIMESTAMP=$(date '+%Y-%m-%d_%H%M')
REPORT_FILE="${REPORT_DIR}/ic_${TIMESTAMP}.md"
mkdir -p "${REPORT_DIR}"

cd "${PROJECT_DIR}"
source .venv/bin/activate 2>/dev/null || true

TIMESTAMP="$TIMESTAMP" python3 -c "
import csv, math, os, sys, re
from pathlib import Path
import sqlite3

TS = os.environ.get('TIMESTAMP', 'unknown')
PROJECT = Path('${PROJECT_DIR}')
PROB_LOG = PROJECT / 'data/realtime/prob_up_log.csv'
STOCK_DB = PROJECT / 'systems/MindLynx-Aistock/data/stock_analysis.db'
SIGNAL_PY = PROJECT / 'systems/lynx_vnpy/lynx_signal.py'
REPORT = PROJECT / 'data/reports/ic-monitor' / f'ic_{TS}.md'

# ── Load predictions ──
preds = []
with open(PROB_LOG) as f:
    reader = csv.DictReader(f)
    for row in reader:
        try:
            rf = float(row['prob_up_rf'])
            lgb = float(row['prob_up_lgb'])
            ensemble = float(row.get('prob_up_ensemble', '')) if row.get('prob_up_ensemble') else (rf+lgb)/2
            preds.append({
                'date': row['date'], 'code': row['stock_code'], 'name': row['stock_name'],
                'rf': rf, 'lgb': lgb, 'ensemble': ensemble,
            })
        except (ValueError, KeyError):
            pass

if not preds:
    print('⚠  prob_up_log.csv 无有效数据')
    exit(0)

# ── Match actual next-day returns ──
conn = sqlite3.connect(str(STOCK_DB))
cur = conn.cursor()
stock_days = {}
for code in set(p['code'] for p in preds):
    cur.execute('SELECT date, pct_chg FROM stock_daily WHERE code = ? ORDER BY date', (code,))
    stock_days[code] = cur.fetchall()
conn.close()

matched = []
for p in preds:
    days = stock_days.get(p['code'], [])
    for i, (d, pct) in enumerate(days):
        if d == p['date'] and i + 1 < len(days):
            p['next_pct'] = days[i+1][1] if days[i+1][1] is not None else 0
            matched.append(p)
            break

if len(matched) < 30:
    print(f'⚠  有效样本不足 ({len(matched)}), 需要 ≥30')
    sys.exit(0)

# ── Calculate IC ──
def pearson(x, y):
    n = len(x)
    mx, my = sum(x)/n, sum(y)/n
    cov = sum((xi-mx)*(yi-my) for xi,yi in zip(x,y))
    sx = math.sqrt(sum((xi-mx)**2 for xi in x))
    sy = math.sqrt(sum((yi-my)**2 for yi in y))
    if sx == 0 or sy == 0: return 0
    return cov/(sx*sy)

def dir_acc(probs, rets):
    n = len(probs)
    return sum(1 for p,r in zip(probs, rets) if (p>50)==(r>0))/n*100 if n else 0

rets = [p['next_pct'] for p in matched]

ic_rf = pearson([p['rf'] for p in matched], rets)
ic_lgb = pearson([p['lgb'] for p in matched], rets)
ic_ens = pearson([p['ensemble'] for p in matched], rets)

acc_rf = dir_acc([p['rf'] for p in matched], rets)
acc_lgb = dir_acc([p['lgb'] for p in matched], rets)
acc_ens = dir_acc([p['ensemble'] for p in matched], rets)

# ── Compute recommended weights ──
total_ic = max(ic_rf, 0) + max(ic_lgb, 0)
if total_ic > 0:
    w_lgb = max(ic_lgb, 0) / total_ic
    w_rf = max(ic_rf, 0) / total_ic
else:
    w_lgb, w_rf = 0.5, 0.5

# ── Read current weights from lynx_signal.py ──
curr_w_lgb = 0.91
curr_w_rf = 0.09
if SIGNAL_PY.exists():
    content = SIGNAL_PY.read_text()
    m_lgb = re.search(r'_LGB_IC_WEIGHT\s*=\s*([\d.]+)', content)
    m_rf = re.search(r'_RF_IC_WEIGHT\s*=\s*([\d.]+)', content)
    if m_lgb: curr_w_lgb = float(m_lgb.group(1))
    if m_rf:  curr_w_rf = float(m_rf.group(1))

# ── Check if update needed ──
need_update = abs(w_lgb - curr_w_lgb) > 0.05
w_lgb_r, w_rf_r = round(w_lgb, 2), round(w_rf, 2)
c_lgb_r, c_rf_r = round(curr_w_lgb, 2), round(curr_w_rf, 2)

# ── Generate report ──
REPORT.parent.mkdir(parents=True, exist_ok=True)
with open(REPORT, 'w', encoding='utf-8') as f:
    f.write(f'# IC 滚动监测报告 — {TS}\n\n')
    f.write(f'样本数: {len(matched)}\n\n')
    f.write(f'| 模型 | Pearson IC | 方向准确率 |\n')
    f.write(f'|------|:---------:|:----------:|\n')
    f.write(f'| RF   | {ic_rf:>8.4f} | {acc_rf:>7.1f}% |\n')
    f.write(f'| LGB  | {ic_lgb:>8.4f} | {acc_lgb:>7.1f}% |\n')
    f.write(f'| Ensemble | {ic_ens:>8.4f} | {acc_ens:>7.1f}% |\n\n')
    f.write(f'## 权重\n\n')
    f.write(f'| 权重 | 当前 | 推荐 |\n')
    f.write(f'|------|:----:|:----:|\n')
    f.write(f'| LGB  | {c_lgb_r:.2f} | {w_lgb_r:.2f} |\n')
    f.write(f'| RF   | {c_rf_r:.2f} | {w_rf_r:.2f} |\n\n')

    if need_update:
        f.write(f'## ⚡ 权重已更新\n')
        f.write(f'LGB {c_lgb_r:.2f} → {w_lgb_r:.2f}, RF {c_rf_r:.2f} → {w_rf_r:.2f}\n')
    else:
        f.write(f'## ✓ 权重无需更新\n')
        f.write(f'偏差 ≤ 0.05，保持当前权重。\n')

# ── Update weights if needed ──
if need_update and SIGNAL_PY.exists():
    content = SIGNAL_PY.read_text()
    content = re.sub(r'_LGB_IC_WEIGHT\s*=\s*[\d.]+', f'_LGB_IC_WEIGHT = {w_lgb_r}', content)
    content = re.sub(r'_RF_IC_WEIGHT\s*=\s*[\d.]+', f'_RF_IC_WEIGHT = {w_rf_r}', content)
    SIGNAL_PY.write_text(content)
    print(f'✅ 权重已更新: LGB={w_lgb_r} RF={w_rf_r}')
    # Commit to git for traceability
    import subprocess
    repo_dir = str(PROJECT)
    subprocess.run(['git', 'add', 'systems/lynx_vnpy/lynx_signal.py'],
                   cwd=repo_dir, capture_output=True)
    subprocess.run(['git', 'commit', '-m',
                    f'auto: IC权重更新 LGB={w_lgb_r} RF={w_rf_r}',
                    '--no-verify'],
                   cwd=repo_dir, capture_output=True)
    print(f'   git commit: IC权重 LGB={w_lgb_r} RF={w_rf_r}')

print(f'📊 报告: {REPORT}')
print(f'   RF  IC={ic_rf:.4f} Acc={acc_rf:.1f}%')
print(f'   LGB IC={ic_lgb:.4f} Acc={acc_lgb:.1f}%')
print(f'   Ens IC={ic_ens:.4f} Acc={acc_ens:.1f}%')
print(f'   当前: LGB={c_lgb_r} RF={c_rf_r}  推荐: LGB={w_lgb_r} RF={w_rf_r}')
print(f'   更新: {\"是\" if need_update else \"否\"}')
"
