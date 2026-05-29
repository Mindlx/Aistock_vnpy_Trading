#!/usr/bin/env bash
# 从上游仓库同步三个系统的最新代码
# 用法: ./scripts/sync_systems.sh
#
# 三个系统的 GitHub 仓库作为上游 Source of Truth。
# 融合系统保留全量拷贝，定期从此脚本同步更新。
#
# rsync 排除规则:
#   .git/       - git 元数据（不追踪）
#   venv/       - 虚拟环境（路径硬编码 + 体积大）
#   __pycache__/ - Python 缓存
#   .env        - API 密钥（逐个仓库单独管理）
#   logs/       - 运行时日志
#   docs/       - 文档（融合系统不需要）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "=== 三系统同步脚本 ==="
echo "目标: $SCRIPT_DIR/systems/"
echo ""

# 通用排除规则
EXCLUDES=(
    --exclude='.git/'
    --exclude='__pycache__/'
    --exclude='*.pyc'
    --exclude='.mypy_cache/'
    --exclude='.pytest_cache/'
    --exclude='.ruff_cache/'
    --exclude='.gitnexus/'
    --exclude='.claude/'
    --exclude='.github/'
    --exclude='.omo/'
    --exclude='.hermes/'
)

# 1. lynx_vnpy
echo "[1/3] lynx_vnpy..."
rsync -a --delete \
    "${EXCLUDES[@]}" \
    --exclude='lynx_env/' \
    --exclude='examples/' \
    --exclude='.gitignore' \
    ../lynx_vnpy/ systems/lynx_vnpy/

# 2. MindLynx-Aistock
echo "[2/3] MindLynx-Aistock..."
rsync -a --delete \
    "${EXCLUDES[@]}" \
    --exclude='.venv/' \
    --exclude='logs/' \
    --exclude='docs/' \
    --exclude='data/' \
    --exclude='.env' \
    --exclude='.env.example' \
    --exclude='.gitignore' \
    ../MindLynx-Aistock/ systems/MindLynx-Aistock/

# 3. mind_TradingAgent
echo "[3/3] mind_TradingAgent..."
rsync -a --delete \
    "${EXCLUDES[@]}" \
    --exclude='.env' \
    --exclude='.env.example' \
    --exclude='assets/' \
    --exclude='.gitignore' \
    ../mind_TradingAgent/ systems/mind_TradingAgent/

echo ""
echo "=== 完成 ==="
echo "各系统大小:"
du -sh systems/*/
