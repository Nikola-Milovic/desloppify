"""Organize-stage triage confirmation handler."""

from __future__ import annotations

import argparse

from desloppify.base.output.terminal import colorize
from desloppify.base.output.user_message import print_user_message
from desloppify.state import utc_now

from .confirmations_basic import MIN_ATTESTATION_LEN, validate_attestation
from .display import show_plan_summary
from .helpers import count_log_activity_since, open_review_ids_from_state, purge_triage_stage, triage_coverage
from .services import TriageServices, default_triage_services


def confirm_organize(
    args: argparse.Namespace,
    plan: dict,
    stages: dict,
    attestation: str | None,
    *,
    services: TriageServices | None = None,
) -> None:
    """Show full plan summary and record confirmation if attestation is valid."""


    resolved_services = services or default_triage_services()
    if "organize" not in stages:
        print(colorize("  Cannot confirm: organize stage not recorded.", "red"))
        print(colorize('  Run: desloppify plan triage --stage organize --report "..."', "dim"))
        return
    if stages["organize"].get("confirmed_at"):
        print(colorize("  Organize stage already confirmed.", "green"))
        return

    runtime = resolved_services.command_runtime(args)
    state = runtime.state

    print(colorize("  Stage: ORGANIZE — Defer contradictions, cluster, & prioritize", "bold"))
    print(colorize("  " + "─" * 63, "dim"))

    reflect_ts = stages.get("reflect", {}).get("timestamp", "")
    if reflect_ts:
        activity = count_log_activity_since(plan, reflect_ts)
        if activity:
            print("  Since reflect, you have:")
            for action, count in sorted(activity.items()):
                print(f"    {action}: {count}")
        else:
            print("  No logged plan operations since reflect.")

    print(colorize("\n  Plan:", "bold"))
    show_plan_summary(plan, state)

    organize_clusters = [n for n in plan.get("clusters", {}) if not plan["clusters"][n].get("auto")]

    from .stage_helpers import unclustered_review_issues, unenriched_clusters  # noqa: PLC0415

    gaps = unenriched_clusters(plan)
    if gaps:
        print(colorize(f"\n  Cannot confirm: {len(gaps)} cluster(s) still need enrichment.", "red"))
        for name, missing in gaps:
            print(colorize(f"    {name}: missing {', '.join(missing)}", "yellow"))
        print(colorize("  Small clusters (<5 issues) need at least 1 action step per issue.", "dim"))
        print(colorize('  Fix: desloppify plan cluster update <name> --steps "step1" "step2"', "dim"))
        return

    unclustered = unclustered_review_issues(plan, state)
    if unclustered:
        print(colorize(f"\n  Cannot confirm: {len(unclustered)} review issue(s) have no action plan.", "red"))
        for fid in unclustered[:5]:
            short = fid.rsplit("::", 2)[-2] if "::" in fid else fid
            print(colorize(f"    {short}", "yellow"))
        if len(unclustered) > 5:
            print(colorize(f"    ... and {len(unclustered) - 5} more", "yellow"))
        print(colorize("  Add each to a cluster or wontfix it before confirming.", "dim"))
        return

    from ._stage_validation import _cluster_file_overlaps, _clusters_with_directory_scatter, _clusters_with_high_step_ratio  # noqa: PLC0415

    scattered = _clusters_with_directory_scatter(plan)
    if scattered:
        print(colorize(f"\n  Warning: {len(scattered)} cluster(s) span many unrelated directories:", "yellow"))
        for name, dir_count, sample_dirs in scattered:
            print(colorize(f"    {name}: {dir_count} directories — likely grouped by theme, not area", "yellow"))
            for d in sample_dirs[:3]:
                print(colorize(f"      {d}", "dim"))
        print(colorize("  Consider splitting into area-focused clusters (same files in same PR).", "dim"))

    high_ratio = _clusters_with_high_step_ratio(plan)
    if high_ratio:
        print(colorize(f"\n  Warning: {len(high_ratio)} cluster(s) have step count ≥ issue count:", "yellow"))
        for name, steps, issues, ratio in high_ratio:
            print(colorize(f"    {name}: {steps} steps for {issues} issues ({ratio:.1f}x)", "yellow"))
        print(colorize("  Steps should consolidate changes to the same file. 1:1 means each issue is its own step.", "dim"))

    overlaps = _cluster_file_overlaps(plan)
    if overlaps:
        clusters = plan.get("clusters", {})
        print(colorize(f"\n  Note: {len(overlaps)} cluster pair(s) reference the same files:", "yellow"))
        for a, b, files in overlaps[:5]:
            print(colorize(f"    {a} ↔ {b}: {len(files)} shared file(s)", "yellow"))
        needs_dep = []
        for a, b, files in overlaps:
            a_deps = set(clusters.get(a, {}).get("depends_on_clusters", []))
            b_deps = set(clusters.get(b, {}).get("depends_on_clusters", []))
            if b not in a_deps and a not in b_deps:
                needs_dep.append((a, b, files))
        if needs_dep:
            print(colorize("  These pairs have no dependency relationship — add one to prevent merge conflicts:", "dim"))
            for a, b, _files in needs_dep[:5]:
                print(colorize(f"    desloppify plan cluster update {b} --depends-on {a}", "dim"))
                print(colorize(f"    # or: desloppify plan cluster update {a} --depends-on {b}", "dim"))

    clusters = plan.get("clusters", {})
    for cname, cluster in clusters.items():
        deps = cluster.get("depends_on_clusters", [])
        if cname in deps:
            print(colorize(f"  Warning: {cname} depends on itself.", "yellow"))

    orphaned = [
        (name, len(cluster.get("action_steps", [])))
        for name, cluster in clusters.items()
        if not cluster.get("auto") and not cluster.get("issue_ids") and cluster.get("action_steps")
    ]
    if orphaned:
        print(colorize(f"\n  Note: {len(orphaned)} cluster(s) have steps but no issues:", "yellow"))
        for name, step_count in orphaned:
            print(colorize(f"    {name}: {step_count} steps, 0 issues", "yellow"))
        print(colorize("  These may need issues added, or may be leftover from resolved work.", "dim"))

    if not attestation or len(attestation.strip()) < MIN_ATTESTATION_LEN:
        if attestation:
            print(colorize(f"\n  Attestation too short ({len(attestation.strip())} chars, min {MIN_ATTESTATION_LEN}).", "red"))
        print(colorize("\n  If satisfied, confirm:", "dim"))
        print(colorize('    desloppify plan triage --confirm organize --attestation "This plan is correct..."', "dim"))
        print(colorize("  If not, adjust clusters, priorities, or queue order before completing.", "dim"))
        return

    validation_err = validate_attestation(attestation.strip(), "organize", cluster_names=organize_clusters)
    if validation_err:
        print(colorize(f"\n  {validation_err}", "red"))
        return

    organized, total, _ = triage_coverage(plan, open_review_ids=open_review_ids_from_state(state))
    stages["organize"]["confirmed_at"] = utc_now()
    stages["organize"]["confirmed_text"] = attestation.strip()
    purge_triage_stage(plan, "organize")
    resolved_services.append_log_entry(
        plan,
        "triage_confirm_organize",
        actor="user",
        detail={"attestation": attestation.strip(), "coverage": f"{organized}/{total}"},
    )
    resolved_services.save_plan(plan)
    print(colorize(f'  ✓ Organize confirmed: "{attestation.strip()}"', "green"))
    print_user_message(
        "Hey — organize is confirmed. Next: enrich your steps"
        " with detail and issue_refs so they're executor-ready."
        " Run `desloppify plan triage --stage enrich --report \"...\"`."
        " You can still reorganize (add/remove clusters, reorder)"
        " during the enrich stage."
    )


__all__ = ["confirm_organize"]
