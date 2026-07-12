#!/usr/bin/env python3
"""
Fork Merge Audit — analyze upstream commits for safe cherry-pick candidates.

A portable tool for any git fork project. Detects fork point, classifies
upstream commits by conflict severity, and recommends execution strategy.

Usage:
    python fork_merge_audit.py
    python fork_merge_audit.py --remote origin --branch main
    python fork_merge_audit.py --output report.json --human
    python fork_merge_audit.py --check-hash abc1234 def5678
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime


# ── Noise files (auto-skipped in conflict detection) ──
# Add project-specific noise files via --noise-patterns or edit DEFAULT_NOISE
DEFAULT_NOISE = {
    "CHANGELOG.md", "CHANGELOG",
    "README.md", "README",
    ".env.example",
    ".gitignore",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "Cargo.lock", "Gemfile.lock",
    "go.sum",
}


def git(*args, repo_root=None):
    """Run a git command and return stdout."""
    cwd = repo_root or Path.cwd()
    result = subprocess.run(
        ["git", *args],
        capture_output=True, text=True,
        cwd=cwd,
    )
    if result.returncode != 0:
        print(f"[ERROR] git {' '.join(args)} failed:\n{result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def git_lines(*args, repo_root=None):
    """Run git and return non-empty lines."""
    out = git(*args, repo_root=repo_root)
    return [l for l in out.split("\n") if l.strip()] if out.strip() else []


def detect_remote(repo_root, prefer="upstream"):
    """Detect the upstream remote. Try prefer, then origin, then first remote."""
    remotes = git_lines("remote", repo_root=repo_root)
    if prefer in remotes:
        return prefer
    if "origin" in remotes:
        return "origin"
    if remotes:
        return remotes[0]
    print("[ERROR] No git remotes found. Add one: git remote add upstream <url>", file=sys.stderr)
    sys.exit(1)


def get_merge_base(remote, branch, repo_root):
    """Find the merge-base between local HEAD and remote branch."""
    return git("merge-base", "HEAD", f"{remote}/{branch}", repo_root=repo_root)


def get_upstream_commits(remote, branch, merge_base, repo_root):
    """List commits on upstream since merge-base, newest first."""
    lines = git_lines(
        "log", "--oneline", "--format=%H %h %s",
        f"{merge_base}..{remote}/{branch}",
        repo_root=repo_root,
    )
    commits = []
    for line in reversed(lines):  # oldest first
        parts = line.split(" ", 2)
        if len(parts) >= 3:
            commits.append({
                "full_hash": parts[0],
                "short_hash": parts[1],
                "subject": parts[2],
            })
    return commits


def get_commit_files(commit_hash, repo_root):
    """Get files changed in a commit (list of paths)."""
    return git_lines("diff", "--name-only", f"{commit_hash}^..{commit_hash}", repo_root=repo_root)


def get_commit_stat(commit_hash, repo_root):
    """Get insertions/deletions for a commit."""
    stat_line = git("diff", "--stat", f"{commit_hash}^..{commit_hash}", repo_root=repo_root)
    lines = [l for l in stat_line.split("\n") if l.strip() and "file" in l and "changed" in l]
    if lines:
        return lines[-1].strip()
    return ""


def count_local_commits(merge_base, repo_root):
    """Number of local commits since fork point."""
    return len(git_lines("log", "--oneline", f"{merge_base}..HEAD", repo_root=repo_root))


def get_locally_modified_files(merge_base, repo_root):
    """All files modified locally since merge-base."""
    return set(git_lines("diff", "--name-only", f"{merge_base}..HEAD", repo_root=repo_root))


def get_tracked_files(repo_root):
    """All files tracked by git in the working tree."""
    return set(git_lines("ls-files", repo_root=repo_root))


def classify_subject(subject, patterns=None):
    """Classify commit type from subject line.

    patterns: optional dict of classification → list of prefixes/keywords.
    Default patterns cover common conventions (conventional-commits, etc.).
    """
    default_patterns = {
        "fix": ["fix", "bug", "hotfix", "patch"],
        "feat": ["feat", "feature", "add", "implement"],
        "docs": ["docs", "documentation"],
        "refactor": ["refactor", "重构", "rework", "redesign"],
        "chore": ["chore", "test", "ci:", "build:", "style"],
        "security": ["security", "cve", "vuln", "xsrf", "xss", "csrf"],
        "perf": ["perf", "performance", "optimize", "优化", "改进"],
    }
    if patterns:
        default_patterns.update(patterns)

    s = subject.lower()
    for cls, keywords in default_patterns.items():
        if any(s.startswith(kw) for kw in keywords):
            return cls
        if any(f"{kw}(" in s[:12] for kw in keywords if len(kw) >= 3):
            return cls

    # Chinese keywords (common in CN projects)
    if any(kw in subject for kw in ["修复", "bug"]):
        return "fix"
    if any(kw in subject for kw in ["新增", "支持", "增加", "添加"]):
        return "feat"
    if any(kw in subject for kw in ["优化", "改进"]):
        return "perf"
    if any(kw in subject for kw in ["重构", "重写"]):
        return "refactor"
    if any(kw in subject for kw in ["文档", "说明"]):
        return "docs"

    return "other"


def classify_commit(files, locally_modified, tracked_files, noise_files=None):
    """Classify a commit by conflict severity.

    SAFE          = 0 conflicting files, 0 new files
    NEEDS_REVIEW  = 0-2 conflicting files, or introduces new files
    BLOCKED       = 3+ conflicting files
    """
    if noise_files is None:
        noise_files = set()

    meaningful_conflicting = sorted(
        f for f in files if f in locally_modified and f not in noise_files
    )
    safe_preexisting = sorted(
        f for f in files if f not in locally_modified and f in tracked_files
    )
    new_files = sorted(
        f for f in files if f not in locally_modified and f not in tracked_files
    )
    safe_noise = sorted(f for f in files if f in noise_files and f in locally_modified)
    safe = safe_preexisting + new_files + safe_noise
    conflicting = meaningful_conflicting

    if not conflicting:
        if new_files:
            status = "NEEDS_REVIEW"
        else:
            status = "SAFE"
    elif len(conflicting) <= 2:
        status = "NEEDS_REVIEW"
    else:
        status = "BLOCKED"

    return status, safe, conflicting, new_files


def check_dependency_chain(commits, repo_root):
    """Identify commits that touch same files (potential dependency chains)."""
    file_to_commits = {}
    for c in commits:
        files = get_commit_files(c["full_hash"], repo_root)
        for f in files:
            file_to_commits.setdefault(f, []).append(c["short_hash"])

    return {
        f: hashes for f, hashes in file_to_commits.items() if len(hashes) >= 3
    }


def main():
    parser = argparse.ArgumentParser(
        description="Audit upstream commits for safe cherry-pick candidates"
    )
    parser.add_argument("--remote", default=None,
                        help="Upstream remote name (auto-detect if omitted)")
    parser.add_argument("--branch", default="main", help="Upstream branch name")
    parser.add_argument("--output", default=None, help="Path to write JSON report")
    parser.add_argument("--human", action="store_true",
                        help="Print human-readable summary to stdout")
    parser.add_argument("--check-hash", nargs="+", default=None,
                        help="Only analyze specific commit hashes")
    parser.add_argument("--no-fetch", action="store_true",
                        help="Skip git fetch (use existing remote refs)")
    parser.add_argument("--noise", nargs="*", default=None,
                        help="Extra noise file patterns (space-separated)")
    parser.add_argument("--classify-patterns", default=None,
                        help="JSON file with custom classification patterns")
    args = parser.parse_args()

    repo_root = Path.cwd()

    # 1. Detect remote
    remote = args.remote or detect_remote(repo_root)
    print(f"[0/5] Remote: {remote}/{args.branch}", file=sys.stderr)

    # 2. Fetch upstream (optional)
    if not args.no_fetch:
        print(f"[1/5] Fetching {remote}/{args.branch}...", file=sys.stderr)
        git("fetch", remote, args.branch, repo_root=repo_root)
    else:
        print("[1/5] Skipping fetch (--no-fetch)", file=sys.stderr)

    # 3. Find fork point
    print("[2/5] Finding fork point...", file=sys.stderr)
    merge_base = get_merge_base(remote, args.branch, repo_root)
    base_info = git("log", "--oneline", "-1", merge_base, repo_root=repo_root)
    print(f"  Fork point: {merge_base} ({base_info})", file=sys.stderr)

    # 4. Get file change sets
    print("[3/5] Analyzing local changes...", file=sys.stderr)
    locally_modified = get_locally_modified_files(merge_base, repo_root)
    local_commit_count = count_local_commits(merge_base, repo_root)
    print(f"  Locally modified files: {len(locally_modified)}", file=sys.stderr)

    # 5. Get and analyze upstream commits
    print("[4/5] Analyzing upstream commits...", file=sys.stderr)
    all_commits = get_upstream_commits(remote, args.branch, merge_base, repo_root)
    print(f"  Upstream commits: {len(all_commits)}", file=sys.stderr)

    # Filter if --check-hash given
    if args.check_hash:
        target = set(args.check_hash)
        commits = [c for c in all_commits
                   if c["short_hash"] in target or c["full_hash"] in target]
        if not commits:
            print(f"[WARNING] Hashes not found in upstream range. "
                  f"First hashes: {', '.join(c['short_hash'] for c in all_commits[:5])}...",
                  file=sys.stderr)
        print(f"  Checking {len(commits)} specific commits", file=sys.stderr)
    else:
        commits = all_commits

    # Build noise set
    noise_files = set(DEFAULT_NOISE)
    if args.noise:
        noise_files.update(args.noise)

    # Load custom classification patterns (optional)
    classify_patterns = None
    if args.classify_patterns:
        with open(args.classify_patterns) as f:
            classify_patterns = json.load(f)

    tracked_files = get_tracked_files(repo_root)

    candidates = []
    for commit in commits:
        files = get_commit_files(commit["full_hash"], repo_root)
        status, safe_files, conflicting_files, new_files = classify_commit(
            files, locally_modified, tracked_files, noise_files
        )
        stat = get_commit_stat(commit["full_hash"], repo_root)
        cls = classify_subject(commit["subject"], classify_patterns)

        candidates.append({
            "hash": commit["short_hash"],
            "full_hash": commit["full_hash"],
            "subject": commit["subject"],
            "classification": cls,
            "conflict_status": status,
            "files_total": len(files),
            "files_safe": safe_files,
            "files_conflicting": conflicting_files,
            "files_new": new_files,
            "stat": stat,
        })

    # Dependency chain detection
    dependency_chains = check_dependency_chain(commits, repo_root)

    # Compile summary
    safe_count = sum(1 for c in candidates if c["conflict_status"] == "SAFE")
    review_count = sum(1 for c in candidates if c["conflict_status"] == "NEEDS_REVIEW")
    blocked_count = sum(1 for c in candidates if c["conflict_status"] == "BLOCKED")

    by_cls = {}
    for c in candidates:
        by_cls.setdefault(c["classification"], 0)
        by_cls[c["classification"]] += 1

    safe_fixes = [c for c in candidates
                  if c["conflict_status"] == "SAFE" and c["classification"] == "fix"]
    safe_independent = [c for c in candidates if c["conflict_status"] == "SAFE"]
    needs_review_with_new_files = [c for c in candidates
                                    if c["conflict_status"] == "NEEDS_REVIEW" and c["files_new"]]

    report = {
        "meta": {
            "analysis_date": datetime.now().strftime("%Y-%m-%d"),
            "remote": f"{remote}/{args.branch}",
            "upstream_url": git("remote", "get-url", remote, repo_root=repo_root),
            "fork_point": merge_base,
            "upstream_commits_total": len(all_commits),
            "local_commits_since_fork": local_commit_count,
            "locally_modified_files": len(locally_modified),
        },
        "summary": {
            "safe": safe_count,
            "needs_review": review_count,
            "blocked": blocked_count,
            "safe_fixes": len(safe_fixes),
            "needs_review_with_new_files": len(needs_review_with_new_files),
            "by_classification": dict(sorted(by_cls.items())),
        },
        "dependency_chains": {
            "files_with_3plus_touches": {
                f: hashes for f, hashes in dependency_chains.items()
            } if dependency_chains else {},
        },
        "recommendations": {
            "cherry_pick_safe": [
                {"hash": c["hash"], "subject": c["subject"], "cls": c["classification"]}
                for c in candidates if c["conflict_status"] == "SAFE"
            ],
            "needs_review_try_cherry_pick": [
                {"hash": c["hash"], "subject": c["subject"], "cls": c["classification"],
                 "conflicting": c["files_conflicting"]}
                for c in candidates if c["conflict_status"] == "NEEDS_REVIEW"
            ],
            "blocked_consider_strategy_d": [
                {"hash": c["hash"], "subject": c["subject"], "cls": c["classification"]}
                for c in candidates if c["conflict_status"] == "BLOCKED"
            ],
        },
        "candidates": candidates,
    }

    # 6. Output
    print("[5/5] Writing report...", file=sys.stderr)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"  Report: {args.output}", file=sys.stderr)

    if args.human or not args.output:
        output_json = json.dumps(report, indent=2, ensure_ascii=False)
        print(output_json)
    else:
        s = report["summary"]
        deps = report["dependency_chains"]["files_with_3plus_touches"]
        print(f"\n  SAFE={s['safe']}  NEEDS_REVIEW={s['needs_review']}  BLOCKED={s['blocked']}",
              file=sys.stderr)
        print(f"  Safe fixes: {s['safe_fixes']}  "
              f"By type: {s['by_classification']}", file=sys.stderr)
        print(f"  Dependency chains (files with 3+ touches): {len(deps)}", file=sys.stderr)

    return report


if __name__ == "__main__":
    main()
