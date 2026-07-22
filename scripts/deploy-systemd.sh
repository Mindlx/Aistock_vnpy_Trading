#!/usr/bin/env bash
# 同步 systemd 配置到用户目录 & 启用定时器
# 用法: bash scripts/deploy-systemd.sh [--restart-daemons]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SYSD_DIR="${PROJECT_DIR}/config/systemd"
USER_SYSD="${HOME}/.config/systemd/user"

RESTART_DAEMONS=false
if [ "${1:-}" = "--restart-daemons" ]; then
    echo "⚠️  将重启所有常驻 daemon（scheduler/monitor/ml-factor/alpha158/realtime-fusion/data-warehouse）"
    echo "   交易时段重启可能导致监控中断或分析任务中断。"
    read -r -p "确认重启? [y/N] " reply
    case "$reply" in [yY]|[yY][eE][sS]) ;; *) echo "已取消"; exit 0 ;; esac
    RESTART_DAEMONS=true
fi

if [ ! -d "$SYSD_DIR" ]; then
    echo "❌ 未找到 config/systemd/ 目录"
    exit 1
fi

mkdir -p "$USER_SYSD"

# 复制所有 .service 和 .timer 文件
count=0
for f in "$SYSD_DIR"/*.{service,timer}; do
    [ -f "$f" ] || continue
    cp "$f" "$USER_SYSD/"
    count=$((count + 1))
done

# 重新加载 systemd 配置
systemctl --user daemon-reload

# 启用所有 timer 文件
enabled=0
for f in "$USER_SYSD"/*.timer; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    if systemctl --user is-enabled "$name" >/dev/null 2>&1; then
        :  # 已启用
    else
        systemctl --user enable "$name" 2>/dev/null && enabled=$((enabled + 1)) || true
    fi
done

# 可选：重启常驻 daemon 服务
if [ "$RESTART_DAEMONS" = true ]; then
    echo "  重启常驻 daemon ..."
    for s in Aistock_vnpy_Trading-realtime-fusion.service \
             Aistock_vnpy_Trading-scheduler.service \
             Aistock_vnpy_Trading-ml-factor.service \
             Aistock_vnpy_Trading-alpha158.service \
             Aistock_vnpy_Trading-monitor.service \
             Aistock_vnpy_Trading-data-warehouse.service; do
        systemctl --user restart "$s" 2>/dev/null && echo "    ✅ $s" || echo "    ⚠️ $s (未安装)"
    done
fi

echo ""
echo "✅ 已同步 $count 个文件"
echo "   新启用 $enabled 个 timer"
echo ""
echo "验证:"
echo "  systemctl --user list-timers --no-pager | grep -E 'Aistock|aistock|c1test'"
echo "  systemctl --user list-units --all | grep -E 'Aistock|aistock|c1test' | grep running"
