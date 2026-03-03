"""Stage execution handlers for plan triage workflow."""

from __future__ import annotations

import argparse

from desloppify.app.commands.helpers.display import short_issue_id
from desloppify.app.commands.helpers.runtime import command_runtime
from desloppify.app.commands.plan.triage_playbook import (
    TRIAGE_CMD_ORGANIZE,
)
from desloppify.core.output import colorize
from desloppify.engine.plan import (
    append_log_entry,
    collect_triage_input,
    detect_recurring_patterns,
    extract_issue_citations,
    load_plan,
    save_plan,
)
from desloppify.state import utc_now

from .confirmations import _MIN_ATTESTATION_LEN, _validate_attestation
from .display import _print_organize_result, _print_reflect_result, _show_plan_summary
from .helpers import (
    _apply_completion,
    _cascade_clear_later_confirmations,
    _has_triage_in_queue,
    _inject_triage_stages,
    _manual_clusters_with_issues,
    _observe_dimension_breakdown,
    _open_review_ids_from_state,
    _print_cascade_clear_feedback,
    _triage_coverage,
)
from .stage_helpers import _unenriched_clusters


def _resolve_reusable_report(
    report: str | None,
    existing_stage: dict | None,
) -> tuple[str | None, bool]:
    if report:
        return report, False
    if existing_stage and existing_stage.get("report"):
        return existing_stage["report"], True
    return None, False


def _print_observe_report_requirement() -> None:
    print(colorize("  --report is required for --stage observe.", "red"))
    print(colorize("  Write an analysis of the issues: themes, root causes, contradictions.", "dim"))
    print(colorize("  Identify issues that contradict each other (opposite recommendations).", "dim"))
    print(colorize("  Do NOT just list issue IDs — describe what you actually observe.", "dim"))


def _record_observe_stage(
    stages: dict,
    *,
    report: str,
    issue_count: int,
    cited_ids: list[str],
    existing_stage: dict | None,
    is_reuse: bool,
) -> list[str]:
    stages["observe"] = {
        "stage": "observe",
        "report": report,
        "cited_ids": cited_ids,
        "timestamp": utc_now(),
        "issue_count": issue_count,
    }
    if is_reuse and existing_stage and existing_stage.get("confirmed_at"):
        stages["observe"]["confirmed_at"] = existing_stage["confirmed_at"]
        stages["observe"]["confirmed_text"] = existing_stage.get("confirmed_text", "")
    cleared = _cascade_clear_later_confirmations(stages, "observe")
    if not is_reuse:
        stages["observe"].pop("confirmed_at", None)
        stages["observe"].pop("confirmed_text", None)
    return cleared


def _print_reflect_report_requirement() -> None:
    print(colorize("  --report is required for --stage reflect.", "red"))
    print(colorize("  Compare current issues against completed work and form a holistic strategy:", "dim"))
    print(colorize("  - What clusters were previously completed? Did fixes hold?", "dim"))
    print(colorize("  - Are any dimensions recurring (resolved before, open again)?", "dim"))
    print(colorize("  - What contradictions did you find? Which direction will you take?", "dim"))
    print(colorize("  - Big picture: what to prioritize, what to defer, what to skip?", "dim"))


def _auto_confirm_observe_if_attested(
    *,
    plan: dict,
    stages: dict,
    attestation: str | None,
    triage_input,
) -> bool:
    if stages["observe"].get("confirmed_at"):
        return True
    if not attestation or len(attestation.strip()) < _MIN_ATTESTATION_LEN:
        print(colorize("  Cannot reflect: observe stage not confirmed.", "red"))
        print(colorize("  Run: desloppify plan triage --confirm observe", "dim"))
        print(colorize("  Or pass --attestation to auto-confirm observe inline.", "dim"))
        return False
    _by_dim, dim_names = _observe_dimension_breakdown(triage_input)
    validation_err = _validate_attestation(
        attestation.strip(),
        "observe",
        dimensions=dim_names,
    )
    if validation_err:
        print(colorize(f"  {validation_err}", "red"))
        return False
    stages["observe"]["confirmed_at"] = utc_now()
    stages["observe"]["confirmed_text"] = attestation.strip()
    save_plan(plan)
    print(colorize("  ✓ Observe auto-confirmed via --attestation.", "green"))
    return True


