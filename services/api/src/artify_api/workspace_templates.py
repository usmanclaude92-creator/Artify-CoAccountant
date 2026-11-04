"""Workspace templates (copy-in, never linked), export of a company's configuration, and revision restore."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from artify_api import entitlements
from artify_api.errors import Conflict, NotFound, Unprocessable
from artify_api.ids import new_id
from artify_api.workspace import (
    MAX_DEPTH_HARD,
    Scope,
    _insert_node,
    _j,
    _recompute_paths,
    bad,
    fetch_nodes,
    row_dict,
)
from artify_api.workspace_engine import (
    KIND_BY_KEY,
    ConfigError,
    slugify_key,
    unique_key,
    unique_name,
    validate_node_name,
)
from artify_api.workspace_templates_data import (
    CategoryDef,
    FieldDefn,
    FilingDef,
    NamingDef,
    NodeDef,
    OptionDef,
    TemplateDefinition,
)


# ---------------------------------------------------------------- export (company -> definition)
def export_definition(conn: Connection, company_id: uuid.UUID) -> tuple[TemplateDefinition, list[str]]:
    """Configuration only: structure, categories, fields, naming and filing rules. Never documents, permissions, users,
    money or anything personal. Rules that cannot be expressed without company-specific ids are skipped and reported."""
    warnings: list[str] = []
    nodes = list(fetch_nodes(conn, company_id, include_archived=False))
    key_by_id = {n["id"]: n["key"] for n in nodes}
    children: dict[str | None, list[dict[str, Any]]] = {}
    for n in nodes:
        children.setdefault(n["parent_id"], []).append(n)

    def build(parent: str | None) -> list[NodeDef]:
        return [
            NodeDef(ref=n["key"], name=n["name"], description=n["description"], folders=build(n["id"]))
            for n in children.get(parent, [])
        ]

    cats = [
        row_dict(r)
        for r in conn.execute(
            text(
                "SELECT * FROM document_categories WHERE company_id = :c AND archived_at IS NULL ORDER BY created_at, id"
            ),
            {"c": company_id},
        )
    ]
    cat_key = {c["id"]: c["key"] for c in cats}
    fields = [
        row_dict(r)
        for r in conn.execute(
            text(
                "SELECT * FROM custom_field_definitions WHERE company_id = :c AND archived_at IS NULL ORDER BY display_order, created_at"
            ),
            {"c": company_id},
        )
    ]
    f_defs: list[FieldDefn] = []
    for f in fields:
        opts = [
            OptionDef(value=o.value, label=o.label)
            for o in conn.execute(
                text(
                    "SELECT value, label FROM custom_field_options WHERE field_id = :f AND archived_at IS NULL ORDER BY position, id"
                ),
                {"f": f["id"]},
            )
        ]
        app = conn.execute(
            text("SELECT category_id, node_id FROM custom_field_applicability WHERE field_id = :f"), {"f": f["id"]}
        ).all()
        f_defs.append(
            FieldDefn(
                ref=f["key"],
                label=f["label"],
                description=f["description"],
                data_type=f["data_type"],
                required=f["required"],
                default_value=f["default_value"],
                validation=f["validation"],
                reference_target=f["reference_target"],
                options=opts,
                category_refs=[
                    cat_key[str(a.category_id)] for a in app if a.category_id and str(a.category_id) in cat_key
                ],
                node_refs=[key_by_id[str(a.node_id)] for a in app if a.node_id and str(a.node_id) in key_by_id],
            )
        )
    naming = []
    for r in map(
        row_dict,
        conn.execute(
            text("SELECT * FROM naming_rules WHERE company_id = :c AND archived_at IS NULL AND is_active"),
            {"c": company_id},
        ),
    ):
        if r["category_id"] and r["category_id"] not in cat_key:
            continue
        naming.append(
            NamingDef(
                name=r["name"],
                pattern=r["pattern"],
                category_ref=cat_key.get(r["category_id"]) if r["category_id"] else None,
            )
        )
    filing = []
    for r in map(
        row_dict,
        conn.execute(
            text(
                "SELECT * FROM filing_rules WHERE company_id = :c AND archived_at IS NULL AND is_active ORDER BY priority"
            ),
            {"c": company_id},
        ),
    ):
        if any(c["field"] == "category_id" for c in r["conditions"]):
            warnings.append(f"Filing rule '{r['name']}' uses a category condition and was not included.")
            continue
        if r["target_node_id"] not in key_by_id:
            continue
        filing.append(
            FilingDef(
                name=r["name"],
                description=r["description"],
                priority=r["priority"],
                conditions=r["conditions"],
                target_node_ref=key_by_id[r["target_node_id"]],
                target_category_ref=cat_key.get(r["target_category_id"]) if r["target_category_id"] else None,
            )
        )
    cat_defs = [
        CategoryDef(
            ref=c["key"],
            name=c["name"],
            description=c["description"],
            document_kind=c["document_kind"],
            accounting_domain=c["accounting_domain"],
            processing_type=c["processing_type"],
            allowed_file_types=list(c["allowed_file_types"]),
            node_ref=key_by_id.get(c["node_id"]) if c["node_id"] else None,
        )
        for c in cats
    ]
    return TemplateDefinition(
        sections=build(None), categories=cat_defs, fields=f_defs, naming_rules=naming, filing_rules=filing
    ), warnings


# ---------------------------------------------------------------- apply (definition -> company, new ids)
def apply_definition(sc: Scope, definition: TemplateDefinition, mode: str) -> dict[str, Any]:
    """Deep copy with brand-new ids. mode 'add' keeps everything that exists (name/key collisions get suffixes);
    mode 'replace' first ARCHIVES the existing configuration (nothing is deleted, documents stay where they are)."""
    try:
        definition.check()
    except ConfigError as exc:
        raise bad(exc) from exc
    if definition.max_depth() > MAX_DEPTH_HARD:
        raise Unprocessable("The template nests folders too deeply.")
    c = sc.conn
    if mode == "replace":
        for table in (
            "workspace_nodes",
            "document_categories",
            "custom_field_definitions",
            "naming_rules",
            "filing_rules",
        ):
            c.execute(
                text(f"UPDATE {table} SET archived_at = now() WHERE company_id = :c AND archived_at IS NULL"),
                {"c": sc.company_id},
            )
        sc.record("workspace.replaced", "workspace", sc.company_id, {"mode": "replace"}, None)
    node_keys = [
        r[0] for r in c.execute(text("SELECT key FROM workspace_nodes WHERE company_id = :c"), {"c": sc.company_id})
    ]
    nodes: dict[str, dict[str, Any]] = {}
    created = {"sections": 0, "folders": 0, "categories": 0, "fields": 0, "naming_rules": 0, "filing_rules": 0}
    skipped: list[str] = []

    def add_nodes(defs: list[NodeDef], parent: dict[str, Any] | None) -> None:
        siblings = [
            r[0]
            for r in c.execute(
                text(
                    "SELECT lower(name) FROM workspace_nodes WHERE company_id = :c AND archived_at IS NULL AND parent_id IS NOT DISTINCT FROM CAST(:p AS uuid)"
                ),
                {"c": sc.company_id, "p": parent["id"] if parent else None},
            )
        ]
        pos = int(
            c.execute(
                text(
                    "SELECT COALESCE(max(position), -1) + 1 FROM workspace_nodes WHERE company_id = :c AND parent_id IS NOT DISTINCT FROM CAST(:p AS uuid)"
                ),
                {"c": sc.company_id, "p": parent["id"] if parent else None},
            ).scalar_one()
        )
        for d in defs:
            try:
                name = unique_name(validate_node_name(d.name), siblings)
            except ConfigError as exc:
                raise bad(exc) from exc
            siblings.append(name.lower())
            key = unique_key(slugify_key(d.ref, "item"), node_keys)
            node_keys.append(key)
            node = _insert_node(
                sc,
                node_id=new_id(),
                parent=parent,
                name=name,
                display_name=None,
                description=d.description,
                config={},
                position=pos,
                key=key,
            )
            pos += 1
            nodes[d.ref] = node
            created["folders" if parent else "sections"] += 1
            add_nodes(d.folders, node)

    add_nodes(definition.sections, None)

    cat_ids: dict[str, str] = {}
    cat_taken = [
        r[0] for r in c.execute(text("SELECT key FROM document_categories WHERE company_id = :c"), {"c": sc.company_id})
    ]
    cat_names = [
        r[0]
        for r in c.execute(
            text("SELECT lower(name) FROM document_categories WHERE company_id = :c AND archived_at IS NULL"),
            {"c": sc.company_id},
        )
    ]
    for cd in definition.categories:
        kind = KIND_BY_KEY[cd.document_kind]
        key = unique_key(slugify_key(cd.ref, "category"), cat_taken)
        cat_taken.append(key)
        name = unique_name(cd.name, cat_names)
        cat_names.append(name.lower())
        cid = new_id()
        c.execute(
            text(
                "INSERT INTO document_categories (id, organization_id, company_id, key, name, description, document_kind, accounting_domain, processing_type,"
                " allowed_file_types, node_id, created_by, updated_by) VALUES (:id, :o, :c, :k, :n, :d, :dk, :ad, :pt, :ft, :node, :u, :u)"
            ),
            {
                "id": cid,
                "o": sc.org_id,
                "c": sc.company_id,
                "k": key,
                "n": name,
                "d": cd.description,
                "dk": kind.key,
                "ad": kind.accounting_domain,
                "pt": cd.processing_type
                if cd.processing_type in (kind.processing_type, "reference_only")
                else kind.processing_type,
                "ft": cd.allowed_file_types,
                "node": nodes[cd.node_ref]["id"] if cd.node_ref else None,
                "u": sc.actor_id,
            },
        )
        cat_ids[cd.ref] = str(cid)
        created["categories"] += 1

    f_taken = [
        r[0]
        for r in c.execute(text("SELECT key FROM custom_field_definitions WHERE company_id = :c"), {"c": sc.company_id})
    ]
    f_labels = [
        r[0]
        for r in c.execute(
            text("SELECT lower(label) FROM custom_field_definitions WHERE company_id = :c AND archived_at IS NULL"),
            {"c": sc.company_id},
        )
    ]
    f_keys: dict[str, str] = {}
    order = int(
        c.execute(
            text("SELECT COALESCE(max(display_order), 0) FROM custom_field_definitions WHERE company_id = :c"),
            {"c": sc.company_id},
        ).scalar_one()
    )
    for fd in definition.fields:
        key = unique_key(fd.ref, f_taken, "_")
        f_taken.append(key)
        f_keys[fd.ref] = key
        label = unique_name(fd.label, f_labels)
        f_labels.append(label.lower())
        order += 10
        fid = new_id()
        c.execute(
            text(
                "INSERT INTO custom_field_definitions (id, organization_id, company_id, key, label, description, data_type, required, default_value, validation,"
                " reference_target, display_order, created_by, updated_by) VALUES (:id, :o, :c, :k, :l, :d, :t, :r, CAST(:dv AS jsonb), CAST(:v AS jsonb), :rt, :ord, :u, :u)"
            ),
            {
                "id": fid,
                "o": sc.org_id,
                "c": sc.company_id,
                "k": key,
                "l": label,
                "d": fd.description,
                "t": fd.data_type,
                "r": fd.required,
                "dv": None if fd.default_value is None else _j(fd.default_value),
                "v": _j(fd.validation),
                "rt": fd.reference_target,
                "ord": order,
                "u": sc.actor_id,
            },
        )
        for pos, o in enumerate(fd.options):
            c.execute(
                text(
                    "INSERT INTO custom_field_options (id, organization_id, company_id, field_id, value, label, position) VALUES (:id, :o, :c, :f, :v, :l, :p)"
                ),
                {"id": new_id(), "o": sc.org_id, "c": sc.company_id, "f": fid, "v": o.value, "l": o.label, "p": pos},
            )
        for cr in dict.fromkeys(fd.category_refs):
            c.execute(
                text(
                    "INSERT INTO custom_field_applicability (id, organization_id, company_id, field_id, category_id) VALUES (:id, :o, :c, :f, :x)"
                ),
                {"id": new_id(), "o": sc.org_id, "c": sc.company_id, "f": fid, "x": cat_ids[cr]},
            )
        for nr in dict.fromkeys(fd.node_refs):
            c.execute(
                text(
                    "INSERT INTO custom_field_applicability (id, organization_id, company_id, field_id, node_id) VALUES (:id, :o, :c, :f, :x)"
                ),
                {"id": new_id(), "o": sc.org_id, "c": sc.company_id, "f": fid, "x": nodes[nr]["id"]},
            )
        created["fields"] += 1

    for nd in definition.naming_rules:
        try:
            with c.begin_nested():
                c.execute(
                    text(
                        "INSERT INTO naming_rules (id, organization_id, company_id, name, pattern, category_id, created_by, updated_by) VALUES (:id, :o, :c, :n, :p, :cat, :u, :u)"
                    ),
                    {
                        "id": new_id(),
                        "o": sc.org_id,
                        "c": sc.company_id,
                        "n": nd.name,
                        "p": nd.pattern,
                        "cat": cat_ids.get(nd.category_ref) if nd.category_ref else None,
                        "u": sc.actor_id,
                    },
                )
            created["naming_rules"] += 1
        except IntegrityError:
            skipped.append(f"Naming rule '{nd.name}' skipped: that category already has an active naming rule.")

    base_prio = int(
        c.execute(
            text("SELECT COALESCE(max(priority), 0) FROM filing_rules WHERE company_id = :c AND archived_at IS NULL"),
            {"c": sc.company_id},
        ).scalar_one()
    )
    for fr in definition.filing_rules:
        conds = [
            {**cd, "field": f"custom:{f_keys[cd['field'][7:]]}"} if str(cd["field"]).startswith("custom:") else cd
            for cd in fr.conditions
        ]
        c.execute(
            text(
                "INSERT INTO filing_rules (id, organization_id, company_id, name, description, priority, conditions, target_node_id, target_category_id, created_by, updated_by)"
                " VALUES (:id, :o, :c, :n, :d, :p, CAST(:cond AS jsonb), :t, :tc, :u, :u)"
            ),
            {
                "id": new_id(),
                "o": sc.org_id,
                "c": sc.company_id,
                "n": fr.name,
                "d": fr.description,
                "p": base_prio + fr.priority,
                "cond": _j(conds),
                "t": nodes[fr.target_node_ref]["id"],
                "tc": cat_ids.get(fr.target_category_ref) if fr.target_category_ref else None,
                "u": sc.actor_id,
            },
        )
        created["filing_rules"] += 1
    sc.record(
        "template.applied", "workspace", sc.company_id, None, {"created": created, "mode": mode, "skipped": skipped}
    )
    return {"created": created, "skipped": skipped}


# ---------------------------------------------------------------- template storage
def list_templates(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            "SELECT t.id, t.organization_id, t.key, t.name, t.description, t.archived_at, t.created_at, t.source_company_id,"
            " (t.organization_id IS NULL) AS is_system,"
            " (SELECT max(version) FROM workspace_template_versions v WHERE v.template_id = t.id) AS latest_version"
            " FROM workspace_templates t WHERE t.archived_at IS NULL ORDER BY (t.organization_id IS NULL) DESC, t.name"
        )
    )
    out = []
    for t in map(row_dict, rows):
        v = conn.execute(
            text("SELECT definition FROM workspace_template_versions WHERE template_id = :t AND version = :v"),
            {"t": t["id"], "v": t["latest_version"]},
        ).scalar_one()
        t["counts"] = TemplateDefinition.model_validate(v).counts()
        out.append(t)
    return out


def get_version(
    conn: Connection, template_id: uuid.UUID, version: int | None
) -> tuple[dict[str, Any], int, dict[str, Any]]:
    t = conn.execute(
        text("SELECT id, organization_id, key, name, description, archived_at FROM workspace_templates WHERE id = :t"),
        {"t": template_id},
    ).first()
    if t is None or t.archived_at is not None:
        raise NotFound("Template not found.")
    q = "SELECT version, definition, changelog, created_at FROM workspace_template_versions WHERE template_id = :t " + (
        "AND version = :v" if version else "ORDER BY version DESC LIMIT 1"
    )
    v = conn.execute(text(q), {"t": template_id, "v": version}).first()
    if v is None:
        raise NotFound("Template version not found.")
    return row_dict(t), int(v.version), v.definition if isinstance(v.definition, dict) else json.loads(v.definition)


def template_versions(conn: Connection, template_id: uuid.UUID) -> list[dict[str, Any]]:
    return [
        row_dict(r)
        for r in conn.execute(
            text(
                "SELECT version, changelog, created_by, created_at FROM workspace_template_versions WHERE template_id = :t ORDER BY version DESC"
            ),
            {"t": template_id},
        )
    ]


def create_template(
    conn: Connection,
    org_id: uuid.UUID,
    actor: uuid.UUID,
    name: str,
    description: str,
    definition: TemplateDefinition,
    source_company_id: uuid.UUID | None,
) -> dict[str, Any]:
    entitlements.guard(conn, org_id, "limit.workspace_templates")
    taken = [
        r[0]
        for r in conn.execute(text("SELECT key FROM workspace_templates WHERE organization_id = :o"), {"o": org_id})
    ]
    tid = new_id()
    conn.execute(
        text(
            "INSERT INTO workspace_templates (id, organization_id, key, name, description, source_company_id, created_by) VALUES (:id, :o, :k, :n, :d, :s, :u)"
        ),
        {
            "id": tid,
            "o": org_id,
            "k": unique_key(slugify_key(name, "template"), taken),
            "n": name,
            "d": description,
            "s": source_company_id,
            "u": actor,
        },
    )
    conn.execute(
        text(
            "INSERT INTO workspace_template_versions (id, template_id, organization_id, version, definition, changelog, created_by) VALUES (:id, :t, :o, 1, CAST(:d AS jsonb), 'Initial version', :u)"
        ),
        {"id": new_id(), "t": tid, "o": org_id, "d": definition.model_dump_json(), "u": actor},
    )
    entitlements.refresh_counter(conn, org_id, "limit.workspace_templates")
    return {"id": str(tid), "version": 1}


def add_version(
    conn: Connection,
    org_id: uuid.UUID,
    actor: uuid.UUID,
    template_id: uuid.UUID,
    definition: TemplateDefinition,
    changelog: str,
) -> int:
    t = conn.execute(
        text("SELECT organization_id, archived_at FROM workspace_templates WHERE id = :t"), {"t": template_id}
    ).first()
    if t is None or t.archived_at is not None:
        raise NotFound("Template not found.")
    if t.organization_id is None or t.organization_id != org_id:
        raise Conflict("Artify system templates cannot be changed.")
    conn.execute(text("SELECT 1 FROM workspace_templates WHERE id = :t FOR UPDATE"), {"t": template_id})
    v = int(
        conn.execute(
            text("SELECT COALESCE(max(version), 0) + 1 FROM workspace_template_versions WHERE template_id = :t"),
            {"t": template_id},
        ).scalar_one()
    )
    conn.execute(
        text(
            "INSERT INTO workspace_template_versions (id, template_id, organization_id, version, definition, changelog, created_by) VALUES (:id, :t, :o, :v, CAST(:d AS jsonb), :c, :u)"
        ),
        {
            "id": new_id(),
            "t": template_id,
            "o": org_id,
            "v": v,
            "d": definition.model_dump_json(),
            "c": changelog,
            "u": actor,
        },
    )
    return v


def fit_report(
    conn: Connection, org_id: uuid.UUID, company_id: uuid.UUID, definition: TemplateDefinition, mode: str
) -> list[dict[str, Any]]:
    """Would applying this definition fit the company's limits? (preview only; the real check runs on apply)"""
    usage = entitlements.company_usage(conn, org_id, company_id)
    c = definition.counts()
    need = {
        "limit.workspace_sections_per_company": c["sections"],
        "limit.folders_per_company": c["folders"],
        "limit.custom_fields_per_company": c["fields"],
        "limit.filing_rules_per_company": c["filing_rules"],
        "limit.folder_depth": definition.max_depth(),
    }
    out = []
    for key, n in need.items():
        lim = entitlements.effective_limit(conn, org_id, key)
        base = 0 if mode == "replace" else usage[key]
        after = max(base, n) if key == "limit.folder_depth" else base + n
        out.append(
            {
                "key": key,
                "limit": lim.value,
                "unlimited": lim.unlimited,
                "current": usage[key],
                "after": after,
                "fits": lim.unlimited or after <= (lim.value or 0) or after <= usage[key],
            }
        )
    return out


