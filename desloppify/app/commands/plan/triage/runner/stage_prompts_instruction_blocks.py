"""Per-stage instruction text blocks for triage prompts."""

from __future__ import annotations


def _observe_instructions() -> str:
    return """\
## OBSERVE Stage Instructions

Your task: verify every open review issue against the actual source code.

**The review system has a high false-positive rate.** Issues frequently:
- Claim "12 unsafe casts" when there are actually 2
- Describe code that was already refactored
- Propose over-engineering that would make things worse
- Count props/returns/args wrong

Your job is to catch these. An observe report that just restates issue titles is **worthless**.
The value you add is reading the actual code and forming an independent judgment.

Do NOT analyze themes, strategy, or relationships between issues. That's the next stage (reflect).
Just verify: is each issue real?

**CRITICAL: You must cite specific issue IDs (hash prefixes like [abcd1234]) in your report.**
The confirmation gate requires citing at least 10% of issues (or 5, whichever is smaller).

**USE SUBAGENTS to parallelize this work.** Launch parallel subagents — one per dimension
group — to investigate concurrently. Each subagent MUST:
- Open and read the actual source file for EVERY assigned issue
- Verify specific claims: count the actual casts, props, returns, line count
- Check if the suggested fix already exists (common false positive)
- Report a clear verdict per issue: genuine / false positive / exaggerated / over-engineering

Example subagent split for 90 issues across 17 dimensions:
- Subagent 1: architecture + organization (cross_module_architecture, package_organization, high_level_elegance)
- Subagent 2: abstraction + design (abstraction_fitness, design_coherence, mid_level_elegance)
- Subagent 3: duplicates + contracts (contract_coherence, api_surface_coherence, low_level_elegance)
- Subagent 4: migrations + debt + conventions (incomplete_migration, ai_generated_debt, convention_outlier, naming_quality)
- Subagent 5: type safety + errors + tests (type_safety, error_consistency, test_strategy, initialization_coupling, dependency_health)

**What a GOOD observe report looks like:**
- "[34580232] taskType is plain string — FALSE POSITIVE. Uses branded string union KnownTaskType
  with ~25 literals in src/types/database.ts line 50. The issue describes code that doesn't exist."
- "[b634fc71] useGenerationsPaneController returns 60+ values — GENUINE. Confirmed 65 properties
  at lines 217-282. Mixes pane lifecycle, filters, gallery data, interaction, and navigation."

**What a LAZY observe report looks like (will be rejected):**
- "There are several convention issues that should be addressed"
- "The type safety dimension has some genuine concerns"
- Listing issue titles without any verification or independent analysis

**Your report must include for EVERY issue:**
1. The hash prefix
2. Your verdict (genuine / false positive / exaggerated / over-engineering)
3. The specific evidence (what you found when you read the code)

When done, run:
```
desloppify plan triage --stage observe --report "<your analysis with [hash] citations>"
```
"""


def _reflect_instructions() -> str:
    return """\
## REFLECT Stage Instructions

Your task: using the verdicts from observe, design the cluster structure.

**A strategy is NOT a restatement of observe.** Observe says "here's what I found." Reflect
says "here's what we should DO about it, and here's what we should NOT do, and here's WHY."

### What you must do:

1. **Filter:** which issues are genuine (from observe verdicts)?
2. **Map:** for each genuine issue, what file/directory does it touch?
3. **Group:** which issues share files or directories? These become clusters.
4. **Skip:** which issues should be skipped? (with per-issue justification — "low priority" is
   not a justification; "the fix would add a 50-line abstraction to save 3 lines of duplication" is)
5. **Order:** which clusters depend on others? What's the execution sequence?
6. **Check recurring patterns** — compare current issues against resolved history. If the same
   dimension keeps producing issues, that's a root cause that needs addressing, not just
   another round of fixes.

### Your report MUST include a concrete cluster blueprint

This blueprint is what the organize stage will execute. Be specific:
```
Cluster "media-lightbox-hooks": issues X, Y, Z (all in src/domains/media-lightbox/)
Cluster "task-typing": issues A, B (both touch src/types/database.ts)
Skip: issue W (false positive per observe), issue V (over-engineering — fix adds 50 lines for 3 lines saved)
```

### What a LAZY reflect looks like (will be rejected):
- Restating observe findings in slightly different words
- "We should prioritize high-impact items and defer low-priority ones"
- A bulleted list of dimensions without any strategic thinking
- Ignoring recurring patterns
- No cluster blueprint (just vague grouping ideas)

### What a GOOD reflect looks like:
- "50% false positive rate. Of 34 issues, 17 are genuine. 10 of those are batch-scriptable
  convention fixes (zero risk, 30 min) — cluster 'convention-batch'. The remaining 7 split into
  3 clusters by file proximity: 'media-lightbox-hooks' (issues X,Y,Z — all in src/domains/media-lightbox/),
  'timeline-cleanup' (issues A,B,C — touching Timeline components), 'task-typing' (issues D,E).
  Skip: issue W (false positive), issue V (over-engineering).
  design_coherence recurs (2 resolved, 5 open) but only 1 of the 5 actually warrants work."

When done, run:
```
desloppify plan triage --stage reflect --report "<your strategy with cluster blueprint>" --attestation "<80+ chars mentioning dimensions or recurring patterns>"
```
"""