def _validate_recurring_dimension_mentions(
    *,
    report: str,
    recurring_dims: list[str],
    recurring: dict,
) -> bool:
    if not recurring_dims:
        return True
    report_lower = report.lower()
    mentioned = [dim for dim in recurring_dims if dim.lower() in report_lower]
    if mentioned:
        return True
    print(colorize("  Recurring patterns detected but not addressed in report:", "red"))
    for dim in recurring_dims:
        info = recurring[dim]
        print(colorize(
            f"    {dim}: {len(info['resolved'])} resolved, "
            f"{len(info['open'])} still open — potential loop",
            "yellow",
        ))
    print(colorize(
        "  Your report must mention at least one recurring dimension name.",
        "dim",
    ))
    return False


def _require_reflect_stage_for_organize(stages: dict) -> bool:
    if "reflect" in stages:
        return True
    if "observe" not in stages:
        print(colorize("  Cannot organize: observe stage not complete.", "red"))
        print(colorize('  Run: desloppify plan triage --stage observe --report "..."', "dim"))
        return False
    print(colorize("  Cannot organize: reflect stage not complete.", "red"))
    print(colorize('  Run: desloppify plan triage --stage reflect --report "..."', "dim"))
    return False


def _auto_confirm_reflect_for_organize(
    *,
    args: argparse.Namespace,
    plan: dict,
    stages: dict,
    attestation: str | None,
) -> bool:
    if stages["reflect"].get("confirmed_at"):
        return True
    if not attestation or len(attestation.strip()) < _MIN_ATTESTATION_LEN:
        print(colorize("  Cannot organize: reflect stage not confirmed.", "red"))
        print(colorize("  Run: desloppify plan triage --confirm reflect", "dim"))
        print(colorize("  Or pass --attestation to auto-confirm reflect inline.", "dim"))
        return False

    runtime = command_runtime(args)
    triage_input = collect_triage_input(plan, runtime.state)
    recurring = detect_recurring_patterns(
        triage_input.open_issues,
        triage_input.resolved_issues,
    )
    _by_dim, observe_dims = _observe_dimension_breakdown(triage_input)
    reflect_dims = sorted(set((list(recurring.keys()) if recurring else []) + observe_dims))
    reflect_clusters = [
        name
        for name in plan.get("clusters", {})
        if not plan["clusters"][name].get("auto")
    ]
    validation_err = _validate_attestation(
        attestation.strip(),
        "reflect",
        dimensions=reflect_dims,
        cluster_names=reflect_clusters,
    )
    if validation_err:
        print(colorize(f"  {validation_err}", "red"))
        return False
    stages["reflect"]["confirmed_at"] = utc_now()
    stages["reflect"]["confirmed_text"] = attestation.strip()
    save_plan(plan)
    print(colorize("  ✓ Reflect auto-confirmed via --attestation.", "green"))
    return True


def _manual_clusters_or_error(plan: dict) -> list[str] | None:
    manual_clusters = _manual_clusters_with_issues(plan)
    if manual_clusters:
        return manual_clusters
    any_clusters = [
        name for name, cluster in plan.get("clusters", {}).items() if cluster.get("issue_ids")
    ]
    if any_clusters:
        print(colorize("  Cannot organize: only auto-clusters exist.", "red"))
        print(colorize("  Create manual clusters that group issues by root cause:", "dim"))
    else:
        print(colorize("  Cannot organize: no clusters with issues exist.", "red"))
    print(colorize('    desloppify plan cluster create <name> --description "..."', "dim"))
    print(colorize("    desloppify plan cluster add <name> <issue-patterns>", "dim"))
    return None


def _clusters_enriched_or_error(plan: dict) -> bool:
    gaps = _unenriched_clusters(plan)
    if not gaps:
        return True
    print(colorize(f"  Cannot organize: {len(gaps)} cluster(s) need enrichment.", "red"))
    for name, missing in gaps:
        print(colorize(f"    {name}: missing {', '.join(missing)}", "yellow"))
    print()
    print(colorize("  Each cluster needs a description and action steps:", "dim"))
    print(colorize(
        '    desloppify plan cluster update <name> --description "what this cluster addresses" '
        '--steps "step 1" "step 2"',
        "dim",
    ))
    return False


