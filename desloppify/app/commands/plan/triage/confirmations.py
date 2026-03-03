"""Attestation and confirmation handlers for plan triage."""

from __future__ import annotations

import argparse

from desloppify.app.commands.helpers.runtime import command_runtime
from desloppify.core.output import colorize
from desloppify.engine.plan import (
    append_log_entry,
    collect_triage_input,
    detect_recurring_patterns,
    load_plan,
    save_plan,
)
from desloppify.state import utc_now

from .display import _show_plan_summary
from .helpers import (
    _count_log_activity_since,
    _observe_dimension_breakdown,
    _open_review_ids_from_state,
    _purge_triage_stage,
    _triage_coverage,
)

_MIN_ATTESTATION_LEN = 80


def _validate_attestation(
    attestation: str,
    stage: str,
    *,
    dimensions: list[str] | None = None,
    cluster_names: list[str] | None = None,
) -> str | None:
    """Return error message if attestation doesn't reference required data, else None."""
    text = attestation.lower()

    if stage == "observe":
        if dimensions:
            found = [d for d in dimensions if d.lower().replace("_", " ") in text or d.lower() in text]
            if not found:
                dim_list = ", ".join(dimensions[:6])
                return f"Attestation must reference at least one dimension from the summary. Mention one of: {dim_list}"

    elif stage == "reflect":
        refs: list[str] = []
        if dimensions:
            refs.extend(d for d in dimensions if d.lower().replace("_", " ") in text or d.lower() in text)
        if cluster_names:
            refs.extend(n for n in cluster_names if n.lower() in text)
        if not refs and (dimensions or cluster_names):
            return (
                f"Attestation must reference at least one dimension or cluster name.\n"
                f"  Valid dimensions: {', '.join((dimensions or [])[:6])}\n"
                f"  Valid clusters: {', '.join((cluster_names or [])[:6]) if cluster_names else '(none yet)'}"
            )

    elif stage == "organize":
        if cluster_names:
            found = [n for n in cluster_names if n.lower() in text]
            if not found:
                names = ", ".join(cluster_names[:6])
                return f"Attestation must reference at least one cluster from the plan. Mention one of: {names}"

    return None

def _confirm_observe(args: argparse.Namespace, plan: dict, stages: dict, attestation: str | None) -> None:
    """Show observe summary and record confirmation if attestation is valid."""
    if "observe" not in stages:
        print(colorize("  Cannot confirm: observe stage not recorded.", "red"))
        print(colorize('  Run: desloppify plan triage --stage observe --report "..."', "dim"))
        return
    if stages["observe"].get("confirmed_at"):
        print(colorize("  Observe stage already confirmed.", "green"))
        return

    # Show summary
    runtime = command_runtime(args)
    si = collect_triage_input(plan, runtime.state)
    obs = stages["observe"]

    print(colorize("  Stage: OBSERVE — Analyse issues & spot contradictions", "bold"))
    print(colorize("  " + "─" * 54, "dim"))

    # Dimension breakdown
    by_dim, dim_names = _observe_dimension_breakdown(si)

    issue_count = obs.get("issue_count", len(si.open_issues))
    print(f"  Your analysis covered {issue_count} issues across {len(by_dim)} dimensions:")
    for dim in dim_names:
        print(f"    {dim}: {by_dim[dim]} issues")

    cited = obs.get("cited_ids", [])
    if cited:
        print(f"  You cited {len(cited)} issue IDs in your report.")

    if not attestation or len(attestation.strip()) < _MIN_ATTESTATION_LEN:
        if attestation:
            print(colorize(
                f"\n  Attestation too short ({len(attestation.strip())} chars, min {_MIN_ATTESTATION_LEN}).",
                "red",
            ))
        print(colorize("\n  If satisfied, confirm:", "dim"))
        print(colorize('    desloppify plan triage --confirm observe --attestation "I have thoroughly reviewed..."', "dim"))
        print(colorize("  If not, continue reviewing issues before reflecting.", "dim"))
        return

    # Validate attestation references actual data
    validation_err = _validate_attestation(attestation.strip(), "observe", dimensions=dim_names)
    if validation_err:
        print(colorize(f"\n  {validation_err}", "red"))
        return

    # Record confirmation
    stages["observe"]["confirmed_at"] = utc_now()
    stages["observe"]["confirmed_text"] = attestation.strip()
    _purge_triage_stage(plan, "observe")
    save_plan(plan)
    append_log_entry(plan, "triage_confirm_observe", actor="user",
                     detail={"attestation": attestation.strip()})
    save_plan(plan)
    print(colorize(f'  ✓ Observe confirmed: "{attestation.strip()}"', "green"))

