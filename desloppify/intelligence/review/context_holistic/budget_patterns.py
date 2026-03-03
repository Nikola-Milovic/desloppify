"""Pattern detectors for abstractions budget context."""

from __future__ import annotations

import ast

from desloppify.core.discovery_api import rel
from desloppify.intelligence.review.context_holistic.budget_analysis import (
    _strip_docstring,
)

_VIOLATION_METHODS = frozenset({"get", "setdefault", "pop"})

def _python_passthrough_target(stmt: ast.stmt) -> str | None:
    """Return passthrough call target when stmt is `return target(...)`."""
    if not isinstance(stmt, ast.Return):
        return None
    value = stmt.value
    if not isinstance(value, ast.Call):
        return None
    target = value.func
    if isinstance(target, ast.Name):
        return target.id
    return None

def _find_python_passthrough_wrappers(tree: ast.Module) -> list[tuple[str, str]]:
    """Find Python wrapper pairs via AST traversal."""
    wrappers: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue

        body = _strip_docstring(list(node.body))
        if len(body) != 1:
            continue

        target_name = _python_passthrough_target(body[0])
        if target_name and node.name != target_name:
            wrappers.append((node.name, target_name))
    return wrappers

def _is_delegation_stmt(stmt: ast.stmt) -> str | None:
    """Return the delegate attribute name if *stmt* is a pure delegation.

    Matches patterns like:
    - ``return self.x.method(...)``
    - ``return self.x``  (property forwarding)
    - ``self.x.method(...)``  (void delegation)
    """
    # Unwrap Expr nodes (void calls like ``self.x.do()``)
    if isinstance(stmt, ast.Expr):
        value = stmt.value
    elif isinstance(stmt, ast.Return) and stmt.value is not None:
        value = stmt.value
    else:
        return None

    # ``self.x.method(...)`` or ``self.x(...)``
    if isinstance(value, ast.Call):
        value = value.func

    # Walk the attribute chain to find ``self.<attr>``
    node = value
    depth = 0
    while isinstance(node, ast.Attribute):
        node = node.value
        depth += 1
    if depth < 1 or not isinstance(node, ast.Name) or node.id != "self":
        return None

    # The first attribute after self — walk back down from the outermost
    # Attribute to find the one whose .value is the Name("self") node.
    first = value
    while isinstance(first, ast.Attribute) and isinstance(first.value, ast.Attribute):
        first = first.value
    if isinstance(first, ast.Attribute) and isinstance(first.value, ast.Name):
        return first.attr
    return None

def _find_delegation_heavy_classes(tree: ast.Module) -> list[dict]:
    """Find classes where most methods delegate to a single inner object."""
    results: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue

        methods: list[ast.FunctionDef | ast.AsyncFunctionDef] = [
            child
            for child in node.body
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
            and child.name != "__init__"
        ]
        if len(methods) <= 3:
            continue

        # Track which methods delegate to which attribute
        delegating_methods: dict[str, list[str]] = {}  # attr -> [method_names]
        for method in methods:
            body = _strip_docstring(list(method.body))
            if len(body) != 1:
                continue
            attr = _is_delegation_stmt(body[0])
            if attr:
                delegating_methods.setdefault(attr, []).append(method.name)

        if not delegating_methods:
            continue

        # Use the most common delegate target
        top_attr = max(delegating_methods, key=lambda a: len(delegating_methods[a]))
        delegate_count = len(delegating_methods[top_attr])
        ratio = delegate_count / len(methods)
        if ratio > 0.5:
            results.append(
                {
                    "class_name": node.name,
                    "line": node.lineno,
                    "delegation_ratio": round(ratio, 2),
                    "method_count": len(methods),
                    "delegate_count": delegate_count,
                    "delegate_target": top_attr,
                    "sample_methods": delegating_methods[top_attr][:5],
                }
            )
    return results

