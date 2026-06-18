#!/usr/bin/env python3
"""
Trace Auto Collector — systemd timer 兜底扫描
==============================================
每 5 分钟检查 OpenCode 会话数据库，发现未处理的 Oracle+c1skill 会话后自动提取轨迹。

查询策略：
  1. 从 OpenCode 的 message 表查找 agent="oracle" 的 assistant 消息
  2. 检查 data/traces/ 下是否已有对应 session 的轨迹文件
  3. 如未处理 → 调用 trace_collect.py 生成轨迹

适用于：
  - systemd timer: Aistock_vnpy_Trading-trace-collect.{service,timer}
  - 手动运行: python scripts/trace_auto_collect.py
"""

import json
import os
import subprocess
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# ── 路径 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRACES_DIR = PROJECT_ROOT / "data" / "traces"
OPCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
TRACE_COLLECT = PROJECT_ROOT / "scripts" / "trace_collect.py"

# ── 追踪已处理 session 的标记文件 ──
PROCESSED_FILE = PROJECT_ROOT / "data" / "traces" / ".processed_sessions.json"


def load_processed() -> set:
    """读取已处理 session ID 列表"""
    if PROCESSED_FILE.exists():
        return set(json.loads(PROCESSED_FILE.read_text()))
    return set()


def save_processed(processed: set):
    """保存已处理 session ID 列表"""
    PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROCESSED_FILE.write_text(json.dumps(sorted(processed), ensure_ascii=False, indent=2))


def get_oracle_sessions() -> list[dict]:
    """从 OpenCode DB 查询包含 Oracle 调用的会话"""
    if not OPCODE_DB.exists():
        print(f"[trace-auto] OpenCode DB not found: {OPCODE_DB}")
        return []

    conn = sqlite3.connect(str(OPCODE_DB))
    conn.row_factory = sqlite3.Row

    # 找当前项目目录
    project_root_str = str(PROJECT_ROOT)
    c = conn.execute(
        "SELECT id FROM project WHERE id IN (SELECT project_id FROM session WHERE directory = ? LIMIT 1)",
        (project_root_str,),
    )
    project_row = c.fetchone()
    if not project_row:
        print(f"[trace-auto] Project not found in DB for: {project_root_str}")
        conn.close()
        return []

    # 查找 agent="oracle" 的 assistant 消息（最近7天）
    week_ago = int((time.time() - 7 * 86400) * 1000)  # ms timestamp
    c = conn.execute(
        """
        SELECT DISTINCT m.session_id, s.title, s.slug,
               MAX(m.time_created) as last_oracle_time
        FROM message m
        JOIN session s ON m.session_id = s.id
        WHERE json_extract(m.data, '$.role') = 'assistant'
          AND json_extract(m.data, '$.agent') = 'oracle'
          AND m.time_created > ?
          AND s.directory = ?
        GROUP BY m.session_id
        ORDER BY last_oracle_time DESC
        """,
        (week_ago, project_root_str),
    )

    sessions = []
    for row in c.fetchall():
        sessions.append({
            "id": row["session_id"],
            "title": row["title"],
            "slug": row["slug"],
            "last_oracle_time": row["last_oracle_time"],
        })

    conn.close()
    return sessions


def has_trace_for_session(session_id: str) -> bool:
    """检查 data/traces/ 下是否已有该 session 的轨迹文件"""
    for f in TRACES_DIR.glob("*.json"):
        if f.name.startswith(".processed_sessions"):
            continue
        try:
            data = json.loads(f.read_text())
            if data.get("session_id") == session_id:
                return True
        except (json.JSONDecodeError, KeyError):
            continue
    return False


def extract_trace(session_id: str, title: str) -> bool:
    """为一个 session 生成轨迹文件"""
    print(f"[trace-auto] 提取轨迹: {session_id} — {title[:60]}")

    # 从 session ID 和 title 中推断 requirement
    # title 通常格式如 "[CONTEXT] Aistock_vnpy_Trading project — (@oracle subagent)"
    requirement = title or f"Oracle analysis session {session_id[:16]}"
    if "@oracle" in title:
        requirement = title.split("@oracle")[0].strip()
        if requirement.startswith("[CONTEXT]"):
            requirement = requirement[9:].strip()
    requirement = requirement[:100]  # 截断过长标题

    try:
        result = subprocess.run(
            [
                sys.executable,
                str(TRACE_COLLECT),
                "--session-id", session_id,
                "--requirement", requirement,
                "--root-cause", "auto-collected from OpenCode session (Oracle invocation detected)",
                "--oracle-recommendation", "auto-collected",
                "--counter-arguments", "auto-collected",
                "--conclusion", "auto-collected",
                "--outcome", "auto-collected",
                "--commit", "none",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            print(f"[trace-auto] ✅ 轨迹已保存: {result.stdout.strip()}")
            return True
        else:
            print(f"[trace-auto] ❌ trace_collect.py 失败 (rc={result.returncode}): {result.stderr[:200]}")
            return False
    except subprocess.TimeoutExpired:
        print(f"[trace-auto] ❌ trace_collect.py 超时")
        return False
    except Exception as e:
        print(f"[trace-auto] ❌ 异常: {e}")
        return False


def main():
    print(f"[trace-auto] ⏰ 扫描开始: {datetime.now().isoformat()}")

    processed = load_processed()
    sessions = get_oracle_sessions()

    if not sessions:
        print(f"[trace-auto] 未发现新的 Oracle 会话")
        save_processed(processed)
        return

    new_count = 0
    for s in sessions:
        if s["id"] in processed:
            continue
        if has_trace_for_session(s["id"]):
            print(f"[trace-auto] 已有轨迹,跳过: {s['id'][:20]}")
            processed.add(s["id"])
            continue

        ok = extract_trace(s["id"], s["slug"] or s["title"])
        if ok:
            processed.add(s["id"])
            new_count += 1

    save_processed(processed)
    print(f"[trace-auto] ✅ 完成: {new_count} 新轨迹, {len(processed)} 总计已处理")


if __name__ == "__main__":
    main()