def _confirm_reflect(args: argparse.Namespace, plan: dict, stages: dict, attestation: str | None) -> None:
    """Show reflect summary and record confirmation if attestation is valid."""
    if "reflect" not in stages:
        print(colorize("  Cannot confirm: reflect stage not recorded.", "red"))
        print(colorize('  Run: desloppify plan triage --stage reflect --report "..."', "dim"))
        return
    if stages["reflect"].get("confirmed_at"):
        print(colorize("  Reflect stage already confirmed.", "green"))
        return

    runtime = command_runtime(args)
    si = collect_triage_input(plan, runtime.state)
    ref = stages["reflect"]

    print(colorize("  Stage: REFLECT — Form strategy & present to user", "bold"))
    print(colorize("  " + "─" * 50, "dim"))

    # Recurring dimensions
    recurring = detect_recurring_patterns(si.open_issues, si.resolved_issues)
    if recurring:
        print(f"  Your strategy identified {len(recurring)} recurring dimension(s):")
        for dim, info in sorted(recurring.items()):
            resolved_count = len(info["resolved"])
            open_count = len(info["open"])
            label = "potential loop" if open_count >= resolved_count else "root cause unaddressed"
            print(f"    {dim}: {resolved_count} resolved, {open_count} still open — {label}")
    else:
        print("  No recurring patterns detected.")

    # Strategy briefing excerpt
    report = ref.get("report", "")
    if report:
        print()
        print(colorize("  ┌─ Your strategy briefing ───────────────────────┐", "cyan"))
        for line in report.strip().splitlines()[:8]:
            print(colorize(f"  │ {line}", "cyan"))
        if len(report.strip().splitlines()) > 8:
            print(colorize("  │ ...", "cyan"))
        print(colorize("  └" + "─" * 51 + "┘", "cyan"))

    # Collect data references for validation — include observe-stage dimensions
    _by_dim, observe_dims = _observe_dimension_breakdown(si)
    reflect_dims = sorted(set((list(recurring.keys()) if recurring else []) + observe_dims))
    reflect_clusters = [n for n in plan.get("clusters", {}) if not plan["clusters"][n].get("auto")]

    if not attestation or len(attestation.strip()) < _MIN_ATTESTATION_LEN:
        if attestation:
            print(colorize(
                f"\n  Attestation too short ({len(attestation.strip())} chars, min {_MIN_ATTESTATION_LEN}).",
                "red",
            ))
        print(colorize("\n  If satisfied, confirm:", "dim"))
        print(colorize('    desloppify plan triage --confirm reflect --attestation "My strategy accounts for..."', "dim"))
        print(colorize("  If not, refine your strategy before organizing.", "dim"))
        return

    # Validate attestation references actual data
    validation_err = _validate_attestation(
        attestation.strip(), "reflect",
        dimensions=reflect_dims, cluster_names=reflect_clusters,
    )
    if validation_err:
        print(colorize(f"\n  {validation_err}", "red"))
        return

    stages["reflect"]["confirmed_at"] = utc_now()
    stages["reflect"]["confirmed_text"] = attestation.strip()
    _purge_triage_stage(plan, "reflect")
    save_plan(plan)
    append_log_entry(plan, "triage_confirm_reflect", actor="user",
                     detail={"attestation": attestation.strip()})
    save_plan(plan)
    print(colorize(f'  ✓ Reflect confirmed: "{attestation.strip()}"', "green"))