def _organize_report_or_error(report: str | None) -> str | None:
    if not report:
        print(colorize("  --report is required for --stage organize.", "red"))
        print(colorize("  Summarize your prioritized organization:", "dim"))
        print(colorize("  - Did you defer contradictory issues before clustering?", "dim"))
        print(colorize("  - What clusters did you create and why?", "dim"))
        print(colorize("  - Explicit priority ordering: which cluster 1st, 2nd, 3rd and why?", "dim"))
        print(colorize("  - What depends on what? What unblocks the most?", "dim"))
        return None
    if len(report) < 100:
        print(colorize(f"  Report too short: {len(report)} chars (minimum 100).", "red"))
        print(colorize("  Explain what you organized, your priorities, and focus order.", "dim"))
        return None
    return report


def _record_organize_stage(
    stages: dict,
    *,
    report: str,
    issue_count: int,
    existing_stage: dict | None,
    is_reuse: bool,
) -> list[str]:
    stages["organize"] = {
        "stage": "organize",
        "report": report,
        "cited_ids": [],
        "timestamp": utc_now(),
        "issue_count": issue_count,
    }
    if is_reuse and existing_stage and existing_stage.get("confirmed_at"):
        stages["organize"]["confirmed_at"] = existing_stage["confirmed_at"]
        stages["organize"]["confirmed_text"] = existing_stage.get("confirmed_text", "")
    return _cascade_clear_later_confirmations(stages, "organize")


def _require_organize_stage_for_complete(
    *,
    plan: dict,
    meta: dict,
    stages: dict,
) -> bool:
    if "organize" in stages:
        return True
    if "observe" not in stages:
        print(colorize("  Cannot complete: no stages done yet.", "red"))
        print(colorize('  Start with: desloppify plan triage --stage observe --report "..."', "dim"))
        return False

    print(colorize("  Cannot complete: organize stage not done.", "red"))
    gaps = _unenriched_clusters(plan)
    if gaps:
        print(colorize(f"  {len(gaps)} cluster(s) still need enrichment:", "yellow"))
        for name, missing in gaps:
            print(colorize(f"    {name}: missing {', '.join(missing)}", "yellow"))
        print(colorize(
            '  Fix: desloppify plan cluster update <name> --description "..." --steps "step1" "step2"',
            "dim",
        ))
        print(colorize(f"  Then: {TRIAGE_CMD_ORGANIZE}", "dim"))
    else:
        manual = _manual_clusters_with_issues(plan)
        if manual:
            print(colorize("  Clusters are enriched. Record the organize stage first:", "dim"))
            print(colorize(f"    {TRIAGE_CMD_ORGANIZE}", "dim"))
        else:
            print(colorize("  Create enriched clusters first, then record organize:", "dim"))
            print(colorize(f"    {TRIAGE_CMD_ORGANIZE}", "dim"))
    if meta.get("strategy_summary"):
        print(colorize('  Or fast-track: --confirm-existing --note "why plan is still valid" --strategy "..."', "dim"))
    return False


def _auto_confirm_organize_for_complete(
    *,
    plan: dict,
    stages: dict,
    attestation: str | None,
) -> bool:
    if stages["organize"].get("confirmed_at"):
        return True
    if not attestation or len(attestation.strip()) < _MIN_ATTESTATION_LEN:
        print(colorize("  Cannot complete: organize stage not confirmed.", "red"))
        print(colorize("  Run: desloppify plan triage --confirm organize", "dim"))
        print(colorize("  Or pass --attestation to auto-confirm organize inline.", "dim"))
        return False

    organize_clusters = [
        name for name in plan.get("clusters", {})
        if not plan["clusters"][name].get("auto")
    ]
    validation_err = _validate_attestation(
        attestation.strip(),
        "organize",
        cluster_names=organize_clusters,
    )
    if validation_err:
        print(colorize(f"  {validation_err}", "red"))
        return False
    stages["organize"]["confirmed_at"] = utc_now()
    stages["organize"]["confirmed_text"] = attestation.strip()
    save_plan(plan)
    print(colorize("  ✓ Organize auto-confirmed via --attestation.", "green"))
    return True


