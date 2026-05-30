#!/usr/bin/env bash
# 从上游 GitHub 仓库同步三个子系统的最新代码
# 用法: ./scripts/sync_systems.sh
#
# 三个子系统各自在独立的 GitHub 仓库中开发维护。
# 开发完成后通过此脚本同步到本融合系统的 systems/ 目录。
#
# 同步来源（环境变量覆盖）:
#   SYNC_SRC_LYNX  默认: ../lynx_vnpy
#   SYNC_SRC_MIND  默认: ../MindLynx-Aistock
#   SYNC_SRC_TA    默认: ../mind_TradingAgent
#
# GitHub 上游:
#   lynx_vnpy:         https://github.com/Mindlx/lynx_vnpy
#   MindLynx-Aistock:  https://github.com/Mindlx/MindLynx-Aistock
#   mind_TradingAgent: https://github.com/Mindlx/mind_TradingAgents

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
    --exclude='.gitignore'
    --exclude='README.md'
    --exclude='LICENSE'
)

# lynx_vnpy
echo "[1/3] lynx_vnpy..."
SYNC_SRC_LYNX="${SYNC_SRC_LYNX:-../lynx_vnpy}"
if [ -d "$SYNC_SRC_LYNX" ]; then
    rsync -a --delete "${EXCLUDES[@]}" \
        --exclude='lynx_env/' \
        --exclude='docs/' \
        --exclude='examples/' \
        --exclude='tests/' \
        --exclude='CHANGELOG.md' \
        --exclude='AGENTS.md' \
        --exclude='CLAUDE.md' \
        --exclude='install*' \
        "$SYNC_SRC_LYNX/" systems/lynx_vnpy/
    echo "  从 $SYNC_SRC_LYNX 同步完成"
else
    echo "  ⚠️  $SYNC_SRC_LYNX 不存在，跳过"
    echo "  设置 SYNC_SRC_LYNX 环境变量指定源码路径"
fi

# MindLynx-Aistock
echo "[2/3] MindLynx-Aistock..."
SYNC_SRC_MIND="${SYNC_SRC_MIND:-../MindLynx-Aistock}"
if [ -d "$SYNC_SRC_MIND" ]; then
    rsync -a --delete "${EXCLUDES[@]}" \
        --exclude='.venv/' \
        --exclude='logs/' \
        --exclude='docs/' \
        --exclude='tests/' \
        --exclude='scripts/' \
        --exclude='api/' \
        --exclude='bot/' \
        --exclude='apps/' \
        --exclude='openspec/' \
        --exclude='templates/' \
        --exclude='docker/' \
        --exclude='.editorconfig' \
        --exclude='.pre-commit-config.yaml' \
        --exclude='.gitattributes' \
        --exclude='.dockerignore' \
        --exclude='setup.cfg' \
        --exclude='pyrightconfig.json' \
        --exclude='SKILL.md' \
        --exclude='AGENTS.md' \
        --exclude='CLAUDE.md' \
        --exclude='wecom_push_types_inventory.md' \
        "$SYNC_SRC_MIND/" systems/MindLynx-Aistock/
    echo "  从 $SYNC_SRC_MIND 同步完成"
else
    echo "  ⚠️  $SYNC_SRC_MIND 不存在，跳过"
fi

# mind_TradingAgent
echo "[3/3] mind_TradingAgent..."
SYNC_SRC_TA="${SYNC_SRC_TA:-../mind_TradingAgent}"
if [ -d "$SYNC_SRC_TA" ]; then
    rsync -a --delete "${EXCLUDES[@]}" \
        --exclude='assets/' \
        --exclude='tests/' \
        --exclude='scripts/' \
        --exclude='Dockerfile' \
        --exclude='docker-compose.yml' \
        --exclude='.dockerignore' \
        --exclude='CHANGELOG.md' \
        --exclude='uv.lock' \
        --exclude='.env.example' \
        --exclude='.env.enterprise.example' \
        "$SYNC_SRC_TA/" systems/mind_TradingAgent/
    echo "  从 $SYNC_SRC_TA 同步完成"
else
    echo "  ⚠️  $SYNC_SRC_TA 不存在，跳过"
fi

echo ""
echo "=== 完成 ==="
du -sh systems/*/
