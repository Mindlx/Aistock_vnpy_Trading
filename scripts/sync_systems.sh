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
#
# 本地定制保护：同步前校验关键定制代码存在性，同步后验真。
# 如果定制代码被上游覆盖，自动从备份恢复。
# 如需新增保护条目，在 LOCAL_CHECKS 数组中添加 "文件路径:函数签名"。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

# ============================================================
# 本地定制代码保护清单
# 格式: "相对路径:grep模式"
# 同步前会检查每个模式是否存在，同步后会再次验真
# ============================================================
LOCAL_CHECKS=(
    # 自选股操盘建议（融合数据→LLM prompt）
    "systems/MindLynx-Aistock/src/market_analyzer.py:_load_stock_pool_data"
    "systems/MindLynx-Aistock/src/market_analyzer.py:stock_data"
    # 大盘复盘 prompt 模板（八、自选股操盘建议）
    "systems/MindLynx-Aistock/src/market_analyzer.py:八、自选股操盘建议"
    # 大盘复盘 label bug 修复（上游忘记改的）
    "systems/MindLynx-Aistock/src/market_analyzer.py:temperature_label"
    # 根 .env 后备加载（消除 ML 独立 webhook 配置）
    "systems/MindLynx-Aistock/src/config.py:root_env = Path.*parent.parent.parent.parent"
    # LY 独立映射（非融合 normalizer）
    "systems/lynx_vnpy/lynx_signal.py:_l7_score"
)

# 临时备份目录
BACKUP_DIR=$(mktemp -d /tmp/sync_backup_XXXXXX)
BACKUP_COUNT=0
RESTORE_COUNT=0

cleanup() {
    rm -rf "$BACKUP_DIR"
}
trap cleanup EXIT

# 检查并备份本地定制
check_and_backup() {
    local file="$1"
    local pattern="$2"
    local full_path="$SCRIPT_DIR/$file"

    if [ ! -f "$full_path" ]; then
        return 0
    fi

    if grep -q "$pattern" "$full_path" 2>/dev/null; then
        local bak_dir="$BACKUP_DIR/$(dirname "$file")"
        mkdir -p "$bak_dir"
        cp "$full_path" "$bak_dir/"
        BACKUP_COUNT=$((BACKUP_COUNT + 1))
        echo "  📦 已备份: $file ($pattern)"
    fi
}

# 验真并恢复
verify_and_restore() {
    local file="$1"
    local pattern="$2"
    local full_path="$SCRIPT_DIR/$file"
    local bak_file="$BACKUP_DIR/$file"

    if [ ! -f "$full_path" ]; then
        return 0
    fi

    if grep -q "$pattern" "$full_path" 2>/dev/null; then
        echo "  ✅ 通过: $file ($pattern)"
    elif [ -f "$bak_file" ]; then
        cp "$bak_file" "$full_path"
        RESTORE_COUNT=$((RESTORE_COUNT + 1))
        echo "  🔄 恢复: $file ($pattern) — 被上游覆盖，已从备份恢复"
    else
        echo "  ⚠️  ${file}: 定制代码 ($pattern) 丢失且无备份，需手动处理"
    fi
}

echo "=== 三系统同步脚本 ==="
echo "目标: $SCRIPT_DIR/systems/"
echo ""

# ── 预同步检查：备份所有本地定制代码 ──
echo "--- 预同步: 备份本地定制代码 ---"
for entry in "${LOCAL_CHECKS[@]}"; do
    file="${entry%%:*}"
    pattern="${entry#*:}"
    check_and_backup "$file" "$pattern"
done
echo "  已备份 $BACKUP_COUNT 个定制文件"
echo ""

# 通用排除规则
EXCLUDES=(
    --exclude='.git/'
    --exclude='__pycache__/'
    --exclude='*.pyc'
    --exclude='.gitignore'
    --exclude='README.md'
    --exclude='LICENSE'
    --exclude='.venv/'
    --exclude='reports/'
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

# ── 后同步验真：检查本地定制代码是否完整 ──
echo ""
echo "--- 后同步: 验真本地定制代码 ---"
for entry in "${LOCAL_CHECKS[@]}"; do
    file="${entry%%:*}"
    pattern="${entry#*:}"
    verify_and_restore "$file" "$pattern"
done
if [ "$RESTORE_COUNT" -gt 0 ]; then
    echo "  ⚠️  ${RESTORE_COUNT} 个定制文件被上游覆盖，已自动恢复。"
    echo "  建议检查恢复后的代码是否需要适应上游 API 变更。"
else
    echo "  全部 $BACKUP_COUNT 个定制文件完整"
fi

echo ""
echo "=== 完成 ==="
du -sh systems/*/
