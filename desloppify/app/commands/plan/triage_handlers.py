"""Handler for ``plan triage`` subcommand."""

from __future__ import annotations

import argparse

from desloppify.app.commands.helpers.runtime import command_runtime
from desloppify.app.commands.helpers.state import require_completed_scan
from desloppify.app.commands.plan.triage import confirmations as _confirmations_mod
from desloppify.app.commands.plan.triage import display as _display_mod
from desloppify.app.commands.plan.triage import helpers as _helpers_mod
from desloppify.app.commands.plan.triage import stages as _stages_mod
from desloppify.app.commands.plan.triage_playbook import TRIAGE_CMD_OBSERVE
from desloppify.core.output import colorize
from desloppify.engine.plan import (
    append_log_entry,
    build_triage_prompt,
    collect_triage_input,
    detect_recurring_patterns,
    extract_issue_citations,
    load_plan,
    save_plan,
)

_MIN_ATTESTATION_LEN = _confirmations_mod._MIN_ATTESTATION_LEN
_validate_attestation = _confirmations_mod._validate_attestation
_triage_coverage = _helpers_mod._triage_coverage


def _sync_triage_module_bindings() -> None:
    """Propagate monkeypatch-friendly bindings into split triage modules."""
    _helpers_mod.command_runtime = command_runtime
    _helpers_mod.save_plan = save_plan

    _display_mod.command_runtime = command_runtime
    _display_mod.collect_triage_input = collect_triage_input
    _display_mod.detect_recurring_patterns = detect_recurring_patterns
    _display_mod.load_plan = load_plan

    _confirmations_mod.append_log_entry = append_log_entry
    _confirmations_mod.collect_triage_input = collect_triage_input
    _confirmations_mod.command_runtime = command_runtime
    _confirmations_mod.detect_recurring_patterns = detect_recurring_patterns
    _confirmations_mod.load_plan = load_plan
    _confirmations_mod.save_plan = save_plan

    _stages_mod.append_log_entry = append_log_entry
    _stages_mod.collect_triage_input = collect_triage_input
    _stages_mod.command_runtime = command_runtime
    _stages_mod.detect_recurring_patterns = detect_recurring_patterns
    _stages_mod.extract_issue_citations = extract_issue_citations
    _stages_mod.load_plan = load_plan
    _stages_mod.save_plan = save_plan


def _cmd_triage_start(args: argparse.Namespace) -> None:
    """Manually inject triage stage IDs into the queue and clear prior stages."""
    _sync_triage_module_bindings()
    plan = load_plan()

    if _helpers_mod._has_triage_in_queue(plan):
        print(colorize("  Planning mode stages are already in the queue.", "yellow"))
        meta = plan.get("epic_triage_meta", {})
        stages = meta.get("triage_stages", {})
        if stages:
            print(
                colorize(
                    f"  {len(stages)} stage(s) in progress — clearing to restart.", "yellow"
                )
            )
            meta["triage_stages"] = {}
            _helpers_mod._inject_triage_stages(plan)
            save_plan(plan)
            append_log_entry(
                plan,
                "triage_start",
                actor="user",
                detail={"action": "restart", "cleared_stages": list(stages.keys())},
            )
            save_plan(plan)
            print(colorize("  Stages cleared. Begin with observe:", "green"))
        else:
            print(colorize("  Begin with observe:", "green"))
        print(colorize(f"    {TRIAGE_CMD_OBSERVE}", "dim"))
        return

    _helpers_mod._inject_triage_stages(plan)
    meta = plan.setdefault("epic_triage_meta", {})
    meta["triage_stages"] = {}
    save_plan(plan)

    append_log_entry(plan, "triage_start", actor="user", detail={"action": "start"})
    save_plan(plan)

    runtime = command_runtime(args)
    si = collect_triage_input(plan, runtime.state)
    print(colorize("  Planning mode started (4 stages queued).", "green"))
    print(f"  Open review issues: {len(si.open_issues)}")
    print(colorize("  Begin with observe:", "dim"))
    print(colorize(f"    {TRIAGE_CMD_OBSERVE}", "dim"))


def cmd_plan_triage(args: argparse.Namespace) -> None:
    """Run epic triage: staged workflow OBSERVE → REFLECT → ORGANIZE → COMMIT."""
    _sync_triage_module_bindings()
    runtime = command_runtime(args)
    state = runtime.state
    if not require_completed_scan(state):
        return

    if getattr(args, "start", False):
        _cmd_triage_start(args)
        return
    if getattr(args, "confirm", None):
        _confirmations_mod._cmd_confirm_stage(args)
        return
    if getattr(args, "complete", False):
        _stages_mod._cmd_triage_complete(args)
        return
    if getattr(args, "confirm_existing", False):
        _stages_mod._cmd_confirm_existing(args)
        return

    stage = getattr(args, "stage", None)
    if stage == "observe":
        _stages_mod._cmd_stage_observe(args)
        return
    if stage == "reflect":
        _stages_mod._cmd_stage_reflect(args)
        return
    if stage == "organize":
        _stages_mod._cmd_stage_organize(args)
        return

    if getattr(args, "dry_run", False):
        plan = load_plan()
        si = collect_triage_input(plan, state)
        prompt = build_triage_prompt(si)
        print(colorize("  Epic triage — dry run", "bold"))
        print(colorize("  " + "─" * 60, "dim"))
        print(f"  Open review issues: {len(si.open_issues)}")
        print(f"  Existing epics: {len(si.existing_epics)}")
        print(f"  New since last: {len(si.new_since_last)}")
        print(f"  Resolved since last: {len(si.resolved_since_last)}")
        print(colorize("\n  Prompt that would be sent to LLM:", "dim"))
        print()
        print(prompt)
        return

    _display_mod._cmd_triage_dashboard(args)

__all__ = [
    "_MIN_ATTESTATION_LEN",
    "_triage_coverage",
    "_validate_attestation",
    "cmd_plan_triage",
]