def _completion_clusters_valid(plan: dict) -> bool:
    manual_clusters = _manual_clusters_with_issues(plan)
    if not manual_clusters:
        any_clusters = [
            name for name, cluster in plan.get("clusters", {}).items()
            if cluster.get("issue_ids")
        ]
        if not any_clusters:
            print(colorize("  Cannot complete: no clusters with issues exist.", "red"))
            print(colorize('  Create clusters: desloppify plan cluster create <name> --description "..."', "dim"))
            return False

    gaps = _unenriched_clusters(plan)
    if not gaps:
        return True
    print(colorize(f"  Cannot complete: {len(gaps)} cluster(s) still need enrichment.", "red"))
    for name, missing in gaps:
        print(colorize(f"    {name}: missing {', '.join(missing)}", "yellow"))
    print(colorize(
        '  Fix: desloppify plan cluster update <name> --description "..." --steps "step1" "step2"',
        "dim",
    ))
    return False


def _resolve_completion_strategy(
    strategy: str | None,
    *,
    meta: dict,
) -> str | None:
    if strategy:
        return strategy
    print(colorize("  --strategy is required.", "red"))
    existing = meta.get("strategy_summary", "")
    if existing:
        print(colorize(f"  Current strategy: {existing}", "dim"))
        print(colorize('  Use --strategy "same" to keep it, or provide a new summary.', "dim"))
    else:
        print(colorize('  Provide --strategy "execution plan describing priorities, ordering, and verification approach"', "dim"))
    return None


def _completion_strategy_valid(strategy: str) -> bool:
    if strategy.strip().lower() == "same":
        return True
    if len(strategy.strip()) >= 200:
        return True
    print(colorize(f"  Strategy too short: {len(strategy.strip())} chars (minimum 200).", "red"))
    print(colorize("  The strategy should describe:", "dim"))
    print(colorize("    - Execution order and priorities", "dim"))
    print(colorize("    - What each cluster accomplishes", "dim"))
    print(colorize("    - How to verify the work is correct", "dim"))
    return False


def _print_complete_summary(plan: dict, stages: dict) -> None:
    print(colorize("  Triage summary:", "bold"))
    if "observe" in stages:
        observe_stage = stages["observe"]
        print(colorize(f"    Observe: {observe_stage.get('issue_count', '?')} issues analysed", "dim"))
    if "reflect" in stages:
        reflect_stage = stages["reflect"]
        recurring = reflect_stage.get("recurring_dims", [])
        if recurring:
            print(colorize(f"    Reflect: {len(recurring)} recurring dimension(s)", "dim"))
        else:
            print(colorize("    Reflect: no recurring patterns", "dim"))
    if "organize" not in stages:
        return
    manual = _manual_clusters_with_issues(plan)
    print(colorize(f"    Organize: {len(manual)} enriched cluster(s)", "dim"))
    for name in manual:
        cluster = plan.get("clusters", {}).get(name, {})
        steps = cluster.get("action_steps", [])
        print(colorize(f"      {name}: {len(steps)} steps", "dim"))


def _require_prior_strategy_for_confirm(meta: dict) -> bool:
    if meta.get("strategy_summary", ""):
        return True
    print(colorize("  Cannot confirm existing: no prior triage has been completed.", "red"))
    print(colorize("  The full OBSERVE → REFLECT → ORGANIZE → COMMIT flow is required the first time.", "dim"))
    print(colorize(f"  Create and enrich clusters, then: {TRIAGE_CMD_ORGANIZE}", "dim"))
    return False


def _print_new_issues_since_last(si) -> None:
    print(colorize(f"  {len(si.new_since_last)} new issue(s) since last triage:", "cyan"))
    for fid in sorted(si.new_since_last):
        issue = si.open_issues.get(fid, {})
        print(f"    * [{short_issue_id(fid)}] {issue.get('summary', '')}")
    print()


def _confirm_existing_stages_valid(
    *,
    stages: dict,
    has_only_additions: bool,
    si,
) -> bool:
    if has_only_additions:
        _print_new_issues_since_last(si)
        return True
    if "observe" not in stages:
        print(colorize("  Cannot confirm existing: observe stage not complete.", "red"))
        print(colorize("  You must read issues first.", "dim"))
        print(colorize('  Run: desloppify plan triage --stage observe --report "..."', "dim"))
        return False
    if "reflect" not in stages:
        print(colorize("  Cannot confirm existing: reflect stage not complete.", "red"))
        print(colorize("  You must compare against completed work first.", "dim"))
        print(colorize('  Run: desloppify plan triage --stage reflect --report "..."', "dim"))
        return False
    return True


