#!/bin/bash
# ============================================================
# Aistock_vnpy_Trading — 快速恢复脚本
# 用途: 从 tar.gz 备份恢复后, 用此脚本重建运行环境
# 用法: bash scripts/setup_restore.sh
# ============================================================
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)

echo "=== 恢复环境 ==="

# 1. Python 虚拟环境
if [ ! -d "$ROOT/.venv" ]; then
    echo "[1/5] 创建虚拟环境..."
    python3 -m venv "$ROOT/.venv"
else
    echo "[1/5] 虚拟环境已存在"
fi

# 2. 安装依赖
echo "[2/5] 安装核心依赖..."
"$ROOT/.venv/bin/pip" install -r "$ROOT/requirements.txt" -q

echo "[2/5] 安装完整依赖(可选)..."
if [ -f "$ROOT/requirements-full.txt" ]; then
    "$ROOT/.venv/bin/pip" install -r "$ROOT/requirements-full.txt" -q
fi

# 3. 检查 systemd 配置
echo "[3/5] 检查 systemd 服务..."
if command -v systemctl &> /dev/null; then
    bash "$ROOT/scripts/deploy-systemd.sh" --check 2>/dev/null || true
    echo "    → 运行 'bash scripts/deploy-systemd.sh' 部署服务"
else
    echo "    → 跳过 (非 systemd 系统)"
fi

# 4. 关键依赖验证
echo "[4/5] 验证依赖..."
"$ROOT/.venv/bin/python" -c "
import sys; sys.path.insert(0, '.')
errors = []
for mod in ['pandas', 'numpy', 'yaml', 'requests', 'matplotlib', 'squarify', 'weasyprint',
            'langgraph', 'openai', 'litellm', 'sqlalchemy', 'sklearn', 'xgboost', 'lightgbm',
            'akshare', 'efinance']:
    try:
        __import__(mod)
        print(f'  ✅ {mod}')
    except ImportError:
        errors.append(mod)
        print(f'  ❌ {mod}')
if errors:
    print(f'缺少模块: {errors}')
" 2>/dev/null || "$ROOT/.venv/bin/python" -c "
import sys
mods = ['pandas', 'yaml', 'requests', 'matplotlib', 'numpy']
for m in mods:
    try:
        __import__(m); print(f'  ✅ {m}')
    except: print(f'  ❌ {m} (可能非必需)')
"

# 5. 数据文件检查
echo "[5/5] 数据文件..."
for f in "$ROOT/config/stock_pool.csv" "$ROOT/config/settings.yaml" "$ROOT/.env"; do
    if [ -f "$f" ]; then
        echo "  ✅ $(basename $f)"
    else
        echo "  ⚠️ $(basename $f) 缺失 (需手动创建)"
    fi
done

echo ""
echo "=== 恢复完成 ==="
echo "运行: cd $ROOT && .venv/bin/python scripts/run_daily.py"