# ---------------------------------------------------------------- restore a revision (non-destructive)
_NODE_COLS = ("name", "display_name", "description", "position", "config", "is_active")


def restore_revision(sc: Scope, version: int) -> dict[str, Any]:
    """Return the configuration (structure, categories, fields, rules) to the state recorded in revision `version`.

    Nothing is deleted: items created later are ARCHIVED, items archived later are restored, renames/moves/edits are
    reverted. Items that were hard-deleted since are re-created with their original ids. Permission grants and the
    access mode are deliberately NOT restored (a security decision must be re-made explicitly). Conflicts roll the
    whole operation back."""
    c = sc.conn
    row = c.execute(
        text("SELECT snapshot FROM workspace_revisions WHERE company_id = :c AND version = :v"),
        {"c": sc.company_id, "v": version},
    ).first()
    if row is None:
        raise NotFound("That revision does not exist.")
    snap = row.snapshot if isinstance(row.snapshot, dict) else json.loads(row.snapshot)
    stats = {"archived": 0, "restored": 0, "updated": 0, "recreated": 0}
    try:
        with c.begin_nested():
            _restore_nodes(sc, snap["nodes"], stats)
            _restore_simple(
                sc,
                "document_categories",
                snap["categories"],
                (
                    "name",
                    "description",
                    "document_kind",
                    "accounting_domain",
                    "processing_type",
                    "allowed_file_types",
                    "processing_settings",
                    "node_id",
                    "is_active",
                ),
                {"processing_settings"},
                stats,
            )
            _restore_simple(
                sc,
                "custom_field_definitions",
                snap["fields"],
                ("label", "description", "required", "default_value", "validation", "display_order", "is_active"),
                {"default_value", "validation"},
                stats,
            )
            _restore_options(sc, snap["field_options"], snap["field_applicability"], {f["id"] for f in snap["fields"]})
            _restore_simple(
                sc,
                "naming_rules",
                snap["naming_rules"],
                ("name", "description", "pattern", "category_id", "is_active"),
                set(),
                stats,
            )
            _restore_simple(
                sc,
                "filing_rules",
                snap["filing_rules"],
                ("name", "description", "priority", "conditions", "target_node_id", "target_category_id", "is_active"),
                {"conditions"},
                stats,
            )
    except IntegrityError as exc:
        raise Conflict(
            "The restore clashes with names or settings that exist now (for example a duplicate name). Rename the conflicting item first."
        ) from exc
    sc.record("workspace.restored_from", "workspace", sc.company_id, None, {"from_version": version, **stats})
    return stats