def _confirm_note_valid(note: str | None) -> bool:
    if not note:
        print(colorize("  --note is required for confirm-existing.", "red"))
        print(colorize('  Explain why the existing plan is still valid (min 100 chars).', "dim"))
        return False
    if len(note) < 100:
        print(colorize(f"  Note too short: {len(note)} chars (minimum 100).", "red"))
        return False
    return True


def _resolve_confirm_existing_strategy(
    strategy: str | None,
    *,
    has_only_additions: bool,
    meta: dict,
) -> str | None:
    if strategy:
        return strategy
    if has_only_additions:
        return "same"
    print(colorize("  --strategy is required.", "red"))
    existing = meta.get("strategy_summary", "")
    if existing:
        print(colorize('  Use --strategy "same" to keep it, or provide a new summary.', "dim"))
    return None


def _confirm_strategy_valid(strategy: str) -> bool:
    if strategy.strip().lower() == "same":
        return True
    if len(strategy.strip()) >= 200:
        return True
    print(colorize(f"  Strategy too short: {len(strategy.strip())} chars (minimum 200).", "red"))
    return False


def _confirmed_text_or_error(
    *,
    plan: dict,
    state: dict,
    confirmed: str | None,
) -> str | None:
    if confirmed and len(confirmed.strip()) >= _MIN_ATTESTATION_LEN:
        return confirmed.strip()
    print(colorize("  Current plan:", "bold"))
    _show_plan_summary(plan, state)
    if confirmed:
        print(colorize(
            f"\n  --confirmed text too short ({len(confirmed.strip())} chars, min {_MIN_ATTESTATION_LEN}).",
            "red",
        ))
    print(colorize('\n  Add --confirmed "I validate this plan..." to proceed.', "dim"))
    return None


def _note_cites_new_issues_or_error(note: str, si) -> bool:
    new_ids = si.new_since_last
    if not new_ids:
        return True
    valid_ids = set(si.open_issues.keys())
    cited = extract_issue_citations(note, valid_ids)
    new_cited = cited & new_ids
    if new_cited:
        return True
    print(colorize("  Note must cite at least 1 new/changed issue.", "red"))
    print(colorize(f"  {len(new_ids)} new issue(s) since last triage:", "dim"))
    for fid in sorted(new_ids)[:5]:
        print(colorize(f"    {fid}", "dim"))
    if len(new_ids) > 5:
        print(colorize(f"    ... and {len(new_ids) - 5} more", "dim"))
    return False


def _record_confirm_existing_completion(
    *,
    stages: dict,
    note: str,
    issue_count: int,
    confirmed_text: str,
) -> None:
    stages["organize"] = {
        "stage": "organize",
        "report": f"[confirmed-existing] {note}",
        "cited_ids": [],
        "timestamp": utc_now(),
        "issue_count": issue_count,
        "confirmed_at": utc_now(),
        "confirmed_text": confirmed_text,
    }


