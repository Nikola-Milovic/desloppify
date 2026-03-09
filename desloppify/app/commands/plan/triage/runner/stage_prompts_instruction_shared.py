"""Shared static text blocks for triage stage prompts."""

from __future__ import annotations

_STAGES = ("observe", "reflect", "organize", "enrich", "sense-check")

_PREAMBLE = """\
You are a triage subagent with full codebase access and the desloppify CLI.
Your job is to complete the **{stage}** stage of triage planning.

Repo root: {repo_root}

## Standards

You are expected to produce **exceptional** work. The output of this triage becomes the
actual plan that an executor follows — if you are lazy, vague, or sloppy, real work gets
wasted. Concretely:

- **Read the actual source code.** Every opinion you form must come from reading the file,
  not from reading the issue title. Issues frequently exaggerate, miscount, or describe
  code that has already been fixed. Trust nothing until you verify it.
- **Have specific opinions.** "This seems like it could be an issue" is worthless. "This is
  a false positive because line 47 already uses the pattern the issue suggests" is useful.
- **Do the hard thinking.** If two issues seem related, figure out WHY. If something should
  be skipped, explain the specific reason for THIS issue, not a generic category.
- **Don't take shortcuts.** Reading 5 files and extrapolating to 30 is lazy. Read all 30.
  If you have too many, use subagents to parallelize — don't skip.

Use the desloppify CLI to record your work. Every command you run mutates plan.json directly.
The orchestrator will review your work and confirm the stage after you record it.

**CRITICAL: Only run commands for YOUR stage ({stage}).** Do NOT re-run earlier stages
(e.g., do not run `--stage observe` if you are the organize subagent). Earlier stages
are already confirmed. Re-running them will corrupt the plan state.
"""

_CLI_REFERENCE = """\
## CLI Command Reference

### Stage recording
```
desloppify plan triage --stage observe --report "<analysis>"
desloppify plan triage --stage reflect --report "<strategy>" --attestation "<80+ chars>"
desloppify plan triage --stage organize --report "<summary>" --attestation "<80+ chars>"
desloppify plan triage --stage enrich --report "<enrichment summary>" --attestation "<80+ chars>"
desloppify plan triage --stage sense-check --report "<verification summary>" --attestation "<80+ chars>"
```

### Cluster management
```
desloppify plan cluster create <name> --description "<what this cluster addresses>"
desloppify plan cluster add <name> <issue-patterns...>
desloppify plan cluster update <name> --description "<desc>" --steps "step 1" "step 2"
desloppify plan cluster update <name> --add-step "<title>" --detail "<sub-points>" --effort small --issue-refs <id1> <id2>
desloppify plan cluster update <name> --update-step N --detail "<sub-points>" --effort medium --issue-refs <id1>
desloppify plan cluster update <name> --depends-on <other-cluster-name>
desloppify plan cluster show <name>
desloppify plan cluster list --verbose
```

### Skip/dismiss
```
desloppify plan skip --permanent <pattern> --note "<reason>" --attest "<attestation>"
```

### Effort tags
Valid values: trivial, small, medium, large. Set on steps via --effort flag.
"""


__all__ = ["_CLI_REFERENCE", "_PREAMBLE", "_STAGES"]