def _organize_instructions() -> str:
    return """\
## ORGANIZE Stage Instructions

Your task: execute the cluster blueprint from the reflect stage.

The reflect report contains a specific plan: which clusters to create, which issues go
where, what to skip. Build it using the CLI. If something doesn't work as planned
(issue hash doesn't match, file proximity doesn't hold), adjust and document why.

This stage should be largely mechanical. If you find yourself making major strategic
decisions, something went wrong in reflect — the strategy should already be decided.

### Process

1. Review the reflect report's cluster blueprint (provided below)
2. **Skip false positives and over-engineering** identified in observe/reflect. Every skip needs a
   per-issue justification — not "low priority" but "false positive: the code at line 47
   already uses named constants, contradicting the issue's claim":
   ```
   desloppify plan skip --permanent <pattern> --note "<specific per-issue reason>" --attest "<attestation>"
   ```
3. Create clusters as specified in the blueprint:
   `desloppify plan cluster create <name> --description "..."`
4. Add issues: `desloppify plan cluster add <name> <patterns...>`
5. Add steps that consolidate: one step per file or logical change, NOT one step per issue
6. Set `--effort` on each step individually (trivial/small/medium/large)
7. Set `--depends-on` when clusters touch overlapping files

### Quality gates (the confirmation will check these)

Before recording, verify:
- [ ] Every cluster name describes an area or specific change, not a problem type
- [ ] No cluster has issues from 5+ unrelated directories (theme-group smell)
- [ ] Step count < issue count (consolidation happened)
- [ ] Every skip has a specific per-issue reason (not "low priority")
- [ ] Overlapping clusters have --depends-on set
- [ ] Cluster descriptions describe the WORK, not the PROBLEMS

Every review issue must end up in a cluster OR be skipped.

When done, run:
```
desloppify plan triage --stage organize --report "<summary of priorities and organization>" --attestation "<80+ chars mentioning cluster names>"
```
"""