def _cmd_stage_observe(args: argparse.Namespace) -> None:
    """Record the OBSERVE stage: agent analyses themes and root causes.

    No citation gate — the point is genuine analysis, not ID-stuffing.
    Just requires a 100-char report describing what the agent observed.
    """
    report: str | None = getattr(args, "report", None)

    runtime = command_runtime(args)
    state = runtime.state
    plan = load_plan()

    # Auto-start: inject triage stage IDs if not present
    if not _has_triage_in_queue(plan):
        _inject_triage_stages(plan)
        meta = plan.setdefault("epic_triage_meta", {})
        meta["triage_stages"] = {}
        save_plan(plan)
        print(colorize("  Planning mode auto-started (4 stages queued).", "cyan"))

    meta = plan.setdefault("epic_triage_meta", {})
    stages = meta.setdefault("triage_stages", {})
    existing_stage = stages.get("observe")

    # Jump-back: reuse existing report if no --report provided
    report, is_reuse = _resolve_reusable_report(report, existing_stage)
    if not report:
        _print_observe_report_requirement()
        return

    si = collect_triage_input(plan, state)
    issue_count = len(si.open_issues)

    # Edge case: 0 issues
    if issue_count == 0:
        cleared = _record_observe_stage(
            stages,
            report=report,
            issue_count=0,
            cited_ids=[],
            existing_stage=existing_stage,
            is_reuse=is_reuse,
        )
        save_plan(plan)
        print(colorize("  Observe stage recorded (no issues to analyse).", "green"))
        if is_reuse:
            print(colorize("  Observe data preserved (no changes).", "dim"))
            if cleared:
                _print_cascade_clear_feedback(cleared, stages)
        return

    # Validation: report length (no citation counting)
    min_chars = 50 if issue_count <= 3 else 100
    if len(report) < min_chars:
        print(colorize(f"  Report too short: {len(report)} chars (minimum {min_chars}).", "red"))
        print(colorize("  Describe themes, root causes, contradictions, and how issues relate.", "dim"))
        return

    # Save stage (still extract citations for analytics, but don't gate on them)
    valid_ids = set(si.open_issues.keys())
    cited = extract_issue_citations(report, valid_ids)

    cleared = _record_observe_stage(
        stages,
        report=report,
        issue_count=issue_count,
        cited_ids=sorted(cited),
        existing_stage=existing_stage,
        is_reuse=is_reuse,
    )

    save_plan(plan)

    append_log_entry(plan, "triage_observe", actor="user",
                     detail={"issue_count": issue_count, "cited_ids": sorted(cited),
                             "reuse": is_reuse})
    save_plan(plan)

    print(colorize(
        f"  Observe stage recorded: {issue_count} issues analysed.",
        "green",
    ))
    if is_reuse:
        print(colorize("  Observe data preserved (no changes).", "dim"))
        if cleared:
            _print_cascade_clear_feedback(cleared, stages)
    else:
        print(colorize("  Now confirm your analysis.", "yellow"))
        print(colorize("    desloppify plan triage --confirm observe", "dim"))

def _cmd_stage_reflect(args: argparse.Namespace) -> None:
    """Record the REFLECT stage: compare current issues against completed work.

    Forces the agent to consider what was previously resolved and whether
    similar issues are recurring. Requires a 100-char report (50 if ≤3 issues).
    If recurring patterns are detected, the report must mention at least one
    recurring dimension name.
    """
    report: str | None = getattr(args, "report", None)
    attestation: str | None = getattr(args, "attestation", None)

    runtime = command_runtime(args)
    state = runtime.state
    plan = load_plan()

    if not _has_triage_in_queue(plan):
        print(colorize("  No planning stages in the queue — nothing to reflect on.", "yellow"))
        return

    meta = plan.get("epic_triage_meta", {})
    stages = meta.get("triage_stages", {})

    # Jump-back: reuse existing report if no --report provided
    existing_stage = stages.get("reflect")
    is_reuse = False
    if not report and existing_stage and existing_stage.get("report"):
        report = existing_stage["report"]
        is_reuse = True
    elif not report:
        _print_reflect_report_requirement()
        return

    if "observe" not in stages:
        print(colorize("  Cannot reflect: observe stage not complete.", "red"))
        print(colorize('  Run: desloppify plan triage --stage observe --report "..."', "dim"))
        return

    si = collect_triage_input(plan, state)

    # Fold-confirm: auto-confirm observe if attestation provided
    if not _auto_confirm_observe_if_attested(
        plan=plan,
        stages=stages,
        attestation=attestation,
        triage_input=si,
    ):
        return

    issue_count = len(si.open_issues)

    # Validation: report length
    min_chars = 50 if issue_count <= 3 else 100
    if len(report) < min_chars:
        print(colorize(f"  Report too short: {len(report)} chars (minimum {min_chars}).", "red"))
        print(colorize("  Describe how current issues relate to previously completed work.", "dim"))
        return

    # Detect recurring patterns
    recurring = detect_recurring_patterns(si.open_issues, si.resolved_issues)
    recurring_dims = sorted(recurring.keys())

    # If recurring patterns exist, report must mention at least one dimension
    if not _validate_recurring_dimension_mentions(
        report=report,
        recurring_dims=recurring_dims,
        recurring=recurring,
    ):
        return

    # Save stage
    stages = meta.setdefault("triage_stages", {})
    reflect_stage = {
        "stage": "reflect",
        "report": report,
        "cited_ids": [],
        "timestamp": utc_now(),
        "issue_count": issue_count,
        "recurring_dims": recurring_dims,
    }
    stages["reflect"] = reflect_stage

    # Jump-back: preserve or clear confirmation
    if is_reuse and existing_stage and existing_stage.get("confirmed_at"):
        stages["reflect"]["confirmed_at"] = existing_stage["confirmed_at"]
        stages["reflect"]["confirmed_text"] = existing_stage.get("confirmed_text", "")
    cleared = _cascade_clear_later_confirmations(stages, "reflect")

    save_plan(plan)

    append_log_entry(plan, "triage_reflect", actor="user",
                     detail={"issue_count": issue_count, "recurring_dims": recurring_dims,
                             "reuse": is_reuse})
    save_plan(plan)

    _print_reflect_result(
        issue_count=issue_count,
        recurring_dims=recurring_dims,
        recurring=recurring,
        report=report,
        is_reuse=is_reuse,
        cleared=cleared,
        stages=stages,
    )