def _find_facade_modules(tree: ast.Module, *, loc: int) -> dict | None:
    """Detect modules where >70% of public names come from imports."""
    import_names: set[str] = set()
    defined_names: set[str] = set()

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[-1]
                import_names.add(name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                import_names.add(name)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            defined_names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            defined_names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    continue
                if isinstance(target, ast.Name):
                    defined_names.add(target.id)

    # Remove private names
    public_imports = {n for n in import_names if not n.startswith("_")}
    public_defs = {n for n in defined_names if not n.startswith("_")}

    total_public = len(public_imports | public_defs)
    if total_public < 3:
        return None

    # Re-exported = imported names that aren't shadowed by a local definition
    re_exported = public_imports - public_defs
    re_export_ratio = len(re_exported) / total_public

    if re_export_ratio < 0.7 or len(public_defs) > 3:
        return None

    return {
        "re_export_ratio": round(re_export_ratio, 2),
        "defined_symbols": len(public_defs),
        "re_exported_symbols": len(re_exported),
        "samples": sorted(re_exported)[:5],
        "loc": loc,
    }

def _collect_typed_dict_defs(
    tree: ast.Module, accumulator: dict[str, set[str]]
) -> None:
    """Collect TypedDict class definitions from a single file's AST."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        is_typed_dict = any(
            (isinstance(b, ast.Name) and b.id == "TypedDict")
            or (isinstance(b, ast.Attribute) and b.attr == "TypedDict")
            for b in node.bases
        )
        if not is_typed_dict:
            continue
        fields: set[str] = set()
        for child in node.body:
            if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                fields.add(child.target.id)
        if fields:
            accumulator[node.name] = fields

def _find_typed_dict_usage_violations(
    parsed_trees: dict[str, ast.Module],
    typed_dicts: dict[str, set[str]],
) -> list[dict]:
    """Find .get()/.setdefault()/.pop() calls on TypedDict-annotated variables.

    *parsed_trees* maps absolute file paths to pre-parsed ASTs (built during
    the main collection loop to avoid redundant parses).

    Returns a list of violation dicts with file, typed_dict_name, violation_type,
    line, field (when extractable), and count.
    """
    if not typed_dicts:
        return []

    violations: list[dict] = []
    for filepath, tree in parsed_trees.items():
        rpath = rel(filepath)

        # Collect variable names annotated with known TypedDict types
        typed_vars: dict[str, str] = {}  # var_name -> TypedDict name
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                ann = node.annotation
                ann_name = None
                if isinstance(ann, ast.Name):
                    ann_name = ann.id
                elif isinstance(ann, ast.Attribute):
                    ann_name = ann.attr
                if ann_name in typed_dicts:
                    typed_vars[node.target.id] = ann_name
            # Also check function param annotations
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for arg in node.args.args + node.args.kwonlyargs:
                    ann = arg.annotation
                    if ann is None:
                        continue
                    ann_name = None
                    if isinstance(ann, ast.Name):
                        ann_name = ann.id
                    elif isinstance(ann, ast.Attribute):
                        ann_name = ann.attr
                    if ann_name in typed_dicts:
                        typed_vars[arg.arg] = ann_name

        if not typed_vars:
            continue

        # Scan for violation calls — collect per (td_name, method, field)
        hits: list[tuple[str, str, str | None, int]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in _VIOLATION_METHODS:
                continue
            if not isinstance(func.value, ast.Name) or func.value.id not in typed_vars:
                continue
            td_name = typed_vars[func.value.id]
            # Extract the field name from the first argument if it's a string constant
            field: str | None = None
            if node.args and isinstance(node.args[0], ast.Constant):
                val = node.args[0].value
                if isinstance(val, str):
                    field = val
            hits.append((td_name, func.attr, field, node.lineno))

        # Group by (td_name, method, field) for compact reporting
        groups: dict[tuple[str, str, str | None], list[int]] = {}
        for td_name, method, field, lineno in hits:
            groups.setdefault((td_name, method, field), []).append(lineno)

        for (td_name, method, field), lines in groups.items():
            entry: dict = {
                "file": rpath,
                "typed_dict_name": td_name,
                "violation_type": method,
                "line": lines[0],
                "count": len(lines),
            }
            if field is not None:
                entry["field"] = field
            violations.append(entry)

    return violations


# ── dict[str, Any] annotation scanner ─────────────────────


def _find_dict_any_annotations(
    parsed_trees: dict[str, ast.Module],
    typed_dict_names: set[str],
) -> list[dict]:
    """Find function parameters/returns annotated as ``dict[str, Any]``.

    Cross-references parameter names against known TypedDict names to suggest
    concrete alternatives when available.
    """
    results: list[dict] = []
    for filepath, tree in parsed_trees.items():
        rpath = rel(filepath)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            # Check args
            all_args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            if node.args.vararg:
                all_args.append(node.args.vararg)
            if node.args.kwarg:
                all_args.append(node.args.kwarg)
            for arg in all_args:
                if arg.annotation and _is_dict_str_any(arg.annotation):
                    alt = _guess_alternative(arg.arg, typed_dict_names)
                    results.append({
                        "file": rpath,
                        "function": node.name,
                        "param": arg.arg,
                        "line": arg.lineno,
                        "known_alternative": alt,
                    })
            # Check return annotation
            if node.returns and _is_dict_str_any(node.returns):
                results.append({
                    "file": rpath,
                    "function": node.name,
                    "param": "(return)",
                    "line": node.lineno,
                    "known_alternative": None,
                })
    return results


def _is_dict_str_any(ann: ast.expr) -> bool:
    """Check if annotation is ``dict[str, Any]``."""
    if not isinstance(ann, ast.Subscript):
        return False
    if not (isinstance(ann.value, ast.Name) and ann.value.id == "dict"):
        return False
    sl = ann.slice
    if isinstance(sl, ast.Tuple) and len(sl.elts) == 2:
        first, second = sl.elts
        if isinstance(first, ast.Name) and first.id == "str":
            if isinstance(second, ast.Name) and second.id == "Any":
                return True
    return False


def _guess_alternative(param_name: str, typed_dict_names: set[str]) -> str | None:
    """Guess a TypedDict alternative by matching param name fragments."""
    if len(param_name) < 4:
        return None
    lower = param_name.lower()
    matches: list[str] = []
    for td_name in sorted(typed_dict_names):
        if td_name.lower() in lower or lower in td_name.lower():
            matches.append(td_name)
    if len(matches) == 1:
        return matches[0]
    return None


# ── Enum bypass scanner ───────────────────────────────────

# Int values too generic to be meaningful enum bypass signals.
_GENERIC_INT_VALUES: frozenset[object] = frozenset({0, 1, 2, 3, -1})


def _collect_enum_defs(
    parsed_trees: dict[str, ast.Module],
) -> dict[tuple[str, str], dict]:
    """Find StrEnum/IntEnum/Enum class defs.

    Returns ``{(file, name): {file, members: {name: value}}}``.
    Keyed by ``(file, class_name)`` to avoid silently overwriting
    same-named enums from different files.
    """
    _ENUM_BASES = {"StrEnum", "IntEnum", "Enum"}
    result: dict[tuple[str, str], dict] = {}
    for filepath, tree in parsed_trees.items():
        rpath = rel(filepath)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            is_enum = any(
                (isinstance(b, ast.Name) and b.id in _ENUM_BASES)
                or (isinstance(b, ast.Attribute) and b.attr in _ENUM_BASES)
                for b in node.bases
            )
            if not is_enum:
                continue
            members: dict[str, object] = {}
            for child in node.body:
                if isinstance(child, ast.Assign):
                    for target in child.targets:
                        if isinstance(target, ast.Name) and isinstance(
                            child.value, ast.Constant
                        ):
                            members[target.id] = child.value.value
            if members:
                result[(rpath, node.name)] = {"file": rpath, "members": members}
    return result


def _find_enum_bypass(
    parsed_trees: dict[str, ast.Module],
    enum_defs: dict[tuple[str, str], dict],
) -> list[dict]:
    """Find raw string/int comparisons that match enum member values.

    Only flags ``==`` and ``!=`` comparisons (not ``>``, ``<``, etc.).
    Skips generic int literals (0, 1, 2, 3, -1, True, False) since they
    produce too many false positives.  Skips the file where the enum is
    defined (internal comparisons are expected).
    """
    if not enum_defs:
        return []

    # Collect the set of files that define enums so we can skip them.
    enum_def_files: set[str] = {info["file"] for info in enum_defs.values()}

    # Build reverse lookup: value → list of (enum_name, member_name).
    # Using a list avoids last-write-wins when two enums share a value.
    value_to_enums: dict[object, list[tuple[str, str]]] = {}
    for (_file, enum_name), info in enum_defs.items():
        for member_name, value in info["members"].items():
            if isinstance(value, str):
                value_to_enums.setdefault(value, []).append((enum_name, member_name))
            elif isinstance(value, int) and value not in _GENERIC_INT_VALUES:
                value_to_enums.setdefault(value, []).append((enum_name, member_name))

    if not value_to_enums:
        return []

    results: list[dict] = []
    for filepath, tree in parsed_trees.items():
        rpath = rel(filepath)
        # Skip files that define enums — internal comparisons are expected.
        if rpath in enum_def_files:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            # Only flag == and != operators.
            if not all(isinstance(op, ast.Eq | ast.NotEq) for op in node.ops):
                continue
            # Check all comparators (left + comparators)
            for const_node in [node.left, *node.comparators]:
                if not isinstance(const_node, ast.Constant):
                    continue
                key = const_node.value
                if key in value_to_enums:
                    for enum_name, member in value_to_enums[key]:
                        results.append({
                            "file": rpath,
                            "line": node.lineno,
                            "enum_name": enum_name,
                            "member": member,
                            "raw_value": repr(key),
                        })
    return results


# ── Type strategy census ──────────────────────────────────


def _census_type_strategies(
    parsed_trees: dict[str, ast.Module],
) -> dict[str, list[dict]]:
    """Count domain object definitions by strategy.

    Returns ``{strategy: [{name, file, field_count}]}``.
    """
    strategies: dict[str, list[dict]] = {
        "TypedDict": [],
        "dataclass": [],
        "frozen_dataclass": [],
        "NamedTuple": [],
    }
    for filepath, tree in parsed_trees.items():
        rpath = rel(filepath)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            field_count = sum(
                1 for c in node.body if isinstance(c, ast.AnnAssign)
            )
            entry = {"name": node.name, "file": rpath, "field_count": field_count}

            # TypedDict
            if any(
                (isinstance(b, ast.Name) and b.id == "TypedDict")
                or (isinstance(b, ast.Attribute) and b.attr == "TypedDict")
                for b in node.bases
            ):
                strategies["TypedDict"].append(entry)
                continue

            # NamedTuple
            if any(
                (isinstance(b, ast.Name) and b.id == "NamedTuple")
                or (isinstance(b, ast.Attribute) and b.attr == "NamedTuple")
                for b in node.bases
            ):
                strategies["NamedTuple"].append(entry)
                continue

            # dataclass (check decorators)
            for dec in node.decorator_list:
                is_dc = False
                is_frozen = False
                if isinstance(dec, ast.Name) and dec.id == "dataclass":
                    is_dc = True
                elif isinstance(dec, ast.Attribute) and dec.attr == "dataclass":
                    is_dc = True
                elif isinstance(dec, ast.Call):
                    func = dec.func
                    if (isinstance(func, ast.Name) and func.id == "dataclass") or (
                        isinstance(func, ast.Attribute) and func.attr == "dataclass"
                    ):
                        is_dc = True
                        # Check for frozen=True
                        for kw in dec.keywords:
                            if (
                                kw.arg == "frozen"
                                and isinstance(kw.value, ast.Constant)
                                and kw.value.value is True
                            ):
                                is_frozen = True
                if is_dc:
                    key = "frozen_dataclass" if is_frozen else "dataclass"
                    strategies[key].append(entry)
                    break

    # Remove empty strategies
    return {k: v for k, v in strategies.items() if v}


__all__ = [
    "_census_type_strategies",
    "_collect_enum_defs",
    "_collect_typed_dict_defs",
    "_find_delegation_heavy_classes",
    "_find_dict_any_annotations",
    "_find_enum_bypass",
    "_find_facade_modules",
    "_find_python_passthrough_wrappers",
    "_find_typed_dict_usage_violations",
    "_is_delegation_stmt",
    "_python_passthrough_target",
]