def _restore_nodes(sc: Scope, snap_nodes: list[dict[str, Any]], stats: dict[str, int]) -> None:
    c = sc.conn
    snap = {n["id"]: n for n in snap_nodes}
    current = {n["id"]: n for n in fetch_nodes(c, sc.company_id)}
    # 0. archive what should not be active
    for nid, n in current.items():
        if n["archived_at"] is None and (nid not in snap or snap[nid]["archived_at"] is not None):
            c.execute(
                text(
                    "UPDATE workspace_nodes SET archived_at = now(), archived_by = :u, updated_at = now() WHERE id = :i"
                ),
                {"u": sc.actor_id, "i": nid},
            )
            stats["archived"] += 1
    # 1. re-create hard-deleted nodes (parents first)
    order = sorted(snap.values(), key=lambda n: n["depth"])
    for n in order:
        if n["id"] not in current:
            parent = fetch_one(c, sc.company_id, n["parent_id"]) if n["parent_id"] else None
            if n["parent_id"] and parent is None:
                raise Conflict("A deleted folder's parent no longer exists; it cannot be recreated.")
            created = _insert_node(
                sc,
                node_id=uuid.UUID(n["id"]),
                parent=parent,
                name=n["name"],
                display_name=n["display_name"],
                description=n["description"],
                config=n["config"],
                position=n["position"],
                key=n["key"],
            )
            current[created["id"]] = created
            stats["recreated"] += 1
    # 2. park renamed/moved nodes under temporary names to avoid transient sibling-name collisions
    for n in order:
        cur = current[n["id"]]
        if n["archived_at"] is None and (cur["name"] != n["name"] or cur["parent_id"] != n["parent_id"]):
            c.execute(
                text("UPDATE workspace_nodes SET name = :t WHERE id = :i"), {"t": f"~restore-{n['id']}", "i": n["id"]}
            )
    # 3. final values, parents first
    paths: dict[str, list[str]] = {}
    for n in order:
        cur = current[n["id"]]
        parent_path = paths.get(
            n["parent_id"], [str(p) for p in current[n["parent_id"]]["path_ids"]] if n["parent_id"] else []
        )
        path = [*parent_path, n["id"]]
        paths[n["id"]] = path
        c.execute(
            text(
                "UPDATE workspace_nodes SET parent_id = :p, path_ids = CAST(:path AS uuid[]), depth = :d, name = :n, display_name = :dn, description = :ds,"
                " position = :pos, config = CAST(:cfg AS jsonb), is_active = :a, archived_at = CAST(:arch AS timestamptz), archived_by = CASE WHEN CAST(:arch AS timestamptz) IS NULL THEN NULL ELSE archived_by END,"
                " updated_by = :u, updated_at = now(), row_version = row_version + 1 WHERE id = :i AND company_id = :c"
            ),
            {
                "p": n["parent_id"],
                "path": "{" + ",".join(path) + "}",
                "d": len(path),
                "n": n["name"],
                "dn": n["display_name"],
                "ds": n["description"],
                "pos": n["position"],
                "cfg": _j(n["config"]),
                "a": n["is_active"],
                "arch": n["archived_at"],
                "u": sc.actor_id,
                "i": n["id"],
                "c": sc.company_id,
            },
        )
        if cur["archived_at"] is not None and n["archived_at"] is None:
            stats["restored"] += 1
        elif cur["name"] != n["name"] or cur["parent_id"] != n["parent_id"] or cur["description"] != n["description"]:
            stats["updated"] += 1
    _recompute_paths(c, sc.company_id)


