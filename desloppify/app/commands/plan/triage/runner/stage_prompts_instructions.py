"""Static prompt text and stage-instruction helpers for triage runner."""

from __future__ import annotations

from .stage_prompts_instruction_blocks import (
    _STAGE_INSTRUCTIONS,
    _enrich_instructions,
    _observe_instructions,
    _organize_instructions,
    _reflect_instructions,
    _sense_check_instructions,
)
from .stage_prompts_instruction_shared import _CLI_REFERENCE, _PREAMBLE, _STAGES

__all__ = [
    "_CLI_REFERENCE",
    "_PREAMBLE",
    "_STAGES",
    "_STAGE_INSTRUCTIONS",
    "_enrich_instructions",
    "_observe_instructions",
    "_organize_instructions",
    "_reflect_instructions",
    "_sense_check_instructions",
]
