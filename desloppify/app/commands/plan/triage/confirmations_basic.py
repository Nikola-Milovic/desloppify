"""Basic triage stage confirmation handlers (observe/reflect + attestation parsing)."""

from __future__ import annotations

import argparse

from desloppify.base.output.terminal import colorize
from desloppify.base.output.user_message import print_user_message
from desloppify.state import utc_now

from .helpers import observe_dimension_breakdown, purge_triage_stage
from .services import TriageServices, default_triage_services


MIN_ATTESTATION_LEN = 80


def validate_attestation(
    attestation: str,
    stage: str,
    *,
    dimensions: list[str] | None = None,
    cluster_names: list[str] | None = None,
) -> str | None:
    """Return error message if attestation doesn't reference required data."""
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
                "Attestation must reference at least one dimension or cluster name.\n"
                f"  Valid dimensions: {', '.join((dimensions or [])[:6])}\n"
                f"  Valid clusters: {', '.join((cluster_names or [])[:6]) if cluster_names else '(none yet)'}"
            )

    elif stage == "organize":
        if cluster_names:
            found = [n for n in cluster_names if n.lower() in text]
            if not found:
                names = ", ".join(cluster_names[:6])
                return f"Attestation must reference at least one cluster from the plan. Mention one of: {names}"

    elif stage == "enrich":
        if cluster_names:
            found = [n for n in cluster_names if n.lower() in text]
            if not found:
                names = ", ".join(cluster_names[:6])
                return f"Attestation must reference at least one cluster you enriched. Mention one of: {names}"

    elif stage == "sense-check":
        if cluster_names:
            found = [n for n in cluster_names if n.lower() in text]
            if not found:
                names = ", ".join(cluster_names[:6])
                return f"Attestation must reference at least one cluster you sense-checked. Mention one of: {names}"

    return None


