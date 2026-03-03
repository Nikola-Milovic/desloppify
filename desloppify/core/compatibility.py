"""Runtime compatibility governance matrix and helper predicates.

This module is the single source of truth for import-boundary policy:
- public runtime modules (stable entry surfaces),
- deprecated compatibility modules (removal candidates),
- private modules (internal implementation details).
"""

from __future__ import annotations

from collections.abc import Iterable

# Public package roots intended for runtime imports across the codebase.
PUBLIC_RUNTIME_ROOTS: tuple[str, ...] = (
    "desloppify.app",
    "desloppify.core",
    "desloppify.engine",
    "desloppify.intelligence",
    "desloppify.languages",
)

# Compatibility shims removed from runtime usage; these must not be reintroduced.
SOFT_DEPRECATED_MODULES: frozenset[str] = frozenset(
    {
        "desloppify.utils",
        "desloppify.file_discovery",
        "desloppify.hook_registry",
        "desloppify.search",
        "desloppify.versioning",
        "desloppify.core.output_api",
        "desloppify.core.output_contract",
    }
)

SOFT_DEPRECATED_SHORT_IMPORTS: frozenset[str] = frozenset(
    {
        "utils",
        "file_discovery",
        "hook_registry",
        "search",
        "versioning",
    }
)

# Private internals: only code under these roots may import them directly.
PRIVATE_MODULE_PREFIXES: tuple[str, ...] = ("desloppify.core._internal",)
PRIVATE_ALLOWED_IMPORTER_PREFIXES: tuple[str, ...] = ("desloppify.core",)


def is_soft_deprecated_module(module: str) -> bool:
    """Return True when module is a soft-deprecated compatibility surface."""
    return module in SOFT_DEPRECATED_MODULES


def is_private_module(module: str) -> bool:
    """Return True when module path belongs to a private internal namespace."""
    normalized = module.strip()
    if not normalized:
        return False
    for prefix in PRIVATE_MODULE_PREFIXES:
        if normalized == prefix or normalized.startswith(f"{prefix}."):
            return True
    return False


def importer_can_access_private(importer_module: str) -> bool:
    """Return True when importer module is allowed to reference private APIs."""
    normalized = importer_module.strip()
    if not normalized:
        return False
    for prefix in PRIVATE_ALLOWED_IMPORTER_PREFIXES:
        if normalized == prefix or normalized.startswith(f"{prefix}."):
            return True
    return False


def iter_soft_deprecated_module_paths() -> Iterable[str]:
    """Yield python-module relative paths for deprecated compatibility modules."""
    for module in sorted(SOFT_DEPRECATED_MODULES):
        parts = module.split(".")
        yield "/".join(parts) + ".py"


__all__ = [
    "PRIVATE_ALLOWED_IMPORTER_PREFIXES",
    "PRIVATE_MODULE_PREFIXES",
    "PUBLIC_RUNTIME_ROOTS",
    "SOFT_DEPRECATED_MODULES",
    "SOFT_DEPRECATED_SHORT_IMPORTS",
    "importer_can_access_private",
    "is_private_module",
    "is_soft_deprecated_module",
    "iter_soft_deprecated_module_paths",
]