def fetch_one(conn: Connection, company_id: uuid.UUID, node_id: str) -> dict[str, Any] | None:
    r = conn.execute(
        text("SELECT id, parent_id, path_ids, depth FROM workspace_nodes WHERE company_id = :c AND id = :i"),
        {"c": company_id, "i": node_id},
    ).first()
    return row_dict(r) if r else None


def _restore_simple(
    sc: Scope, table: str, rows: list[dict[str, Any]], cols: tuple[str, ...], json_cols: set[str], stats: dict[str, int]
) -> None:
    c = sc.conn
    existing = {
        str(r.id): r
        for r in c.execute(text(f"SELECT id, archived_at FROM {table} WHERE company_id = :c"), {"c": sc.company_id})
    }
    snap_ids = {r["id"] for r in rows}
    for rid, e in existing.items():
        if rid not in snap_ids and e.archived_at is None:
            c.execute(text(f"UPDATE {table} SET archived_at = now(), updated_at = now() WHERE id = :i"), {"i": rid})
            stats["archived"] += 1
    # park names/labels first so swaps cannot collide
    name_col = "name" if "name" in cols else ("label" if "label" in cols else None)
    for r in rows:
        if r["id"] in existing and name_col and r["archived_at"] is None:
            c.execute(
                text(f"UPDATE {table} SET {name_col} = :t WHERE id = :i"), {"t": f"~restore-{r['id']}", "i": r["id"]}
            )
    for r in rows:
        if r["id"] not in existing:
            continue  # configuration entities are never hard-deleted, so this only guards odd data
        assigns = ", ".join(f"{k} = CAST(:{k} AS jsonb)" if k in json_cols else f"{k} = :{k}" for k in cols)
        params = {k: (None if r[k] is None else _j(r[k])) if k in json_cols else r[k] for k in cols}
        c.execute(
            text(
                f"UPDATE {table} SET {assigns}, archived_at = CAST(:arch AS timestamptz), updated_at = now(), updated_by = :u WHERE id = :i AND company_id = :c"
            ),
            {**params, "arch": r["archived_at"], "u": sc.actor_id, "i": r["id"], "c": sc.company_id},
        )
        if existing[r["id"]].archived_at is not None and r["archived_at"] is None:
            stats["restored"] += 1


