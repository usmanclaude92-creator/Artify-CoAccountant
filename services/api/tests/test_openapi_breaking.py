"""Unit tests for scripts/check_openapi_breaking.py (synthetic contracts; no database)."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_openapi_breaking.py"
spec = importlib.util.spec_from_file_location("check_openapi_breaking", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

BASE = {
    "paths": {
        "/items": {
            "post": {
                "operationId": "items_create",
                "parameters": [{"name": "q", "in": "query", "required": False, "schema": {"type": "string"}}],
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/In"}}},
                },
                "responses": {
                    "201": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Out"}}}}
                },
            },
            "get": {
                "operationId": "items_list",
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {"type": "array", "items": {"$ref": "#/components/schemas/Out"}}
                            }
                        }
                    }
                },
            },
        }
    },
    "components": {
        "schemas": {
            "In": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "maxLength": 50},
                    "kind": {"type": "string", "enum": ["a", "b", "c"]},
                    "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
            },
            "Out": {
                "type": "object",
                "required": ["id", "name"],
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "status": {"type": "string", "enum": ["x", "y"]},
                },
            },
        }
    },
}


def keys(new: dict) -> set[str]:
    return {k for _, k, _ in mod.diff(BASE, new)}


def mutate(fn):  # type: ignore[no-untyped-def]
    doc = copy.deepcopy(BASE)
    fn(doc)
    return doc


def test_identical_contract_has_no_issues() -> None:
    assert mod.diff(BASE, copy.deepcopy(BASE)) == []


def test_compatible_changes_are_not_flagged() -> None:
    def change(d):  # type: ignore[no-untyped-def]
        d["components"]["schemas"]["In"]["properties"]["extra"] = {"type": "string"}  # new optional request field
        d["components"]["schemas"]["Out"]["properties"]["more"] = {"type": "integer"}  # new response field
        d["components"]["schemas"]["In"]["properties"]["name"]["maxLength"] = 100  # limit relaxed
        d["components"]["schemas"]["In"]["properties"]["kind"]["enum"].append("d")  # request enum widened
        d["paths"]["/new"] = {"get": {"operationId": "new_get", "responses": {"200": {}}}}  # new operation

    assert mod.diff(BASE, mutate(change)) == []


def test_breaking_request_changes_are_detected() -> None:
    def change(d):  # type: ignore[no-untyped-def]
        s = d["components"]["schemas"]["In"]
        s["required"].append("kind")  # property became required
        s["properties"]["new_req"] = {"type": "string"}
        s["required"].append("new_req")  # new required property
        s["properties"]["name"]["maxLength"] = 10  # tightened
        s["properties"]["kind"]["enum"].remove("c")  # enum value removed
        s["properties"]["note"] = {"type": "string"}  # null no longer accepted
        d["paths"]["/items"]["post"]["parameters"].append(
            {"name": "must", "in": "query", "required": True, "schema": {"type": "string"}}
        )

    got = keys(mutate(change))
    for prefix in (
        "REQ_REQUIRED_ADDED",
        "REQ_LIMIT_TIGHTENED",
        "REQ_ENUM_REMOVED",
        "REQ_TYPE_NARROWED",
        "REQ_PARAM_ADDED",
    ):
        assert any(k.startswith(prefix) for k in got), (prefix, got)


def test_breaking_response_changes_are_detected() -> None:
    def change(d):  # type: ignore[no-untyped-def]
        o = d["components"]["schemas"]["Out"]
        del o["properties"]["name"]  # property removed
        o["required"].remove("id")  # no longer always present
        o["properties"]["status"]["enum"].append("z")  # enum grew (warning)
        d["paths"]["/items"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["items"] = {
            "type": "string"
        }

    issues = mod.diff(BASE, mutate(change))
    sev = {k: s for s, k, _ in issues}
    assert any(k.startswith("RESP_PROPERTY_REMOVED") for k in sev)
    assert any(k.startswith("RESP_REQUIRED_DROPPED") for k in sev)
    assert any(k.startswith("RESP_TYPE_CHANGED") for k in sev)
    assert [s for s, k, _ in issues if k.startswith("RESP_ENUM_ADDED")] == ["WARNING"]


def test_operation_removed_renamed_and_status_removed() -> None:
    removed = mutate(lambda d: d["paths"]["/items"].pop("get"))
    assert "OP_REMOVED GET /items" in keys(removed)
    renamed = mutate(lambda d: d["paths"]["/items"]["get"].update(operationId="items_all"))
    assert "OPERATION_ID_CHANGED GET /items" in keys(renamed)
    gone = mutate(lambda d: d["paths"]["/items"]["post"]["responses"].pop("201"))
    assert "RESP_STATUS_REMOVED POST /items 201" in keys(gone)
    body = mutate(lambda d: d["paths"]["/items"]["post"]["requestBody"].update(required=True))
    assert "REQ_BODY_REQUIRED" not in "".join(keys(body))  # was already required: no change


def test_allow_list_suppresses_known_breaks(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import json

    new = mutate(lambda d: d["paths"]["/items"].pop("get"))
    base_f, new_f, allow_f = tmp_path / "b.json", tmp_path / "n.json", tmp_path / "allow.txt"
    base_f.write_text(json.dumps(BASE))
    new_f.write_text(json.dumps(new))
    assert mod.main([str(base_f), str(new_f)]) == 1
    allow_f.write_text("# accepted: endpoint replaced in v0\nOP_REMOVED GET /items\n")
    assert mod.main([str(base_f), str(new_f), "--allow", str(allow_f)]) == 0


def test_missing_baseline_is_not_an_error(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert (
        mod.main(
            [
                "git:definitely-not-a-ref-xyz",
                str(SCRIPT.parent.parent.parent.parent / "packages/api-client/openapi.json"),
            ]
        )
        == 0
    )
