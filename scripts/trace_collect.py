#!/usr/bin/env python3
"""
trace_collect.py — L1 轨迹采集脚本

用法:
  # 采集当前会话（手动填写关键字段）
  python scripts/trace_collect.py --session-id ses_xxx --requirement "修复bug" \
      --conclusion "根因是内存泄漏，添加free()调用" \
      --outcome "commit abc123: fix memory leak"

  # 只创建骨架（稍后手动补充）
  python scripts/trace_collect.py --session-id ses_xxx --init-only

  # 列出已采集的轨迹
  python scripts/trace_collect.py --list

Schema 定义: docs/research/loop-engineering-research/c1skill-prompt-template.md
数据集目录: data/traces/
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

TRACES_DIR = Path(__file__).resolve().parent.parent / "data" / "traces"
TRACES_DIR.mkdir(parents=True, exist_ok=True)

TRACE_SCHEMA = {
    "trace_id": "",
    "session_id": "",
    "timestamp": "",
    "requirement": {
        "source": "direct_instruction",
        "description": ""
    },
    "oracle_analysis": {
        "root_cause": "",
        "initial_recommendation": ""
    },
    "c1skill_validation": {
        "stage_4_counter_arguments": "",
        "stage_7_final_conclusion": ""
    },
    "final_outcome": {
        "patch_summary": "",
        "commit_hash": ""
    }
}


def next_trace_id() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    existing = list(TRACES_DIR.glob(f"{today}_*.json"))
    seq = len(existing) + 1
    return f"{today}_{seq:03d}"


def list_traces():
    files = sorted(TRACES_DIR.glob("*.json"))
    if not files:
        print("📭 尚无轨迹数据")
        return
    print(f"📊 共 {len(files)} 条轨迹:")
    for f in files[-20:]:  # 最近 20 条
        try:
            with open(f) as fh:
                data = json.load(fh)
            desc = data.get("requirement", {}).get("description", "")[:60]
            conclusion = data.get("c1skill_validation", {}).get("stage_7_final_conclusion", "")[:40]
            print(f"  {f.stem}  |  {desc}  →  {conclusion}")
        except Exception:
            print(f"  {f.stem}  |  ⚠️ 读取失败")


def main():
    parser = argparse.ArgumentParser(description="c1skill 推理轨迹采集 L1 工具")
    parser.add_argument("--session-id", default="", help="OpenCode session ID (ses_xxx)")
    parser.add_argument("--requirement", default="", help="任务需求描述")
    parser.add_argument("--root-cause", default="", help="Oracle 根因分析结论")
    parser.add_argument("--oracle-recommendation", default="", help="Oracle 初始推荐方案")
    parser.add_argument("--counter-arguments", default="", help="c1skill 反方论据")
    parser.add_argument("--conclusion", default="", help="c1skill 最终结论")
    parser.add_argument("--outcome", default="", help="代码变更摘要")
    parser.add_argument("--commit", default="", help="commit hash")
    parser.add_argument("--init-only", action="store_true", help="仅创建骨架，不填内容")
    parser.add_argument("--list", action="store_true", help="列出已采集轨迹")
    args = parser.parse_args()

    if args.list:
        list_traces()
        return

    trace = dict(TRACE_SCHEMA)
    trace["trace_id"] = next_trace_id()
    trace["session_id"] = args.session_id
    trace["timestamp"] = datetime.now().isoformat()

    if not args.init_only:
        trace["requirement"]["description"] = args.requirement
        trace["oracle_analysis"]["root_cause"] = args.root_cause
        trace["oracle_analysis"]["initial_recommendation"] = args.oracle_recommendation
        trace["c1skill_validation"]["stage_4_counter_arguments"] = args.counter_arguments
        trace["c1skill_validation"]["stage_7_final_conclusion"] = args.conclusion
        trace["final_outcome"]["patch_summary"] = args.outcome
        trace["final_outcome"]["commit_hash"] = args.commit

    # 检查完整性
    filled = sum(1 for v in trace.values() if isinstance(v, str) and v)
    nested_filled = 0
    for k, v in trace.items():
        if isinstance(v, dict):
            nested_filled += sum(1 for vv in v.values() if vv)

    filepath = TRACES_DIR / f"{trace['trace_id']}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(trace, f, ensure_ascii=False, indent=2)

    print(f"✅ 轨迹已保存: {filepath}")
    print(f"   填写进度: {filled + nested_filled}/{sum(len(v) if isinstance(v, dict) else 1 for v in TRACE_SCHEMA.values())} 字段")
    if args.init_only:
        print(f"   💡 编辑文件后重新运行: 无需 --init-only 即可追加内容")


if __name__ == "__main__":
    main()
