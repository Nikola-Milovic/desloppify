"""Enrich and sense-check triage confirmation handlers."""

from __future__ import annotations

import argparse

from desloppify.base.output.terminal import colorize
from desloppify.base.output.user_message import print_user_message
from desloppify.state import utc_now

from .confirmations_basic import MIN_ATTESTATION_LEN, validate_attestation
from .helpers import purge_triage_stage
from .services import TriageServices, default_triage_services


def confirm_enrich(
    args: argparse.Namespace,
    plan: dict,
    stages: dict,
    attestation: str | None,
    *,
    services: TriageServices | None = None,
) -> None:
    """Show enrich summary and record confirmation if attestation is valid."""


    resolved_services = services or default_triage_services()
    if "enrich" not in stages:
        print(colorize("  Cannot confirm: enrich stage not recorded.", "red"))
        print(colorize('  Run: desloppify plan triage --stage enrich --report "..."', "dim"))
        return
    if stages["enrich"].get("confirmed_at"):
        print(colorize("  Enrich stage already confirmed.", "green"))
        return

    from ._stage_validation import (
        _steps_missing_issue_refs,
        _steps_referencing_skipped_issues,
        _steps_with_bad_paths,
        _steps_with_vague_detail,
        _steps_without_effort,
        _underspecified_steps,
    )

    print(colorize("  Stage: ENRICH — Make steps executor-ready (detail, refs)", "bold"))
    print(colorize("  " + "─" * 54, "dim"))

    underspec = _underspecified_steps(plan)
    if underspec:
        total_bare = sum(n for _, n, _ in underspec)
        print(colorize(f"  Cannot confirm: {total_bare} step(s) still lack detail or issue_refs.", "red"))
        for name, bare, total in underspec[:5]:
            print(colorize(f"    {name}: {bare}/{total} steps", "yellow"))
        print()
        print(colorize("  Every step needs --detail (sub-points) or --issue-refs (for auto-completion).", "dim"))
        print(colorize("  Fix:", "dim"))
        print(colorize('    desloppify plan cluster update <name> --update-step N --detail "sub-details"', "dim"))
        return
    else:
        print(colorize("  All steps have detail or issue_refs.", "green"))

    from desloppify.base.discovery.paths import get_project_root

    bad_paths = _steps_with_bad_paths(plan, get_project_root())
    if bad_paths:
        total_bad = sum(len(bp) for _, _, bp in bad_paths)
        print(colorize(f"\n  Cannot confirm: {total_bad} file path(s) in step details don't exist on disk.", "red"))
        for name, step_num, paths in bad_paths:
            for path_str in paths:
                print(colorize(f"    {name} step {step_num}: {path_str}", "yellow"))
        print(colorize("  Fix paths with: desloppify plan cluster update <name> --update-step N --detail '...'", "dim"))
        return

    untagged = _steps_without_effort(plan)
    if untagged:
        total_missing = sum(n for _, n, _ in untagged)
        print(colorize(f"\n  Cannot confirm: {total_missing} step(s) have no effort tag.", "red"))
        for name, missing, total in untagged[:5]:
            print(colorize(f"    {name}: {missing}/{total} steps missing effort", "yellow"))
        print(colorize("  Every step needs --effort (trivial/small/medium/large).", "dim"))
        print(colorize("  Fix: desloppify plan cluster update <name> --update-step N --effort small", "dim"))
        return

    no_refs = _steps_missing_issue_refs(plan)
    if no_refs:
        total_missing = sum(n for _, n, _ in no_refs)
        print(colorize(f"\n  Cannot confirm: {total_missing} step(s) have no issue_refs.", "red"))
        for name, missing, total in no_refs[:5]:
            print(colorize(f"    {name}: {missing}/{total} steps missing refs", "yellow"))
        print(colorize("  Every step needs --issue-refs linking it to the review issue(s) it addresses.", "dim"))
        print(colorize("  Fix: desloppify plan cluster update <name> --update-step N --issue-refs <hash1> <hash2>", "dim"))
        return

    vague = _steps_with_vague_detail(plan, get_project_root())
    if vague:
        print(colorize(f"\n  Cannot confirm: {len(vague)} step(s) have vague detail (< 80 chars, no file paths).", "red"))
        for name, step_num, title in vague[:5]:
            print(colorize(f"    {name} step {step_num}: {title}", "yellow"))
        print(colorize("  Executor-ready means: someone with zero context knows which file to open and what to change.", "dim"))
        print(colorize("  Add file paths and specific instructions to each step's --detail.", "dim"))
        return

    stale_refs = _steps_referencing_skipped_issues(plan)
    if stale_refs:
        total_stale = sum(len(ids) for _, _, ids in stale_refs)
        print(colorize(f"\n  Warning: {total_stale} step issue_ref(s) point to skipped/wontfixed issues.", "yellow"))
        for name, step_num, ids in stale_refs[:5]:
            print(colorize(f"    {name} step {step_num}: {', '.join(ids[:3])}", "yellow"))
        print(colorize("  Consider removing stale refs or removing the step if it's no longer needed.", "dim"))

    enrich_clusters = [n for n in plan.get("clusters", {}) if not plan["clusters"][n].get("auto")]

    if not attestation or len(attestation.strip()) < MIN_ATTESTATION_LEN:
        if attestation:
            print(colorize(f"\n  Attestation too short ({len(attestation.strip())} chars, min {MIN_ATTESTATION_LEN}).", "red"))
        print(colorize("\n  If satisfied, confirm:", "dim"))
        print(colorize('    desloppify plan triage --confirm enrich --attestation "Steps are executor-ready..."', "dim"))
        return

    validation_err = validate_attestation(attestation.strip(), "enrich", cluster_names=enrich_clusters)
    if validation_err:
        print(colorize(f"\n  {validation_err}", "red"))
        return

    stages["enrich"]["confirmed_at"] = utc_now()
    stages["enrich"]["confirmed_text"] = attestation.strip()
    purge_triage_stage(plan, "enrich")
    resolved_services.append_log_entry(
        plan,
        "triage_confirm_enrich",
        actor="user",
        detail={"attestation": attestation.strip()},
    )
    resolved_services.save_plan(plan)
    print(colorize(f'  ✓ Enrich confirmed: "{attestation.strip()}"', "green"))
    print_user_message(
        "Hey — enrich is confirmed. Run `desloppify plan triage"
        " --stage sense-check --report \"...\"` to verify step"
        " accuracy and cross-cluster dependencies."
    )


