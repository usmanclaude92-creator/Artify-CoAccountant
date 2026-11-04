"""Semantic breaking-change check for the API contract (packages/api-client/openapi.json).

Compares a BASE document with the NEW one and reports what existing generated clients could
break on. Rules (BREAKING fails the run unless allow-listed; WARNING is informational):

  request side   BREAKING: operation/path removed or operationId changed; new required parameter or
                 body property; parameter/property type narrowed; enum value removed; limits tightened
                 (maxLength down, minLength/minimum up, pattern changed); body newly required.
  response side  BREAKING: success status removed; property removed; property type changed to one the
                 old clients did not expect; required property became optional.
                 WARNING: enum value added.

usage:  check_openapi_breaking.py BASE NEW [--allow FILE]
        BASE/NEW are file paths, or git:<ref> to read packages/api-client/openapi.json at a ref.
An allow-list line is the exact issue key printed in the report (one per line, '#' comments).
Pre-1.0 (API version 0.x) breaking changes are permitted only when allow-listed with a reason."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

CONTRACT = "packages/api-client/openapi.json"
METHODS = ("get", "put", "post", "delete", "patch", "head", "options")
Issue = tuple[str, str, str]  # (severity, key, message)


def _resolve(doc: dict[str, Any], node: Any, seen: tuple[str, ...] = ()) -> Any:
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if ref in seen:
            return {}
        seen += (ref,)
        target: Any = doc
        for part in ref.removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def _types(doc: dict[str, Any], schema: Any) -> set[str]:
    """Flatten a schema's accepted JSON types (anyOf/oneOf/nullable aware)."""
    schema = _resolve(doc, schema)
    if not isinstance(schema, dict):
        return {"any"}
    out: set[str] = set()
    for branch in schema.get("anyOf", []) + schema.get("oneOf", []):
        out |= _types(doc, branch)
    t = schema.get("type")
    if isinstance(t, list):
        out |= set(t)
    elif t:
        out.add(t)
    elif "enum" in schema or "const" in schema:
        out.add("enum")
    elif not out:
        out.add("object" if "properties" in schema else "any")
    return out


def _compare_schema(
    od: dict[str, Any],
    old: Any,
    nd: dict[str, Any],
    new: Any,
    where: str,
    side: str,
    issues: list[Issue],
    depth: int = 0,
) -> None:
    if depth > 8:
        return
    old, new = _resolve(od, old), _resolve(nd, new)
    if not isinstance(old, dict) or not isinstance(new, dict):
        return
    ot, nt = _types(od, old), _types(nd, new)
    if "any" not in ot and "any" not in nt:
        if side == "request" and not ot <= nt:
            issues.append(
                ("BREAKING", f"REQ_TYPE_NARROWED {where}", f"{where}: accepted types {sorted(ot)} -> {sorted(nt)}")
            )
        if side == "response" and not nt <= ot:
            issues.append(
                ("BREAKING", f"RESP_TYPE_CHANGED {where}", f"{where}: returned types {sorted(ot)} -> {sorted(nt)}")
            )
    oe, ne = set(map(str, old.get("enum", []))), set(map(str, new.get("enum", [])))
    if oe and ne:
        if side == "request" and oe - ne:
            issues.append(
                ("BREAKING", f"REQ_ENUM_REMOVED {where}", f"{where}: values no longer accepted: {sorted(oe - ne)}")
            )
        if side == "response" and ne - oe:
            issues.append(
                ("WARNING", f"RESP_ENUM_ADDED {where}", f"{where}: new values may be returned: {sorted(ne - oe)}")
            )
    if side == "request":
        for key, worse in (
            ("maxLength", lambda a, b: b < a),
            ("maximum", lambda a, b: b < a),
            ("minLength", lambda a, b: b > a),
            ("minimum", lambda a, b: b > a),
        ):
            if key in new and (key not in old or worse(old[key], new[key])):
                issues.append(
                    ("BREAKING", f"REQ_LIMIT_TIGHTENED {where} {key}", f"{where}: {key} {old.get(key)} -> {new[key]}")
                )
        if new.get("pattern") != old.get("pattern") and "pattern" in new:
            issues.append(("BREAKING", f"REQ_PATTERN_CHANGED {where}", f"{where}: pattern changed"))
    op, np_ = old.get("properties", {}), new.get("properties", {})
    oreq, nreq = set(old.get("required", [])), set(new.get("required", []))
    for name, sub in op.items():
        if name not in np_:
            if side == "response":
                issues.append(
                    ("BREAKING", f"RESP_PROPERTY_REMOVED {where}.{name}", f"{where}: property '{name}' removed")
                )
            continue
        _compare_schema(od, sub, nd, np_[name], f"{where}.{name}", side, issues, depth + 1)
    for name in np_:
        if name not in op and side == "request" and name in nreq:
            issues.append(
                ("BREAKING", f"REQ_REQUIRED_ADDED {where}.{name}", f"{where}: new required property '{name}'")
            )
    for name in nreq - oreq:
        if name in op and side == "request":
            issues.append(
                ("BREAKING", f"REQ_REQUIRED_ADDED {where}.{name}", f"{where}: property '{name}' became required")
            )
    for name in oreq - nreq:
        if name in op and side == "response":
            issues.append(
                (
                    "BREAKING",
                    f"RESP_REQUIRED_DROPPED {where}.{name}",
                    f"{where}: property '{name}' is no longer always present",
                )
            )
    if "items" in old and "items" in new:
        _compare_schema(od, old["items"], nd, new["items"], f"{where}[]", side, issues, depth + 1)