def _restore_options(
    sc: Scope, options: list[dict[str, Any]], applicability: list[dict[str, Any]], field_ids: set[str]
) -> None:
    c = sc.conn
    snap_opt = {o["id"]: o for o in options}
    for r in c.execute(
        text("SELECT id FROM custom_field_options WHERE company_id = :c AND archived_at IS NULL"), {"c": sc.company_id}
    ).all():
        if str(r.id) not in snap_opt or snap_opt[str(r.id)]["archived_at"] is not None:
            c.execute(text("UPDATE custom_field_options SET archived_at = now() WHERE id = :i"), {"i": r.id})
    for o in options:
        c.execute(
            text(
                "UPDATE custom_field_options SET label = :l, position = :p, archived_at = CAST(:a AS timestamptz) WHERE id = :i AND company_id = :c"
            ),
            {"l": o["label"], "p": o["position"], "a": o["archived_at"], "i": o["id"], "c": sc.company_id},
        )
    c.execute(
        text("DELETE FROM custom_field_applicability WHERE company_id = :c AND field_id = ANY(CAST(:f AS uuid[]))"),
        {"c": sc.company_id, "f": "{" + ",".join(field_ids) + "}"},
    )
    for a in applicability:
        c.execute(
            text(
                "INSERT INTO custom_field_applicability (id, organization_id, company_id, field_id, category_id, node_id) VALUES (:id, :o, :c, :f, :cat, :n)"
            ),
            {
                "id": new_id(),
                "o": sc.org_id,
                "c": sc.company_id,
                "f": a["field_id"],
                "cat": a["category_id"],
                "n": a["node_id"],
            },
        )