def _confirm_organize(args: argparse.Namespace, plan: dict, stages: dict, attestation: str | None) -> None:
    """Show full plan summary and record confirmation if attestation is valid."""
    if "organize" not in stages:
        print(colorize("  Cannot confirm: organize stage not recorded.", "red"))
        print(colorize('  Run: desloppify plan triage --stage organize --report "..."', "dim"))
        return
    if stages["organize"].get("confirmed_at"):
        print(colorize("  Organize stage already confirmed.", "green"))
        return

    runtime = command_runtime(args)
    state = runtime.state

    print(colorize("  Stage: ORGANIZE — Defer contradictions, cluster, & prioritize", "bold"))
    print(colorize("  " + "─" * 63, "dim"))

    # Activity since reflect
    reflect_ts = stages.get("reflect", {}).get("timestamp", "")
    if reflect_ts:
        activity = _count_log_activity_since(plan, reflect_ts)
        if activity:
            print("  Since reflect, you have:")
            for action, count in sorted(activity.items()):
                print(f"    {action}: {count}")
        else:
            print("  No logged plan operations since reflect.")

    # Show full plan
    print(colorize("\n  Plan:", "bold"))
    _show_plan_summary(plan, state)

    organize_clusters = [n for n in plan.get("clusters", {}) if not plan["clusters"][n].get("auto")]

    if not attestation or len(attestation.strip()) < _MIN_ATTESTATION_LEN:
        if attestation:
            print(colorize(
                f"\n  Attestation too short ({len(attestation.strip())} chars, min {_MIN_ATTESTATION_LEN}).",
                "red",
            ))
        print(colorize("\n  If satisfied, confirm:", "dim"))
        print(colorize('    desloppify plan triage --confirm organize --attestation "This plan is correct..."', "dim"))
        print(colorize("  If not, adjust clusters, priorities, or queue order before completing.", "dim"))
        return

    # Validate attestation references actual data
    validation_err = _validate_attestation(
        attestation.strip(), "organize", cluster_names=organize_clusters,
    )
    if validation_err:
        print(colorize(f"\n  {validation_err}", "red"))
        return

    organized, total, _ = _triage_coverage(
        plan, open_review_ids=_open_review_ids_from_state(state),
    )
    stages["organize"]["confirmed_at"] = utc_now()
    stages["organize"]["confirmed_text"] = attestation.strip()
    _purge_triage_stage(plan, "organize")
    save_plan(plan)
    append_log_entry(plan, "triage_confirm_organize", actor="user",
                     detail={
                         "attestation": attestation.strip(),
                         "coverage": f"{organized}/{total}",
                     })
    save_plan(plan)
    print(colorize(f'  ✓ Organize confirmed: "{attestation.strip()}"', "green"))

def _cmd_confirm_stage(args: argparse.Namespace) -> None:
    """Router for ``--confirm observe/reflect/organize``."""
    confirm_stage = getattr(args, "confirm", None)
    attestation = getattr(args, "attestation", None)
    plan = load_plan()
    meta = plan.get("epic_triage_meta", {})
    stages = meta.get("triage_stages", {})

    if confirm_stage == "observe":
        _confirm_observe(args, plan, stages, attestation)
    elif confirm_stage == "reflect":
        _confirm_reflect(args, plan, stages, attestation)
    elif confirm_stage == "organize":
        _confirm_organize(args, plan, stages, attestation)

__all__ = [
    "_MIN_ATTESTATION_LEN",
    "_cmd_confirm_stage",
    "_confirm_observe",
    "_confirm_organize",
    "_confirm_reflect",
    "_validate_attestation",
]