def _body_schema(doc: dict[str, Any], container: Any) -> Any:
    container = _resolve(doc, container) or {}
    content = container.get("content", {})
    for media in ("application/json", "application/problem+json"):
        if media in content:
            return content[media].get("schema")
    return next((v.get("schema") for v in content.values()), None)


def diff(base: dict[str, Any], new: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    bp, np_ = base.get("paths", {}), new.get("paths", {})
    for path, ops in bp.items():
        for method in METHODS:
            if method not in ops:
                continue
            tag = f"{method.upper()} {path}"
            nop = np_.get(path, {}).get(method)
            if nop is None:
                issues.append(("BREAKING", f"OP_REMOVED {tag}", f"{tag}: operation removed"))
                continue
            oop = ops[method]
            if oop.get("operationId") != nop.get("operationId"):
                issues.append(
                    (
                        "BREAKING",
                        f"OPERATION_ID_CHANGED {tag}",
                        f"{tag}: operationId {oop.get('operationId')} -> {nop.get('operationId')} (renames generated client methods)",
                    )
                )
            oparams = {(p["in"], p["name"]): p for p in (_resolve(base, x) for x in oop.get("parameters", []))}
            nparams = {(p["in"], p["name"]): p for p in (_resolve(new, x) for x in nop.get("parameters", []))}
            for k, p in nparams.items():
                if k not in oparams and p.get("required"):
                    issues.append(
                        ("BREAKING", f"REQ_PARAM_ADDED {tag} {k[1]}", f"{tag}: new required {k[0]} parameter '{k[1]}'")
                    )
                elif k in oparams:
                    if p.get("required") and not oparams[k].get("required"):
                        issues.append(
                            (
                                "BREAKING",
                                f"REQ_PARAM_REQUIRED {tag} {k[1]}",
                                f"{tag}: parameter '{k[1]}' became required",
                            )
                        )
                    _compare_schema(
                        base,
                        oparams[k].get("schema", {}),
                        new,
                        p.get("schema", {}),
                        f"{tag} param {k[1]}",
                        "request",
                        issues,
                    )
            obody, nbody = oop.get("requestBody"), nop.get("requestBody")
            if nbody and _resolve(new, nbody).get("required") and not (obody and _resolve(base, obody).get("required")):
                issues.append(("BREAKING", f"REQ_BODY_REQUIRED {tag}", f"{tag}: request body became required"))
            if obody and nbody:
                _compare_schema(
                    base, _body_schema(base, obody), new, _body_schema(new, nbody), f"{tag} body", "request", issues
                )
            for code, oresp in oop.get("responses", {}).items():
                nresp = nop.get("responses", {}).get(code)
                if nresp is None:
                    if code.startswith("2"):
                        issues.append(
                            ("BREAKING", f"RESP_STATUS_REMOVED {tag} {code}", f"{tag}: response {code} removed")
                        )
                    continue
                if code.startswith("2"):
                    _compare_schema(
                        base,
                        _body_schema(base, oresp),
                        new,
                        _body_schema(new, nresp),
                        f"{tag} {code}",
                        "response",
                        issues,
                    )
    return issues


def _load(spec: str) -> dict[str, Any] | None:
    if spec.startswith("git:"):
        res = subprocess.run(["git", "show", f"{spec[4:]}:{CONTRACT}"], capture_output=True, text=True, check=False)  # noqa: S603, S607
        if res.returncode != 0:
            return None
        return json.loads(res.stdout)
    return json.loads(Path(spec).read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    allow: set[str] = set()
    args = [a for a in argv if not a.startswith("--allow")]
    if "--allow" in argv:
        path = Path(argv[argv.index("--allow") + 1])
        args = [a for a in args if a != str(path)]
        if path.exists():
            allow = {
                ln.strip()
                for ln in path.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")
            }
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    base, new = _load(args[0]), _load(args[1])
    if base is None:
        print(f"no baseline contract at {args[0]}; nothing to compare (first publication)")
        return 0
    if new is None:
        print(f"cannot read {args[1]}", file=sys.stderr)
        return 2
    issues = diff(base, new)
    breaking = [i for i in issues if i[0] == "BREAKING" and i[1] not in allow]
    for sev, key, msg in issues:
        state = " (allowed)" if key in allow else ""
        print(f"{sev}{state}: {msg}\n    key: {key}")
    print(
        f"{len([i for i in issues if i[0] == 'BREAKING'])} breaking ({len(breaking)} not allow-listed), "
        f"{len([i for i in issues if i[0] == 'WARNING'])} warning(s)"
    )
    return 1 if breaking else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
