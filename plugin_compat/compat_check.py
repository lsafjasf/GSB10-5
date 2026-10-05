"""Plugin version compatibility checker.

A plugin version is described by an *interface manifest* (a plain
JSON-serializable dict).  Compatibility is judged from the interface and its
semantics -- parameters, return type, default values, and error semantics --
not from the version number.

Manifest schema
---------------
{
  "name":      str,
  "version":   str,                       # informational only
  "functions": {func_name: {
      "params":  [ {"name": str,
                    "kind": "positional_or_keyword"
                           | "positional_only"
                           | "keyword_only"
                           | "var_positional"
                           | "var_keyword",
                    "required": bool,
                    "default": <any JSON value, present iff required is False>},
                   ... ],
      "returns": str,                      # return type contract
      "errors":  { condition:str : error_name:str }
  }, ...}
}

Verdicts
--------
"compatible"           -- the two interfaces are semantically identical
"backward_compatible"  -- every existing caller keeps working
"incompatible"         -- at least one rule below fires
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

COMPATIBLE = "compatible"
BACKWARD_COMPATIBLE = "backward_compatible"
INCOMPATIBLE = "incompatible"

# Finding severity codes.
BC = "backward_compatible"
IC = "incompatible"


@dataclass(frozen=True)
class Finding:
    """One ordered, machine-readable compatibility finding."""

    level: str          # BC or IC
    code: str           # stable rule code, see RULES below
    target: str         # e.g. "function resize_image" or parameter path
    detail: str         # human-readable description

    def as_dict(self) -> Dict[str, str]:
        return {"level": self.level, "code": self.code,
                "target": self.target, "detail": self.detail}


@dataclass(frozen=True)
class CheckResult:
    verdict: str
    findings: Tuple[Finding, ...] = field(default_factory=tuple)

    @property
    def incompatibilities(self) -> Tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.level == IC)

    @property
    def backward_compatible_changes(self) -> Tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.level == BC)

    def as_dict(self) -> Dict[str, Any]:
        return {"verdict": self.verdict,
                "incompatibilities": [f.as_dict()
                                      for f in self.incompatibilities],
                "backward_compatible_changes":
                    [f.as_dict() for f in self.backward_compatible_changes]}


# Rule catalogue.  Each code documents exactly why the finding fires.
RULES: Dict[str, str] = {
    "FUNCTION_REMOVED":
        "A public function visible in the old version is gone; existing "
        "callers importing/invoking it break. [incompatible]",
    "FUNCTION_ADDED":
        "A new function does not affect any existing caller. "
        "[backward compatible]",
    "PARAM_REMOVED":
        "A parameter existing callers may pass positionally or by name is "
        "gone; calls passing it raise. [incompatible]",
    "PARAM_ADDED_REQUIRED":
        "A new parameter has no default; old call sites that omit it now "
        "fail. [incompatible]",
    "PARAM_ADDED_OPTIONAL":
        "A new parameter has a default; old call sites are unaffected. "
        "[backward compatible]",
    "PARAM_KIND_CHANGED":
        "Parameter calling convention changed (e.g. positional -> "
        "keyword-only); valid old call forms stop binding. [incompatible]",
    "REQUIRED_FLAG_CHANGED":
        "An optional parameter became required; call sites omitting it now "
        "fail. (The reverse direction is covered by PARAM_ADDED_OPTIONAL "
        "semantics and is backward compatible.) [incompatible]",
    "DEFAULT_CHANGED":
        "Default value of an optional parameter changed; callers that omit "
        "the parameter silently get different behaviour with no error. "
        "Version numbers do not protect against this. [incompatible]",
    "RETURN_TYPE_CHANGED":
        "Declared return contract changed; callers consuming the old shape "
        "or type break. [incompatible]",
    "ERROR_REMOVED":
        "A documented error condition no longer raises the documented "
        "error; callers relying on catching it for that condition hang or "
        "take a wrong branch. [incompatible]",
    "ERROR_ADDED":
        "A new error is raised on a path existing callers could previously "
        "drive without catching it. [incompatible]",
    "ERROR_NAME_CHANGED":
        "The same documented condition now raises a different error; "
        "`except OldError` no longer matches. [incompatible]",
}


def _f(level: str, code: str, target: str, detail: str) -> Finding:
    return Finding(level, code, target, detail)


def _index_params(params: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {p["name"]: p for p in params}


def _check_function(name: str, old: Dict[str, Any],
                    new: Dict[str, Any]) -> List[Finding]:
    findings: List[Finding] = []
    target = f"function {name}"
    old_params = _index_params(old.get("params", []))
    new_params = _index_params(new.get("params", []))

    # Parameter list -- keep the old declaration order so findings are stable.
    for pname, op in old_params.items():
        ptarget = f"{name}({pname})"
        if pname not in new_params:
            findings.append(_f(IC, "PARAM_REMOVED", ptarget,
                               f"parameter removed from {target}"))
            continue
        np = new_params[pname]
        if op.get("kind", "positional_or_keyword") != \
                np.get("kind", "positional_or_keyword"):
            findings.append(_f(
                IC, "PARAM_KIND_CHANGED", ptarget,
                f"kind {op.get('kind', 'positional_or_keyword')!r} -> "
                f"{np.get('kind', 'positional_or_keyword')!r}"))
        if not op.get("required", False) and np.get("required", False):
            findings.append(_f(IC, "REQUIRED_FLAG_CHANGED", ptarget,
                               "optional parameter became required"))
        if not op.get("required", False) and not np.get("required", False) \
                and op.get("default") != np.get("default"):
            findings.append(_f(
                IC, "DEFAULT_CHANGED", ptarget,
                f"default {op.get('default')!r} -> {np.get('default')!r}; "
                "callers omitting it silently change behaviour"))
        if op.get("required", False) and not np.get("required", False):
            findings.append(_f(
                BC, "PARAM_ADDED_OPTIONAL", ptarget,
                f"previously required, now optional with default "
                f"{np.get('default')!r}; old call sites still valid"))

    for pname in sorted(set(new_params) - set(old_params)):
        np = new_params[pname]
        ptarget = f"{name}({pname})"
        if np.get("required", False):
            findings.append(_f(IC, "PARAM_ADDED_REQUIRED", ptarget,
                               f"new required parameter on {target}"))
        else:
            findings.append(_f(BC, "PARAM_ADDED_OPTIONAL", ptarget,
                               f"new optional parameter with default "
                               f"{np.get('default')!r}"))

    # Return type contract.
    if old.get("returns") != new.get("returns"):
        findings.append(_f(
            IC, "RETURN_TYPE_CHANGED", target,
            f"returns {old.get('returns')!r} -> {new.get('returns')!r}"))

    # Error semantics: {condition: error_name}.
    old_errs: Dict[str, str] = dict(old.get("errors", {}))
    new_errs: Dict[str, str] = dict(new.get("errors", {}))
    for cond in sorted(set(old_errs) | set(new_errs)):
        etarget = f"{name} errors[{cond}]"
        if cond in old_errs and cond not in new_errs:
            findings.append(_f(
                IC, "ERROR_REMOVED", etarget,
                f"condition {cond!r} no longer raises {old_errs[cond]!r}"))
        elif cond not in old_errs and cond in new_errs:
            findings.append(_f(
                IC, "ERROR_ADDED", etarget,
                f"condition {cond!r} now raises {new_errs[cond]!r}"))
        elif old_errs[cond] != new_errs[cond]:
            findings.append(_f(
                IC, "ERROR_NAME_CHANGED", etarget,
                f"condition {cond!r}: {old_errs[cond]!r} -> "
                f"{new_errs[cond]!r}"))

    return findings


def check_compatibility(old: Dict[str, Any],
                        new: Dict[str, Any]) -> CheckResult:
    """Compare two plugin manifests and return a deterministic verdict.

    Rule precedence: any incompatible finding makes the overall verdict
    "incompatible"; otherwise any backward-compatible finding makes it
    "backward_compatible"; otherwise the interfaces are "compatible".
    """
    findings: List[Finding] = []
    old_funcs: Dict[str, Any] = dict(old.get("functions", {}))
    new_funcs: Dict[str, Any] = dict(new.get("functions", {}))

    for fname in sorted(old_funcs):
        if fname not in new_funcs:
            findings.append(_f(IC, "FUNCTION_REMOVED", f"function {fname}",
                               "public function removed"))
        else:
            findings.extend(_check_function(fname, old_funcs[fname],
                                            new_funcs[fname]))
    for fname in sorted(set(new_funcs) - set(old_funcs)):
        findings.append(_f(BC, "FUNCTION_ADDED", f"function {fname}",
                           "new public function added"))

    if any(f.level == IC for f in findings):
        verdict = INCOMPATIBLE
    elif findings:
        verdict = BACKWARD_COMPATIBLE
    else:
        verdict = COMPATIBLE
    return CheckResult(verdict, tuple(findings))
