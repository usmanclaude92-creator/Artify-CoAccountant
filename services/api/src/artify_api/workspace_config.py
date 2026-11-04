"""Workspace configuration entities: categories, custom fields, naming rules, filing rules and permission grants."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from artify_api.errors import Conflict, NotFound, Unprocessable
from artify_api.ids import new_id
from artify_api.workspace import Scope, _j, bad, get_node, row_dict
from artify_api.workspace_engine import (
    KIND_BY_KEY,
    ConfigError,
    FieldDef,
    RuleCondition,
    RuleDef,
    normalize_file_types,
    parse_pattern,
    slugify_key,
    unique_key,
    validate_conditions,
    validate_field_definition,
)


def _get(conn: Connection, table: str, company_id: uuid.UUID, row_id: uuid.UUID, what: str) -> dict[str, Any]:
    row = conn.execute(
        text(f"SELECT * FROM {table} WHERE company_id = :c AND id = :i"), {"c": company_id, "i": row_id}
    ).first()
    if row is None:
        raise NotFound(f"{what} not found.")
    return row_dict(row)


def _brief(row: dict[str, Any], drop: tuple[str, ...] = ("organization_id", "company_id")) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in drop}


def _unique_violation(exc: IntegrityError, message: str) -> Conflict:
    return Conflict(message)


# ---------------------------------------------------------------- categories
CATEGORY_COLS = (
    "id, key, name, description, document_kind, accounting_domain, processing_type, allowed_file_types, processing_settings,"
    " node_id, is_active, archived_at, created_at, updated_at"
)


def _check_mapping(kind_key: str, domain: str | None, ptype: str | None) -> tuple[str, str]:
    kind = KIND_BY_KEY.get(kind_key)
    if kind is None:
        raise Unprocessable(f"'{kind_key}' is not a document kind. Choose one from the Artify catalog.")
    if domain is not None and domain != kind.accounting_domain:
        raise Unprocessable(
            f"The accounting domain of '{kind.name}' is '{kind.accounting_domain}' and cannot be changed."
        )
    if ptype is not None and ptype not in (kind.processing_type, "reference_only"):
        raise Unprocessable(f"'{kind.name}' can be processed as '{kind.processing_type}' or stored as reference only.")
    return kind.accounting_domain, ptype or kind.processing_type


def _live_node(conn: Connection, company_id: uuid.UUID, node_id: uuid.UUID) -> None:
    n = get_node(conn, company_id, node_id)
    if n["archived_at"] is not None or not n["is_active"]:
        raise Conflict("The chosen folder is archived or inactive.")


def create_category(sc: Scope, d: dict[str, Any]) -> dict[str, Any]:
    domain, ptype = _check_mapping(d["document_kind"], d.get("accounting_domain"), d.get("processing_type"))
    if d.get("node_id"):
        _live_node(sc.conn, sc.company_id, d["node_id"])
    try:
        types = normalize_file_types(d.get("allowed_file_types") or [])
    except ConfigError as exc:
        raise bad(exc) from exc
    keys = [
        r[0]
        for r in sc.conn.execute(
            text("SELECT key FROM document_categories WHERE company_id = :c"), {"c": sc.company_id}
        )
    ]
    cid = new_id()
    try:
        with sc.conn.begin_nested():
            sc.conn.execute(
                text(
                    "INSERT INTO document_categories (id, organization_id, company_id, key, name, description, document_kind,"
                    " accounting_domain, processing_type, allowed_file_types, processing_settings, node_id, is_active, created_by, updated_by)"
                    " VALUES (:id, :o, :c, :k, :n, :d, :dk, :ad, :pt, :ft, CAST(:ps AS jsonb), :node, :act, :u, :u)"
                ),
                {
                    "id": cid,
                    "o": sc.org_id,
                    "c": sc.company_id,
                    "k": unique_key(slugify_key(d["name"], "category"), keys),
                    "n": d["name"].strip(),
                    "d": d.get("description", ""),
                    "dk": d["document_kind"],
                    "ad": domain,
                    "pt": ptype,
                    "ft": types,
                    "ps": _j(d.get("processing_settings") or {}),
                    "node": d.get("node_id"),
                    "act": d.get("is_active", True),
                    "u": sc.actor_id,
                },
            )
    except IntegrityError as exc:
        raise Conflict(f"A category named '{d['name']}' already exists.") from exc
    row = _get(sc.conn, "document_categories", sc.company_id, cid, "Category")
    sc.record("category.created", "category", cid, None, _brief(row))
    return row


def update_category(sc: Scope, category_id: uuid.UUID, d: dict[str, Any]) -> dict[str, Any]:
    cur = _get(sc.conn, "document_categories", sc.company_id, category_id, "Category")
    if cur["archived_at"] is not None:
        raise Conflict("Restore this category before changing it.")
    kind = d.get("document_kind", cur["document_kind"])
    domain, ptype = _check_mapping(kind, None, d.get("processing_type"))
    sets: dict[str, Any] = {"document_kind": kind, "accounting_domain": domain, "processing_type": ptype}
    for k in ("name", "description", "processing_settings", "is_active"):
        if k in d and d[k] is not None:
            sets[k] = d[k].strip() if k == "name" else d[k]
    if "allowed_file_types" in d and d["allowed_file_types"] is not None:
        try:
            sets["allowed_file_types"] = normalize_file_types(d["allowed_file_types"])
        except ConfigError as exc:
            raise bad(exc) from exc
    if "node_id" in d:
        if d["node_id"]:
            _live_node(sc.conn, sc.company_id, d["node_id"])
        sets["node_id"] = d["node_id"]
    _apply(sc, "document_categories", category_id, sets, {"processing_settings"})
    row = _get(sc.conn, "document_categories", sc.company_id, category_id, "Category")
    sc.record("category.updated", "category", category_id, _brief(cur), _brief(row))
    return row


def _apply(sc: Scope, table: str, row_id: uuid.UUID, sets: dict[str, Any], json_cols: set[str] = frozenset()) -> None:  # type: ignore[assignment]
    assigns = ", ".join(f"{k} = CAST(:{k} AS jsonb)" if k in json_cols else f"{k} = :{k}" for k in sets)
    params = {k: (_j(v) if k in json_cols else v) for k, v in sets.items()}
    try:
        with sc.conn.begin_nested():
            sc.conn.execute(
                text(
                    f"UPDATE {table} SET {assigns}, updated_by = :_u, updated_at = now() WHERE id = :_i AND company_id = :_c"
                ),
                {**params, "_u": sc.actor_id, "_i": row_id, "_c": sc.company_id},
            )
    except IntegrityError as exc:
        raise Conflict("That change conflicts with an existing item (for example a duplicate name).") from exc


def set_archived(sc: Scope, table: str, entity: str, row_id: uuid.UUID, archived: bool) -> dict[str, Any]:
    cur = _get(sc.conn, table, sc.company_id, row_id, entity.title())
    if archived == (cur["archived_at"] is not None):
        raise Conflict(f"This {entity} is already {'archived' if archived else 'active'}.")
    if archived:
        sc.conn.execute(
            text(
                f"UPDATE {table} SET archived_at = now(), updated_at = now(), updated_by = :u WHERE id = :i AND company_id = :c"
            ),
            {"u": sc.actor_id, "i": row_id, "c": sc.company_id},
        )
    else:
        _apply(sc, table, row_id, {"archived_at": None})
    row = _get(sc.conn, table, sc.company_id, row_id, entity.title())
    sc.record(
        f"{entity}.{'archived' if archived else 'restored'}",
        entity,
        row_id,
        {"archived_at": cur["archived_at"]},
        {"archived_at": row["archived_at"]},
    )
    return row


# ---------------------------------------------------------------- custom fields
def field_with_children(conn: Connection, company_id: uuid.UUID, row: dict[str, Any]) -> dict[str, Any]:
    opts = [
        row_dict(r)
        for r in conn.execute(
            text(
                "SELECT id, value, label, position, archived_at FROM custom_field_options WHERE field_id = :f ORDER BY position, id"
            ),
            {"f": row["id"]},
        )
    ]
    app = [
        row_dict(r)
        for r in conn.execute(
            text("SELECT category_id, node_id FROM custom_field_applicability WHERE field_id = :f"), {"f": row["id"]}
        )
    ]
    return {
        **{k: v for k, v in row.items() if k not in ("organization_id", "company_id")},
        "options": opts,
        "category_ids": [a["category_id"] for a in app if a["category_id"]],
        "node_ids": [a["node_id"] for a in app if a["node_id"]],
    }


def _set_options(sc: Scope, field_id: uuid.UUID, options: list[dict[str, str]]) -> None:
    existing = {
        r.value: r
        for r in sc.conn.execute(
            text("SELECT id, value, archived_at FROM custom_field_options WHERE field_id = :f"), {"f": field_id}
        )
    }
    wanted = {o["value"]: o for o in options}
    for pos, o in enumerate(options):
        if o["value"] in existing:
            sc.conn.execute(
                text("UPDATE custom_field_options SET label = :l, position = :p, archived_at = NULL WHERE id = :i"),
                {"l": o["label"], "p": pos, "i": existing[o["value"]].id},
            )
        else:
            sc.conn.execute(
                text(
                    "INSERT INTO custom_field_options (id, organization_id, company_id, field_id, value, label, position) VALUES (:id, :o, :c, :f, :v, :l, :p)"
                ),
                {
                    "id": new_id(),
                    "o": sc.org_id,
                    "c": sc.company_id,
                    "f": field_id,
                    "v": o["value"],
                    "l": o["label"],
                    "p": pos,
                },
            )
    for value, r in existing.items():  # options are archived, never deleted: stored values stay interpretable
        if value not in wanted and r.archived_at is None:
            sc.conn.execute(text("UPDATE custom_field_options SET archived_at = now() WHERE id = :i"), {"i": r.id})


def _set_applicability(
    sc: Scope, field_id: uuid.UUID, category_ids: list[uuid.UUID], node_ids: list[uuid.UUID]
) -> None:
    for cid in category_ids:
        _get(sc.conn, "document_categories", sc.company_id, cid, "Category")
    for nid in node_ids:
        get_node(sc.conn, sc.company_id, nid)
    sc.conn.execute(text("DELETE FROM custom_field_applicability WHERE field_id = :f"), {"f": field_id})
    for cid in dict.fromkeys(category_ids):
        sc.conn.execute(
            text(
                "INSERT INTO custom_field_applicability (id, organization_id, company_id, field_id, category_id) VALUES (:id, :o, :c, :f, :x)"
            ),
            {"id": new_id(), "o": sc.org_id, "c": sc.company_id, "f": field_id, "x": cid},
        )
    for nid in dict.fromkeys(node_ids):
        sc.conn.execute(
            text(
                "INSERT INTO custom_field_applicability (id, organization_id, company_id, field_id, node_id) VALUES (:id, :o, :c, :f, :x)"
            ),
            {"id": new_id(), "o": sc.org_id, "c": sc.company_id, "f": field_id, "x": nid},
        )


def _check_field(d: dict[str, Any], option_values: list[str]) -> None:
    try:
        validate_field_definition(
            d["data_type"], d.get("validation") or {}, d.get("default_value"), option_values, d.get("reference_target")
        )
    except ConfigError as exc:
        raise bad(exc) from exc


def create_field(sc: Scope, d: dict[str, Any]) -> dict[str, Any]:
    options = d.get("options") or []
    _check_field(d, [o["value"] for o in options])
    keys = [
        r[0]
        for r in sc.conn.execute(
            text("SELECT key FROM custom_field_definitions WHERE company_id = :c"), {"c": sc.company_id}
        )
    ]
    key = d.get("key") or unique_key(slugify_key(d["label"], "field").replace("-", "_"), keys, "_")
    if key in keys:
        raise Conflict(f"A field with the internal key '{key}' already exists.")
    fid = new_id()
    order = sc.conn.execute(
        text("SELECT COALESCE(max(display_order), 0) + 10 FROM custom_field_definitions WHERE company_id = :c"),
        {"c": sc.company_id},
    ).scalar_one()
    try:
        with sc.conn.begin_nested():
            sc.conn.execute(
                text(
                    "INSERT INTO custom_field_definitions (id, organization_id, company_id, key, label, description, data_type, required,"
                    " default_value, validation, reference_target, display_order, is_active, created_by, updated_by)"
                    " VALUES (:id, :o, :c, :k, :l, :d, :t, :r, CAST(:dv AS jsonb), CAST(:v AS jsonb), :rt, :ord, :act, :u, :u)"
                ),
                {
                    "id": fid,
                    "o": sc.org_id,
                    "c": sc.company_id,
                    "k": key,
                    "l": d["label"].strip(),
                    "d": d.get("description", ""),
                    "t": d["data_type"],
                    "r": d.get("required", False),
                    "dv": None if d.get("default_value") is None else _j(d["default_value"]),
                    "v": _j(d.get("validation") or {}),
                    "rt": d.get("reference_target"),
                    "ord": d.get("display_order") or int(order),
                    "act": d.get("is_active", True),
                    "u": sc.actor_id,
                },
            )
    except IntegrityError as exc:
        raise Conflict(f"A field labelled '{d['label']}' already exists.") from exc
    _set_options(sc, fid, options)
    _set_applicability(sc, fid, d.get("category_ids") or [], d.get("node_ids") or [])
    out = field_with_children(
        sc.conn, sc.company_id, _get(sc.conn, "custom_field_definitions", sc.company_id, fid, "Field")
    )
    sc.record("field.created", "field", fid, None, out)
    return out


def update_field(sc: Scope, field_id: uuid.UUID, d: dict[str, Any]) -> dict[str, Any]:
    """key and data_type are immutable (stored values keep their meaning). Everything else may change; options that
    are removed are archived, never deleted."""
    cur = _get(sc.conn, "custom_field_definitions", sc.company_id, field_id, "Field")
    if cur["archived_at"] is not None:
        raise Conflict("Restore this field before changing it.")
    before = field_with_children(sc.conn, sc.company_id, cur)
    options = d.get("options")
    option_values = (
        [o["value"] for o in options]
        if options is not None
        else [o["value"] for o in before["options"] if o["archived_at"] is None]
    )
    merged = {
        "data_type": cur["data_type"],
        "validation": d.get("validation", cur["validation"]),
        "default_value": d.get("default_value", cur["default_value"]) if "default_value" in d else cur["default_value"],
        "reference_target": cur["reference_target"],
    }
    _check_field(merged, option_values)
    sets: dict[str, Any] = {}
    for k in ("label", "description", "required", "display_order", "is_active", "validation"):
        if k in d and d[k] is not None:
            sets[k] = d[k].strip() if k == "label" else d[k]
    if "default_value" in d:
        sets["default_value"] = d["default_value"]
    if sets:
        assigns, params = [], {}
        for k, v in sets.items():
            if k in ("validation", "default_value"):
                assigns.append(f"{k} = CAST(:{k} AS jsonb)")
                params[k] = None if v is None else _j(v)
            else:
                assigns.append(f"{k} = :{k}")
                params[k] = v
        try:
            with sc.conn.begin_nested():
                sc.conn.execute(
                    text(
                        f"UPDATE custom_field_definitions SET {', '.join(assigns)}, updated_by = :_u, updated_at = now() WHERE id = :_i AND company_id = :_c"
                    ),
                    {**params, "_u": sc.actor_id, "_i": field_id, "_c": sc.company_id},
                )
        except IntegrityError as exc:
            raise Conflict("Another field already uses that label.") from exc
    if options is not None:
        _set_options(sc, field_id, options)
    if d.get("category_ids") is not None or d.get("node_ids") is not None:
        cats: list[uuid.UUID] = d["category_ids"] if d.get("category_ids") is not None else before["category_ids"]
        nds: list[uuid.UUID] = d["node_ids"] if d.get("node_ids") is not None else before["node_ids"]
        _set_applicability(sc, field_id, cats, nds)
    after = field_with_children(
        sc.conn, sc.company_id, _get(sc.conn, "custom_field_definitions", sc.company_id, field_id, "Field")
    )
    sc.record("field.updated", "field", field_id, before, after)
    return after


def applicable_fields(
    conn: Connection, company_id: uuid.UUID, category_id: uuid.UUID | None, node_chain: list[str]
) -> list[dict[str, Any]]:
    """Active fields that apply: a field with no applicability rows applies everywhere; otherwise only to the listed
    categories/folders (a folder scope covers its subfolders)."""
    rows = conn.execute(
        text(
            "SELECT f.* FROM custom_field_definitions f WHERE f.company_id = :c AND f.archived_at IS NULL AND f.is_active AND ("
            " NOT EXISTS (SELECT 1 FROM custom_field_applicability a WHERE a.field_id = f.id)"
            " OR EXISTS (SELECT 1 FROM custom_field_applicability a WHERE a.field_id = f.id AND"
            "   (a.category_id = CAST(:cat AS uuid) OR a.node_id = ANY(CAST(:chain AS uuid[]))))) ORDER BY f.display_order, f.created_at"
        ),
        {"c": company_id, "cat": category_id, "chain": "{" + ",".join(node_chain) + "}"},
    )
    return [field_with_children(conn, company_id, row_dict(r)) for r in rows]


def to_field_def(f: dict[str, Any]) -> FieldDef:
    return FieldDef(
        key=f["key"],
        label=f["label"],
        data_type=f["data_type"],
        required=f["required"],
        validation=f["validation"],
        default_value=f["default_value"],
        reference_target=f["reference_target"],
        option_values=frozenset(o["value"] for o in f["options"] if o["archived_at"] is None),
    )


def ref_exists_fn(conn: Connection, company_id: uuid.UUID):  # type: ignore[no-untyped-def]
    def exists(target: str, ref: uuid.UUID) -> bool:
        table = {"workspace_node": "workspace_nodes", "document_category": "document_categories"}[target]
        return (
            conn.execute(
                text(f"SELECT 1 FROM {table} WHERE company_id = :c AND id = :i AND archived_at IS NULL"),
                {"c": company_id, "i": ref},
            ).first()
            is not None
        )

    return exists


# ---------------------------------------------------------------- naming rules
def active_field_keys(conn: Connection, company_id: uuid.UUID) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            text(
                "SELECT key FROM custom_field_definitions WHERE company_id = :c AND archived_at IS NULL AND is_active"
            ),
            {"c": company_id},
        )
    ]


def check_pattern(conn: Connection, company_id: uuid.UUID, pattern: str) -> None:
    try:
        parse_pattern(pattern, active_field_keys(conn, company_id))
    except ConfigError as exc:
        raise bad(exc) from exc


def create_naming_rule(sc: Scope, d: dict[str, Any]) -> dict[str, Any]:
    check_pattern(sc.conn, sc.company_id, d["pattern"])
    if d.get("category_id"):
        _get(sc.conn, "document_categories", sc.company_id, d["category_id"], "Category")
    rid = new_id()
    try:
        with sc.conn.begin_nested():
            sc.conn.execute(
                text(
                    "INSERT INTO naming_rules (id, organization_id, company_id, name, description, pattern, category_id, is_active, created_by, updated_by)"
                    " VALUES (:id, :o, :c, :n, :d, :p, :cat, :a, :u, :u)"
                ),
                {
                    "id": rid,
                    "o": sc.org_id,
                    "c": sc.company_id,
                    "n": d["name"].strip(),
                    "d": d.get("description", ""),
                    "p": d["pattern"],
                    "cat": d.get("category_id"),
                    "a": d.get("is_active", True),
                    "u": sc.actor_id,
                },
            )
    except IntegrityError as exc:
        raise Conflict("There is already an active naming rule for this category (or a company-wide default).") from exc
    row = _get(sc.conn, "naming_rules", sc.company_id, rid, "Naming rule")
    sc.record("naming_rule.created", "naming_rule", rid, None, _brief(row))
    return row


def update_naming_rule(sc: Scope, rule_id: uuid.UUID, d: dict[str, Any]) -> dict[str, Any]:
    cur = _get(sc.conn, "naming_rules", sc.company_id, rule_id, "Naming rule")
    if cur["archived_at"] is not None:
        raise Conflict("Restore this rule before changing it.")
    if d.get("pattern"):
        check_pattern(sc.conn, sc.company_id, d["pattern"])
    sets = {
        k: (d[k].strip() if k == "name" else d[k])
        for k in ("name", "description", "pattern", "is_active")
        if d.get(k) is not None
    }
    if "category_id" in d:
        if d["category_id"]:
            _get(sc.conn, "document_categories", sc.company_id, d["category_id"], "Category")
        sets["category_id"] = d["category_id"]
    if sets:
        _apply(sc, "naming_rules", rule_id, sets)
    row = _get(sc.conn, "naming_rules", sc.company_id, rule_id, "Naming rule")
    sc.record("naming_rule.updated", "naming_rule", rule_id, _brief(cur), _brief(row))
    return row


# ---------------------------------------------------------------- filing rules
def category_ids(conn: Connection, company_id: uuid.UUID) -> list[str]:
    return [
        str(r[0])
        for r in conn.execute(
            text("SELECT id FROM document_categories WHERE company_id = :c AND archived_at IS NULL"), {"c": company_id}
        )
    ]


def check_filing(
    conn: Connection,
    company_id: uuid.UUID,
    conditions: list[dict[str, Any]],
    target_node_id: uuid.UUID,
    target_category_id: uuid.UUID | None,
) -> list[RuleCondition]:
    try:
        conds = validate_conditions(conditions, active_field_keys(conn, company_id), category_ids(conn, company_id))
    except ConfigError as exc:
        raise bad(exc) from exc
    node = get_node(conn, company_id, target_node_id)
    if node["archived_at"] is not None or not node["is_active"]:
        raise Conflict("The target folder is archived or inactive, so nothing can be filed there.")
    if target_category_id:
        cat = _get(conn, "document_categories", company_id, target_category_id, "Category")
        if cat["archived_at"] is not None:
            raise Conflict("The target category is archived.")
    return conds


def create_filing_rule(sc: Scope, d: dict[str, Any]) -> dict[str, Any]:
    check_filing(sc.conn, sc.company_id, d["conditions"], d["target_node_id"], d.get("target_category_id"))
    rid = new_id()
    prio = d.get("priority") or int(
        sc.conn.execute(
            text(
                "SELECT COALESCE(max(priority), 0) + 10 FROM filing_rules WHERE company_id = :c AND archived_at IS NULL"
            ),
            {"c": sc.company_id},
        ).scalar_one()
    )
    sc.conn.execute(
        text(
            "INSERT INTO filing_rules (id, organization_id, company_id, name, description, priority, conditions, target_node_id, target_category_id, is_active, created_by, updated_by)"
            " VALUES (:id, :o, :c, :n, :d, :p, CAST(:cond AS jsonb), :t, :tc, :a, :u, :u)"
        ),
        {
            "id": rid,
            "o": sc.org_id,
            "c": sc.company_id,
            "n": d["name"].strip(),
            "d": d.get("description", ""),
            "p": prio,
            "cond": _j(d["conditions"]),
            "t": d["target_node_id"],
            "tc": d.get("target_category_id"),
            "a": d.get("is_active", True),
            "u": sc.actor_id,
        },
    )
    row = _get(sc.conn, "filing_rules", sc.company_id, rid, "Filing rule")
    sc.record("filing_rule.created", "filing_rule", rid, None, _brief(row))
    return row


def update_filing_rule(sc: Scope, rule_id: uuid.UUID, d: dict[str, Any]) -> dict[str, Any]:
    cur = _get(sc.conn, "filing_rules", sc.company_id, rule_id, "Filing rule")
    if cur["archived_at"] is not None:
        raise Conflict("Restore this rule before changing it.")
    conds: list[dict[str, Any]] = d["conditions"] if d.get("conditions") is not None else cur["conditions"]
    target = d.get("target_node_id") or uuid.UUID(cur["target_node_id"])
    tcat = (
        d["target_category_id"]
        if "target_category_id" in d
        else (uuid.UUID(cur["target_category_id"]) if cur["target_category_id"] else None)
    )
    if d.get("is_active") is False:
        pass  # deactivating never needs a valid target
    else:
        check_filing(sc.conn, sc.company_id, conds, target, tcat)
    sets: dict[str, Any] = {
        k: (d[k].strip() if k == "name" else d[k])
        for k in ("name", "description", "priority", "is_active")
        if d.get(k) is not None
    }
    if d.get("conditions") is not None:
        sets["conditions"] = d["conditions"]
    sets["target_node_id"], sets["target_category_id"] = target, tcat
    _apply(sc, "filing_rules", rule_id, sets, {"conditions"})
    row = _get(sc.conn, "filing_rules", sc.company_id, rule_id, "Filing rule")
    sc.record("filing_rule.updated", "filing_rule", rule_id, _brief(cur), _brief(row))
    return row


def reorder_filing_rules(sc: Scope, ordered: list[uuid.UUID]) -> None:
    live = [
        str(r[0])
        for r in sc.conn.execute(
            text("SELECT id FROM filing_rules WHERE company_id = :c AND archived_at IS NULL"), {"c": sc.company_id}
        )
    ]
    wanted = [str(i) for i in ordered]
    if sorted(wanted) != sorted(live) or len(set(wanted)) != len(wanted):
        raise Unprocessable("List every active filing rule exactly once.", extra={"expected_ids": live})
    for i, rid in enumerate(wanted, 1):
        sc.conn.execute(
            text(
                "UPDATE filing_rules SET priority = :p, updated_at = now(), updated_by = :u WHERE id = :i AND company_id = :c"
            ),
            {"p": i * 10, "u": sc.actor_id, "i": rid, "c": sc.company_id},
        )
    sc.record("filing_rule.reordered", "filing_rule", "all", None, {"order": wanted})


def rule_defs(conn: Connection, company_id: uuid.UUID, *, only_live_targets: bool = True) -> list[RuleDef]:
    rows = conn.execute(
        text(
            "SELECT r.* FROM filing_rules r JOIN workspace_nodes n ON n.id = r.target_node_id WHERE r.company_id = :c AND r.archived_at IS NULL AND r.is_active"
            + (" AND n.archived_at IS NULL AND n.is_active" if only_live_targets else "")
        ),
        {"c": company_id},
    )
    out = []
    for r in map(row_dict, rows):
        out.append(
            RuleDef(
                uuid.UUID(r["id"]),
                r["name"],
                r["priority"],
                r["created_at"],
                tuple(RuleCondition(c["field"], c["operator"], c.get("value")) for c in r["conditions"]),
                uuid.UUID(r["target_node_id"]),
                uuid.UUID(r["target_category_id"]) if r["target_category_id"] else None,
            )
        )
    return out


# ---------------------------------------------------------------- permission grants
def set_grant(
    sc: Scope,
    node_id: uuid.UUID | None,
    subject_type: str,
    user_id: uuid.UUID | None,
    role: str | None,
    permission: str,
    effect: str,
) -> dict[str, Any]:
    from artify_api.permissions import SYSTEM_ROLES

    if node_id:
        get_node(sc.conn, sc.company_id, node_id)
    admin_roles = {
        k
        for k, (scope, _n, perms) in SYSTEM_ROLES.items()
        if scope == "organization" and "workspace.permissions.manage" in perms
    }
    subject_role = role
    if subject_type == "user":
        row = sc.conn.execute(
            text(
                "SELECT r.key FROM organization_memberships m JOIN roles r ON r.id = m.role_id WHERE m.organization_id = :o AND m.user_id = :u AND m.status = 'active'"
            ),
            {"o": sc.org_id, "u": user_id},
        ).first()
        if row is None:
            raise Unprocessable("Grants can only be given to active members of this organization.")
        subject_role = row.key
    elif role not in SYSTEM_ROLES or SYSTEM_ROLES[role][0] != "organization":
        raise Unprocessable("Unknown organization role.")
    if subject_role in admin_roles:
        raise Unprocessable(
            "Owners and administrators always have full workspace access; grants cannot restrict or extend them."
        )
    if subject_role == "read_only" and permission != "view":
        raise Unprocessable("Read-only members can only be given 'view'.")
    gid = new_id()
    sc.conn.execute(
        text(
            "DELETE FROM workspace_permissions WHERE company_id = :c AND node_id IS NOT DISTINCT FROM CAST(:n AS uuid) AND subject_type = :t"
            " AND subject_user_id IS NOT DISTINCT FROM CAST(:u AS uuid) AND subject_role IS NOT DISTINCT FROM CAST(:r AS text) AND permission = :p"
        ),
        {
            "c": sc.company_id,
            "n": node_id,
            "t": subject_type,
            "u": user_id,
            "r": role if subject_type == "role" else None,
            "p": permission,
        },
    )
    sc.conn.execute(
        text(
            "INSERT INTO workspace_permissions (id, organization_id, company_id, node_id, subject_type, subject_user_id, subject_role, permission, effect, created_by)"
            " VALUES (:id, :o, :c, :n, :t, :u, :r, :p, :e, :cb)"
        ),
        {
            "id": gid,
            "o": sc.org_id,
            "c": sc.company_id,
            "n": node_id,
            "t": subject_type,
            "u": user_id,
            "r": role if subject_type == "role" else None,
            "p": permission,
            "e": effect,
            "cb": sc.actor_id,
        },
    )
    grant = _get(sc.conn, "workspace_permissions", sc.company_id, gid, "Grant")
    sc.record("permission.set", "permission", gid, None, _brief(grant))
    return grant


def remove_grant(sc: Scope, grant_id: uuid.UUID) -> None:
    row = _get(sc.conn, "workspace_permissions", sc.company_id, grant_id, "Grant")
    sc.conn.execute(
        text("DELETE FROM workspace_permissions WHERE id = :i AND company_id = :c"), {"i": grant_id, "c": sc.company_id}
    )
    sc.record("permission.removed", "permission", grant_id, _brief(row), None)