def confirm_sense_check(
    args: argparse.Namespace,
    plan: dict,
    stages: dict,
    attestation: str | None,
    *,
    services: TriageServices | None = None,
) -> None:
    """Show sense-check summary and record confirmation if attestation is valid."""


    resolved_services = services or default_triage_services()
    if "sense-check" not in stages:
        print(colorize("  Cannot confirm: sense-check stage not recorded.", "red"))
        print(colorize('  Run: desloppify plan triage --stage sense-check --report "..."', "dim"))
        return
    if stages["sense-check"].get("confirmed_at"):
        print(colorize("  Sense-check stage already confirmed.", "green"))
        return

    from ._stage_validation import (
        _steps_missing_issue_refs,
        _steps_with_bad_paths,
        _steps_with_vague_detail,
        _steps_without_effort,
        _underspecified_steps,
    )
    from desloppify.base.discovery.paths import get_project_root

    print(colorize("  Stage: SENSE-CHECK — Verify accuracy & cross-cluster deps", "bold"))
    print(colorize("  " + "─" * 57, "dim"))

    repo_root = get_project_root()

    underspec = _underspecified_steps(plan)
    if underspec:
        total_bare = sum(n for _, n, _ in underspec)
        print(colorize(f"  Cannot confirm: {total_bare} step(s) still lack detail or issue_refs.", "red"))
        for name, bare, total in underspec[:5]:
            print(colorize(f"    {name}: {bare}/{total} steps", "yellow"))
        return

    bad_paths = _steps_with_bad_paths(plan, repo_root)
    if bad_paths:
        total_bad = sum(len(bp) for _, _, bp in bad_paths)
        print(colorize(f"\n  Cannot confirm: {total_bad} file path(s) in step details don't exist on disk.", "red"))
        for name, step_num, paths in bad_paths:
            for path_str in paths:
                print(colorize(f"    {name} step {step_num}: {path_str}", "yellow"))
        return

    untagged = _steps_without_effort(plan)
    if untagged:
        total_missing = sum(n for _, n, _ in untagged)
        print(colorize(f"\n  Cannot confirm: {total_missing} step(s) have no effort tag.", "red"))
        return

    no_refs = _steps_missing_issue_refs(plan)
    if no_refs:
        total_missing = sum(n for _, n, _ in no_refs)
        print(colorize(f"\n  Cannot confirm: {total_missing} step(s) have no issue_refs.", "red"))
        return

    vague = _steps_with_vague_detail(plan, repo_root)
    if vague:
        print(colorize(f"\n  Cannot confirm: {len(vague)} step(s) have vague detail.", "red"))
        return

    print(colorize("  All enrich-level checks pass.", "green"))

    sense_check_clusters = [n for n in plan.get("clusters", {}) if not plan["clusters"][n].get("auto")]

    if not attestation or len(attestation.strip()) < MIN_ATTESTATION_LEN:
        if attestation:
            print(colorize(f"\n  Attestation too short ({len(attestation.strip())} chars, min {MIN_ATTESTATION_LEN}).", "red"))
        print(colorize("\n  If satisfied, confirm:", "dim"))
        print(colorize('    desloppify plan triage --confirm sense-check --attestation "Content and structure verified..."', "dim"))
        return

    validation_err = validate_attestation(attestation.strip(), "sense-check", cluster_names=sense_check_clusters)
    if validation_err:
        print(colorize(f"\n  {validation_err}", "red"))
        return

    stages["sense-check"]["confirmed_at"] = utc_now()
    stages["sense-check"]["confirmed_text"] = attestation.strip()
    purge_triage_stage(plan, "sense-check")
    resolved_services.append_log_entry(
        plan,
        "triage_confirm_sense_check",
        actor="user",
        detail={"attestation": attestation.strip()},
    )
    resolved_services.save_plan(plan)
    print(colorize(f'  ✓ Sense-check confirmed: "{attestation.strip()}"', "green"))
    print_user_message(
        "Hey — sense-check is confirmed. Run `desloppify plan triage"
        " --complete --strategy \"...\"` to finish triage."
    )


__all__ = ["confirm_enrich", "confirm_sense_check"]
