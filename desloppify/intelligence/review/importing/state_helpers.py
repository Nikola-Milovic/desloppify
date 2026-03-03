"""State access helpers for review import workflows."""

from __future__ import annotations

from desloppify.engine._state.schema import StateModel


def _review_file_cache(state: StateModel) -> dict:
    """Access ``state["review_cache"]["files"]``, creating if absent."""
    return state.setdefault("review_cache", {}).setdefault("files", {})


def _lang_potentials(state: StateModel, lang_name: str) -> dict:
    """Access ``state["potentials"][lang_name]``, creating if absent."""
    return state.setdefault("potentials", {}).setdefault(lang_name, {})
