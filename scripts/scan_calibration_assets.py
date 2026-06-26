#!/usr/bin/env python3
"""
扫描代码库中的 @calibration 标记，生成受保护文件清单。
用法:
    python scripts/scan_calibration_assets.py              # 输出到 stdout
    python scripts/scan_calibration_assets.py --output docs/protected_files.txt
"""
import argparse
import glob
import re
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 排除目录
EXCLUDE_DIRS = {
    '.venv', '__pycache__', '.git', '.mypy_cache', '.pytest_cache',
    'node_modules', '.opencode', '.agents',
}

def scan_calibration_markers(root: str) -> dict[str, list[str]]:
    """扫描所有文件中的 @calibration 标记"""
    results = {}
    pattern = re.compile(r'@calibration\s+(.+?)(?:\s*[#\n]|$)')
    
    for dirpath, dirnames, filenames in os.walk(root):
        # 跳过排除目录
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        
        for f in filenames:
            if not f.endswith(('.py', '.yaml', '.yml', '.json', '.md')):
                continue
            filepath = os.path.join(dirpath, f)
            relpath = os.path.relpath(filepath, root)
            
            try:
                with open(filepath, 'r', encoding='utf-8') as fh:
                    content = fh.read()
            except (UnicodeDecodeError, IOError):
                continue
            
            matches = pattern.findall(content)
            if matches:
                results[relpath] = [m.strip() for m in matches]
    
    return results


def generate_protected_files(calibration_data: dict[str, list[str]]) -> str:
    """生成受保护文件清单"""
    lines = [
        "# 受保护文件清单 — 合入上游时禁止全量替换",
        "# 自动生成自: python scripts/scan_calibration_assets.py",
        f"# 生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "# 格式: 文件路径 | 校准内容",
        "#",
    ]
    
    for filepath, markers in sorted(calibration_data.items()):
        for marker in markers:
            lines.append(f"{filepath} | {marker}")
    
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='扫描 @calibration 标记')
    parser.add_argument('--output', '-o', help='输出文件路径（默认 stdout）')
    args = parser.parse_args()
    
    calibration_data = scan_calibration_markers(PROJECT_ROOT)
    
    if not calibration_data:
        print("未找到 @calibration 标记")
        sys.exit(1)
    
    output = generate_protected_files(calibration_data)
    
    if args.output:
        output_path = os.path.join(PROJECT_ROOT, args.output)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(output)
        print(f"✅ 已生成受保护文件清单: {args.output}")
    else:
        print(output)
    
    total_markers = sum(len(v) for v in calibration_data.values())
    print(f"\n总计: {len(calibration_data)} 个文件, {total_markers} 个校准标记")


if __name__ == '__main__':
    main()