def confirm_observe(
    args: argparse.Namespace,
    plan: dict,
    stages: dict,
    attestation: str | None,
    *,
    services: TriageServices | None = None,
) -> None:
    """Show observe summary and record confirmation if attestation is valid."""


    resolved_services = services or default_triage_services()
    if "observe" not in stages:
        print(colorize("  Cannot confirm: observe stage not recorded.", "red"))
        print(colorize('  Run: desloppify plan triage --stage observe --report "..."', "dim"))
        return
    if stages["observe"].get("confirmed_at"):
        print(colorize("  Observe stage already confirmed.", "green"))
        return

    runtime = resolved_services.command_runtime(args)
    si = resolved_services.collect_triage_input(plan, runtime.state)
    obs = stages["observe"]

    print(colorize("  Stage: OBSERVE — Analyse issues & spot contradictions", "bold"))
    print(colorize("  " + "─" * 54, "dim"))

    by_dim, dim_names = observe_dimension_breakdown(si)

    issue_count = obs.get("issue_count", len(si.open_issues))
    print(f"  Your analysis covered {issue_count} issues across {len(by_dim)} dimensions:")
    for dim in dim_names:
        print(f"    {dim}: {by_dim[dim]} issues")

    cited = obs.get("cited_ids", [])
    if cited:
        print(f"  You cited {len(cited)} issue IDs in your report.")

    min_citations = min(5, max(1, issue_count // 10)) if issue_count > 0 else 0
    if len(cited) < min_citations:
        print(colorize(f"\n  Cannot confirm: only {len(cited)} issue ID(s) cited in report (need {min_citations}+).", "red"))
        print(colorize("  Your observe report should reference specific issues by their hash IDs to prove", "dim"))
        print(colorize("  you actually read them. Cite at least 10% of issues or 5, whichever is smaller.", "dim"))
        print(colorize("  Re-record observe with more issue citations, then re-confirm.", "dim"))
        return

    if not attestation or len(attestation.strip()) < MIN_ATTESTATION_LEN:
        if attestation:
            print(colorize(f"\n  Attestation too short ({len(attestation.strip())} chars, min {MIN_ATTESTATION_LEN}).", "red"))
        print(colorize("\n  If satisfied, confirm:", "dim"))
        print(colorize('    desloppify plan triage --confirm observe --attestation "I have thoroughly reviewed..."', "dim"))
        print(colorize("  If not, continue reviewing issues before reflecting.", "dim"))
        return

    validation_err = validate_attestation(attestation.strip(), "observe", dimensions=dim_names)
    if validation_err:
        print(colorize(f"\n  {validation_err}", "red"))
        return

    stages["observe"]["confirmed_at"] = utc_now()
    stages["observe"]["confirmed_text"] = attestation.strip()
    purge_triage_stage(plan, "observe")
    resolved_services.append_log_entry(
        plan,
        "triage_confirm_observe",
        actor="user",
        detail={"attestation": attestation.strip()},
    )
    resolved_services.save_plan(plan)
    print(colorize(f'  ✓ Observe confirmed: "{attestation.strip()}"', "green"))
    print_user_message(
        "Hey — observe is confirmed. Run `desloppify plan triage"
        " --stage reflect --report \"...\"` next. No need to reply,"
        " just keep going."
    )


def confirm_reflect(
    args: argparse.Namespace,
    plan: dict,
    stages: dict,
    attestation: str | None,
    *,
    services: TriageServices | None = None,
) -> None:
    """Show reflect summary and record confirmation if attestation is valid."""


    resolved_services = services or default_triage_services()
    if "reflect" not in stages:
        print(colorize("  Cannot confirm: reflect stage not recorded.", "red"))
        print(colorize('  Run: desloppify plan triage --stage reflect --report "..."', "dim"))
        return
    if stages["reflect"].get("confirmed_at"):
        print(colorize("  Reflect stage already confirmed.", "green"))
        return

    runtime = resolved_services.command_runtime(args)
    si = resolved_services.collect_triage_input(plan, runtime.state)
    ref = stages["reflect"]

    print(colorize("  Stage: REFLECT — Form strategy & present to user", "bold"))
    print(colorize("  " + "─" * 50, "dim"))

    recurring = resolved_services.detect_recurring_patterns(si.open_issues, si.resolved_issues)
    if recurring:
        print(f"  Your strategy identified {len(recurring)} recurring dimension(s):")
        for dim, info in sorted(recurring.items()):
            resolved_count = len(info["resolved"])
            open_count = len(info["open"])
            label = "potential loop" if open_count >= resolved_count else "root cause unaddressed"
            print(f"    {dim}: {resolved_count} resolved, {open_count} still open — {label}")
    else:
        print("  No recurring patterns detected.")

    report = ref.get("report", "")
    if report:
        print()
        print(colorize("  ┌─ Your strategy briefing ───────────────────────┐", "cyan"))
        for line in report.strip().splitlines()[:8]:
            print(colorize(f"  │ {line}", "cyan"))
        if len(report.strip().splitlines()) > 8:
            print(colorize("  │ ...", "cyan"))
        print(colorize("  └" + "─" * 51 + "┘", "cyan"))

    _by_dim, observe_dims = observe_dimension_breakdown(si)
    reflect_dims = sorted(set((list(recurring.keys()) if recurring else []) + observe_dims))
    reflect_clusters = [name for name in plan.get("clusters", {}) if not plan["clusters"][name].get("auto")]

    if not attestation or len(attestation.strip()) < MIN_ATTESTATION_LEN:
        if attestation:
            print(colorize(f"\n  Attestation too short ({len(attestation.strip())} chars, min {MIN_ATTESTATION_LEN}).", "red"))
        print(colorize("\n  If satisfied, confirm:", "dim"))
        print(colorize('    desloppify plan triage --confirm reflect --attestation "My strategy accounts for..."', "dim"))
        print(colorize("  If not, refine your strategy before organizing.", "dim"))
        return

    validation_err = validate_attestation(attestation.strip(), "reflect", dimensions=reflect_dims, cluster_names=reflect_clusters)
    if validation_err:
        print(colorize(f"\n  {validation_err}", "red"))
        return

    stages["reflect"]["confirmed_at"] = utc_now()
    stages["reflect"]["confirmed_text"] = attestation.strip()
    purge_triage_stage(plan, "reflect")
    resolved_services.append_log_entry(
        plan,
        "triage_confirm_reflect",
        actor="user",
        detail={"attestation": attestation.strip()},
    )
    resolved_services.save_plan(plan)
    print(colorize(f'  ✓ Reflect confirmed: "{attestation.strip()}"', "green"))
    print_user_message(
        "Hey — reflect is confirmed. Now create clusters, enrich"
        " them with action steps, then run `desloppify plan triage"
        " --stage organize --report \"...\"`. No need to reply,"
        " just keep going."
    )


__all__ = ["MIN_ATTESTATION_LEN", "confirm_observe", "confirm_reflect", "validate_attestation"]