def _cmd_stage_organize(args: argparse.Namespace) -> None:
    """Record the ORGANIZE stage: validates cluster enrichment.

    Instead of gating on a text report, validates that the plan data
    itself has been enriched: each manual cluster needs description +
    action_steps. This forces the agent to actually think about each
    cluster's execution plan.
    """
    report: str | None = getattr(args, "report", None)
    attestation: str | None = getattr(args, "attestation", None)

    plan = load_plan()

    if not _has_triage_in_queue(plan):
        print(colorize("  No planning stages in the queue \u2014 nothing to organize.", "yellow"))
        return

    meta = plan.get("epic_triage_meta", {})
    stages = meta.get("triage_stages", {})

    # Jump-back: reuse existing report if no --report provided
    existing_stage = stages.get("organize")
    is_reuse = False
    if not report and existing_stage and existing_stage.get("report"):
        report = existing_stage["report"]
        is_reuse = True

    if not _require_reflect_stage_for_organize(stages):
        return

    # Fold-confirm: auto-confirm reflect if attestation provided
    if not _auto_confirm_reflect_for_organize(
        args=args,
        plan=plan,
        stages=stages,
        attestation=attestation,
    ):
        return

    # Validate: at least 1 manual cluster with issues
    manual_clusters = _manual_clusters_or_error(plan)
    if manual_clusters is None:
        return

    # Validate: all manual clusters are enriched
    if not _clusters_enriched_or_error(plan):
        return

    report = _organize_report_or_error(report)
    if report is None:
        return

    stages = meta.setdefault("triage_stages", {})
    cleared = _record_organize_stage(
        stages,
        report=report,
        issue_count=len(manual_clusters),
        existing_stage=existing_stage,
        is_reuse=is_reuse,
    )

    save_plan(plan)

    append_log_entry(plan, "triage_organize", actor="user",
                     detail={"cluster_count": len(manual_clusters), "reuse": is_reuse})
    save_plan(plan)

    _print_organize_result(
        manual_clusters=manual_clusters,
        plan=plan,
        report=report,
        is_reuse=is_reuse,
        cleared=cleared,
        stages=stages,
    )

