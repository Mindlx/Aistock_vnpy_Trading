<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **Aistock_vnpy_Trading** (12769 symbols, 23592 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/Aistock_vnpy_Trading/context` | Codebase overview, check index freshness |
| `gitnexus://repo/Aistock_vnpy_Trading/clusters` | All functional areas |
| `gitnexus://repo/Aistock_vnpy_Trading/processes` | All execution flows |
| `gitnexus://repo/Aistock_vnpy_Trading/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

## Default Workflow

Every non-trivial analysis automatically follows the **c1skill 7 Stage** framework:
1. **原架构理解** — Understand intent & constraints
2. **事实声明** — List verifiable facts (code/logs/config)
3. **证据验证** — Verify each fact
4. **缺失分析** — Identify blind spots
5. **反方论据** — 2-3 adversarial counter-points with responses
6. **方案评估** — Compare options
7. **风险监控** — Risks & tracking metrics
8. **最终结论** — Executable conclusion

Simple tasks may skip stages. The framework ensures completeness, not rigidity.

## Automation Chain (MANDATORY)

The following chain activates automatically without user prompting:

1. **Oracle triggers c1skill**: Whenever the user requests or you invoke Oracle for deep analysis, **automatically follow up with c1skill** after Oracle completes (no need to ask permission).
2. **c1skill triggers trace capture**: Whenever c1skill is invoked and produces a conclusion, **automatically run trace_collect.py** to save the reasoning trace to `data/traces/`.

Example flow:
```
user: "分析这个bug"
→ 你: run Oracle → automatically chain c1skill → automatically save trace
```

The trace_collect.py command format:
```bash
python scripts/trace_collect.py \
  --session-id "$SESSION_ID" \
  --requirement "task description" \
  --root-cause "oracle root cause" \
  --oracle-recommendation "oracle initial recommendation" \
  --counter-arguments "c1skill counter arguments" \
  --conclusion "c1skill final conclusion" \
  --outcome "code change summary" \
  --commit "$COMMIT_HASH"
```