def _enrich_instructions() -> str:
    return """\
## ENRICH Stage Instructions

Your task: make EVERY step executor-ready. The test: could a developer who has never seen
this codebase read your step detail and make the change without asking a single question?

If the answer is "they'd need to figure out which file" or "they'd need to understand the
context" — your step is not ready. Be specific enough that the work is mechanical.

### Requirements (ALL BLOCKING — confirmation will reject if not met)

1. Every step MUST have `--detail` with 80+ chars INCLUDING at least one file path (src/... or supabase/...)
2. Every step MUST have `--issue-refs` linking it to specific review issue hash(es)
3. Every step MUST have `--effort` tag (trivial/small/medium/large) — set INDIVIDUALLY, not bulk
4. File paths in detail MUST exist on disk (validator checks this)
5. No step may reference a skipped/wontfixed issue in its issue_refs

### How to enrich

**USE SUBAGENTS — one per cluster.** Each subagent MUST:

1. Run `desloppify plan cluster show <name>` to get current steps and issue list
2. **Read the actual source file for every step** — not just the issue description.
   The issue says what's wrong; you need to see the code to say what to DO.
3. Write detail that includes: the file path, the specific location (line range or
   function name), and the exact change to make
4. Set effort based on the ACTUAL complexity you see in the code, not a guess

### Common lazy patterns to avoid

**Copying the issue description as step detail.** The issue says "useGenerationsPaneController
returns 60+ values mixing concerns." That's a PROBLEM description. The step detail should say
"In src/shared/components/GenerationsPane/hooks/useGenerationsPaneController.ts (283 lines),
extract lines 45-89 (filter state: activeFilter, setActiveFilter, filterOptions, applyFilter)
into a new useGenerationFilters hook. The controller imports and spreads the sub-hook's return."

**Vague action verbs.** "Refactor", "clean up", "improve", "fix" are not actions.
"Extract lines 45-89 into useGenerationFilters", "delete lines 12-18", "rename the file
from X to Y and update the 3 imports in A.tsx, B.tsx, C.tsx" are actions.

**Guessing file paths.** If you write `src/shared/lib/jsonNarrowing.ts` and it doesn't exist,
confirmation will block. READ the file system. Only reference files you've verified exist.

**Bulk effort tags.** Don't mark everything "small". A file rename with 2 imports is "trivial".
Decomposing a 400-line hook into 3 sub-hooks is "medium" or "large". Think about each one.

### Examples

**GOOD step detail:**
```
--detail "In src/shared/hooks/billing/useAutoTopup.ts lines 118-129, add onMutate handler
to capture previous queryClient state before optimistic update. In onError callback, restore
previous state and change showToast from false to true."
--issue-refs 79baeebf --effort small
```

**BAD step detail (will be rejected):**
```
--detail "Fix silent error swallowing"  # No file, no location, no action
--detail "Decompose god-hooks"  # What file? What hooks? Into what?
--detail "Address the issues identified in the observe stage"  # This says nothing
```

### Do NOT mark steps as done

Use `--update-step N` to add detail, effort, and issue-refs.
Do NOT use `--done-step` — steps are only marked done when actual code changes are made.

### File path rules

Only reference files that exist RIGHT NOW. Do not reference files that a step will create
(e.g., a new shared module) or rename targets (the new filename after a rename). Reference
the current source file and describe what will change. The path validator will block
confirmation if paths don't exist on disk.

When done, run:
```
desloppify plan triage --stage enrich --report "<enrichment summary>" --attestation "<80+ chars mentioning cluster names>"
```
"""


def _sense_check_instructions() -> str:
    return """\
## SENSE-CHECK Stage Instructions

This stage is handled by two parallel subagents. If you are being run as a
single-subprocess fallback, perform BOTH the content and structure checks below.

### Content Check (per cluster)
For EVERY step in every cluster, read the actual source file and verify:
1. LINE NUMBERS: Does the code at the claimed lines match the step description?
2. NAMES: Do function/variable/type names in the step exist in the file?
3. COUNTS: Are counts ("update the 3 imports") accurate?
4. STALENESS: Is the problem still present, or already fixed?
5. VAGUENESS: Could a developer with zero context execute this step?
6. EFFORT TAGS: Does the tag match actual scope?
7. DUPLICATES: Flag steps that duplicate work in another cluster.
8. OVER-ENGINEERING: Would this change make the codebase *worse*? Flag steps that:
   - Add abstractions, wrappers, or indirection for a one-time operation
   - Introduce unnecessary config/feature-flags/generalization
   - Make simple code harder to read for marginal benefit
   - Gold-plate beyond what the issue actually requires
   - Trade one smell for a worse one (e.g. fix duplication by adding a fragile base class)
   Remove or simplify over-engineered steps. If the whole cluster is net-negative, say so.

Fix with: `desloppify plan cluster update <name> --update-step N --detail "..." --effort <tag>`

### Structure Check (global)
Build a file-touch graph and check:
1. SHARED FILES: Two clusters touching same file without --depends-on → add dependency
2. MISSING CASCADE: Rename/remove without importer updates → add cascade step
3. CIRCULAR DEPS: Flag cycles, don't add them

Fix with: `desloppify plan cluster update <name> --depends-on <other>`
Fix with: `desloppify plan cluster update <name> --add-step "..." --detail "..." --effort trivial --issue-refs <hash>`

When done, run:
```
desloppify plan triage --stage sense-check --report "<findings summary>"
```
"""


_STAGE_INSTRUCTIONS = {
    "observe": _observe_instructions,
    "reflect": _reflect_instructions,
    "organize": _organize_instructions,
    "enrich": _enrich_instructions,
    "sense-check": _sense_check_instructions,
}


__all__ = [
    "_STAGE_INSTRUCTIONS",
    "_enrich_instructions",
    "_observe_instructions",
    "_organize_instructions",
    "_reflect_instructions",
    "_sense_check_instructions",
]
