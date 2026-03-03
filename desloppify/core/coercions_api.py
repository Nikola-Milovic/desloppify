"""Public coercion helpers for CLI/runtime input parsing."""

from __future__ import annotations

from desloppify.core._internal import coercions as coercions_mod


def coerce_positive_int(value: object, *, default: int, minimum: int = 1) -> int:
    return coercions_mod.coerce_positive_int(value, default=default, minimum=minimum)


def coerce_positive_float(
    value: object,
    *,
    default: float,
    minimum: float = 0.1,
) -> float:
    return coercions_mod.coerce_positive_float(value, default=default, minimum=minimum)


def coerce_non_negative_float(value: object, *, default: float) -> float:
    return coercions_mod.coerce_non_negative_float(value, default=default)


def coerce_non_negative_int(value: object, *, default: int) -> int:
    return coercions_mod.coerce_non_negative_int(value, default=default)


def coerce_confidence(value: object, *, default: float = 1.0) -> float:
    return coercions_mod.coerce_confidence(value, default=default)


def coerce_optional_str(value: object) -> str | None:
    return coercions_mod.coerce_optional_str(value)


__all__ = [
    "coerce_optional_str",
    "coerce_confidence",
    "coerce_non_negative_float",
    "coerce_non_negative_int",
    "coerce_positive_float",
    "coerce_positive_int",
]
