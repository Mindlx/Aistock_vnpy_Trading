#!/usr/bin/env bash
# 同步 systemd 配置到用户目录
# 在修改 config/systemd/ 下的文件后运行：
#   bash scripts/deploy-systemd.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SYSD_DIR="${PROJECT_DIR}/config/systemd"
USER_SYSD="${HOME}/.config/systemd/user"

if [ ! -d "$SYSD_DIR" ]; then
    echo "❌ 未找到 config/systemd/ 目录"
    exit 1
fi

mkdir -p "$USER_SYSD"

# 复制所有服务文件和定时器文件
count=0
for f in "$SYSD_DIR"/Aistock_vnpy_Trading-*.{service,timer}; do
    [ -f "$f" ] || continue
    cp "$f" "$USER_SYSD/"
    count=$((count + 1))
done

systemctl --user daemon-reload

echo "✅ 已同步 $count 个文件到 ${USER_SYSD}"
echo "   运行 systemctl --user list-timers --no-pager 验证"