def _cmd_triage_complete(args: argparse.Namespace) -> None:
    """Complete triage \u2014 requires organize stage (or confirm-existing path)."""
    strategy: str | None = getattr(args, "strategy", None)
    attestation: str | None = getattr(args, "attestation", None)
    plan = load_plan()

    if not _has_triage_in_queue(plan):
        print(colorize("  No planning stages in the queue \u2014 nothing to complete.", "yellow"))
        return

    meta = plan.get("epic_triage_meta", {})
    stages = meta.get("triage_stages", {})

    state = command_runtime(args).state
    review_ids = _open_review_ids_from_state(state)

    # Require organize stage confirmed
    if not _require_organize_stage_for_complete(
        plan=plan,
        meta=meta,
        stages=stages,
    ):
        return

    # Fold-confirm: auto-confirm organize if attestation provided
    if not _auto_confirm_organize_for_complete(
        plan=plan,
        stages=stages,
        attestation=attestation,
    ):
        return

    # Re-validate cluster enrichment at completion time (prevents bypassing
    # organize gate by editing plan.json directly)
    if not _completion_clusters_valid(plan):
        return

    # Verify cluster coverage
    organized, total, clusters = _triage_coverage(plan, open_review_ids=review_ids)

    if total > 0 and organized == 0:
        print(colorize("  Cannot complete: no issues have been organized into clusters.", "red"))
        print(colorize(f"  {total} issues are waiting.", "dim"))
        return

    if total > 0 and organized < total:
        remaining = total - organized
        print(colorize(
            f"  Warning: {remaining}/{total} issues are not yet in any cluster.",
            "yellow",
        ))

    strategy = _resolve_completion_strategy(strategy, meta=meta)
    if strategy is None:
        return
    if not _completion_strategy_valid(strategy):
        return

    # Show summary
    _print_complete_summary(plan, stages)

    organized, total, _ = _triage_coverage(plan, open_review_ids=review_ids)

    # Jump-back guidance before committing
    print()
    print(colorize("  To revise an earlier stage: desloppify plan triage --stage <observe|reflect|organize>", "dim"))
    print(colorize("  Pass --report to update, or omit to keep existing analysis.", "dim"))

    append_log_entry(plan, "triage_complete", actor="user",
                     detail={
                         "strategy_len": len(strategy.strip()),
                         "coverage": f"{organized}/{total}",
                     })

    _apply_completion(args, plan, strategy)

def _cmd_confirm_existing(args: argparse.Namespace) -> None:
    """Fast-track: confirm existing plan structure is still valid."""
    note: str | None = getattr(args, "note", None)
    strategy: str | None = getattr(args, "strategy", None)
    confirmed: str | None = getattr(args, "confirmed", None)
    plan = load_plan()

    if not _has_triage_in_queue(plan):
        print(colorize("  No planning stages in the queue — nothing to confirm.", "yellow"))
        return

    meta = plan.get("epic_triage_meta", {})
    stages = meta.get("triage_stages", {})

    # Require a prior completed triage — can't skip the full flow on first run
    if not _require_prior_strategy_for_confirm(meta):
        return

    # Determine if this is a light-path (additions only) or full ceremony
    runtime = command_runtime(args)
    state = runtime.state
    si = collect_triage_input(plan, state)
    has_only_additions = bool(si.new_since_last) and not si.resolved_since_last

    if not _confirm_existing_stages_valid(
        stages=stages,
        has_only_additions=has_only_additions,
        si=si,
    ):
        return

    # Require existing enriched clusters
    clusters_with_issues = _manual_clusters_with_issues(plan)
    if not clusters_with_issues:
        print(colorize("  Cannot confirm existing: no clusters with issues exist.", "red"))
        print(colorize("  Use the full organize flow instead.", "dim"))
        return

    # Require note
    if not _confirm_note_valid(note):
        return

    # Require strategy (default to "same" on light path)
    strategy = _resolve_confirm_existing_strategy(
        strategy,
        has_only_additions=has_only_additions,
        meta=meta,
    )
    if strategy is None:
        return

    # Strategy length check (unless "same")
    if not _confirm_strategy_valid(strategy):
        return

    # Require --confirmed with plan review
    confirmed_text = _confirmed_text_or_error(
        plan=plan,
        state=state,
        confirmed=confirmed,
    )
    if confirmed_text is None:
        return

    # Validate: note cites at least 1 new/changed issue (if there are any)
    if not _note_cites_new_issues_or_error(note, si):
        return

    # Record organize as confirmed-existing and complete
    stages = meta.setdefault("triage_stages", {})
    _record_confirm_existing_completion(
        stages=stages,
        note=note,
        issue_count=len(clusters_with_issues),
        confirmed_text=confirmed_text,
    )

    append_log_entry(plan, "triage_confirm_existing", actor="user",
                     detail={"confirmed_text": confirmed_text})

    _apply_completion(args, plan, strategy)
    print(colorize("  Confirmed existing plan — triage complete.", "green"))

__all__ = [
    "_cmd_confirm_existing",
    "_cmd_stage_observe",
    "_cmd_stage_organize",
    "_cmd_stage_reflect",
    "_cmd_triage_complete",
]
