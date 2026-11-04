"""Workspace core: scope, optimistic versioning, snapshots/revisions, node tree operations and access loading.

Every mutation runs in the request transaction after `begin_change` has locked the company's configuration row
(`company_configurations`, ADR phase-3 integration rule 2), so changes to one company are serialized, atomic and
versioned. `commit` then bumps `config_version`, stores an immutable revision (snapshot + before/after) and writes
an audit event.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from artify_api import audit, entitlements
from artify_api.errors import Conflict, EntitlementExceeded, Forbidden, NotFound, Unprocessable
from artify_api.ids import new_id
from artify_api.workspace_engine import (
    AccessSubject,
    ConfigError,
    Grant,
    decide,
    slugify_key,
    unique_key,
    unique_name,
    validate_node_name,
)

# System safety ceiling for nesting (path arrays, local Windows path length). The plan limit
# `limit.folder_depth` (entitlements) is enforced separately and is always <= this ceiling in practice.
MAX_DEPTH_HARD = 12


def revoke_user_grants(
    conn: Connection,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    actor_id: uuid.UUID,
    meta: audit.RequestMeta,
    reason: str,
    company_id: uuid.UUID | None = None,
) -> int:
    """Delete a person's personal workspace grants (one company, or every company of the organization).

    Grants follow the assignment that made them meaningful: when an assignment is removed, the person's explicit
    workspace grants on that company go with it, so a later re-assignment starts from the role defaults instead of
    silently reviving old powers. Role-subject grants are not touched. One audit event per affected company."""
    where = "organization_id = :o AND subject_user_id = :u" + (" AND company_id = :c" if company_id else "")
    params: dict[str, Any] = {"o": org_id, "u": user_id, **({"c": company_id} if company_id else {})}
    rows = conn.execute(text(f"DELETE FROM workspace_permissions WHERE {where} RETURNING company_id"), params).all()
    per_company: dict[uuid.UUID, int] = {}
    for r in rows:
        per_company[r.company_id] = per_company.get(r.company_id, 0) + 1
    for cid, n in per_company.items():
        audit.write_audit(
            conn,
            action="workspace.grants_revoked",
            actor_id=actor_id,
            org_id=org_id,
            company_id=cid,
            entity_type="user",
            entity_id=user_id,
            summary={"count": n, "reason": reason},
            meta=meta,
        )
    return len(rows)


class VersionConflict(Conflict):
    code, title = "workspace_version_conflict", "The workspace changed since you loaded it"


class ConfirmationRequired(Conflict):
    code, title = "confirmation_required", "This change affects existing configuration"


def bad(exc: ConfigError) -> Unprocessable:
    return Unprocessable(str(exc))


@dataclass
class Scope:
    conn: Connection
    org_id: uuid.UUID
    company_id: uuid.UUID
    actor_id: uuid.UUID
    meta: audit.RequestMeta
    changes: list[dict[str, Any]]
    before_usage: dict[str, int]

    def record(
        self, action: str, entity_type: str, entity_id: uuid.UUID | str, before: Any = None, after: Any = None
    ) -> None:
        self.changes.append(
            {
                "action": action,
                "entity_type": entity_type,
                "entity_id": str(entity_id),
                "before": before,
                "after": after,
            }
        )


def _j(value: Any) -> str:
    return json.dumps(value, default=str)


def row_dict(row: Any) -> dict[str, Any]:
    d = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
    return {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in d.items()}


# ---------------------------------------------------------------- configuration lock, versioning, snapshots
def begin_change(
    conn: Connection,
    org_id: uuid.UUID,
    company_id: uuid.UUID,
    actor_id: uuid.UUID,
    meta: audit.RequestMeta,
    base_version: int | None = None,
) -> Scope:
    """Lock the company's configuration row and verify the company is editable and the caller's view is current."""
    row = conn.execute(
        text(
            "SELECT cc.config_version, c.status FROM company_configurations cc JOIN companies c ON c.id = cc.company_id"
            " WHERE cc.company_id = :c FOR UPDATE OF cc"
        ),
        {"c": company_id},
    ).first()
    if row is None:
        raise NotFound("Company not found.")
    if row.status != "active":
        raise Conflict(
            "Only active companies can be changed; this company is inactive or archived.",
            extra={"company_status": row.status},
        )
    if base_version is not None and base_version != row.config_version:
        raise VersionConflict(
            "The workspace was changed by someone else. Reload and try again.",
            extra={"current_version": row.config_version, "base_version": base_version},
        )
    return Scope(conn, org_id, company_id, actor_id, meta, [], entitlements.company_usage(conn, org_id, company_id))


def current_version(conn: Connection, company_id: uuid.UUID) -> int:
    v = conn.execute(
        text("SELECT config_version FROM company_configurations WHERE company_id = :c"), {"c": company_id}
    ).scalar()
    if v is None:
        raise NotFound("Company not found.")
    return int(v)


_SNAP_SQL = """
SELECT jsonb_build_object(
  'nodes', COALESCE((SELECT jsonb_agg(to_jsonb(n) - 'organization_id' - 'company_id' - 'path_ids' ORDER BY n.depth, n.position, n.created_at, n.id)
                     FROM workspace_nodes n WHERE n.company_id = :c), '[]'::jsonb),
  'categories', COALESCE((SELECT jsonb_agg(to_jsonb(x) - 'organization_id' - 'company_id' ORDER BY x.created_at, x.id)
                          FROM document_categories x WHERE x.company_id = :c), '[]'::jsonb),
  'fields', COALESCE((SELECT jsonb_agg(to_jsonb(f) - 'organization_id' - 'company_id' ORDER BY f.display_order, f.created_at, f.id)
                      FROM custom_field_definitions f WHERE f.company_id = :c), '[]'::jsonb),
  'field_options', COALESCE((SELECT jsonb_agg(to_jsonb(o) - 'organization_id' - 'company_id' ORDER BY o.field_id, o.position, o.id)
                             FROM custom_field_options o WHERE o.company_id = :c), '[]'::jsonb),
  'field_applicability', COALESCE((SELECT jsonb_agg(to_jsonb(a) - 'organization_id' - 'company_id' ORDER BY a.id)
                                   FROM custom_field_applicability a WHERE a.company_id = :c), '[]'::jsonb),
  'naming_rules', COALESCE((SELECT jsonb_agg(to_jsonb(r) - 'organization_id' - 'company_id' ORDER BY r.created_at, r.id)
                            FROM naming_rules r WHERE r.company_id = :c), '[]'::jsonb),
  'filing_rules', COALESCE((SELECT jsonb_agg(to_jsonb(r) - 'organization_id' - 'company_id' ORDER BY r.priority, r.created_at, r.id)
                            FROM filing_rules r WHERE r.company_id = :c), '[]'::jsonb),
  'permissions', COALESCE((SELECT jsonb_agg(to_jsonb(p) - 'organization_id' - 'company_id' ORDER BY p.created_at, p.id)
                           FROM workspace_permissions p WHERE p.company_id = :c), '[]'::jsonb),
  'access_mode', (SELECT workspace_access_mode FROM company_configurations WHERE company_id = :c))
"""


def snapshot(conn: Connection, company_id: uuid.UUID) -> dict[str, Any]:
    result = conn.execute(text(_SNAP_SQL), {"c": company_id}).scalar_one()
    return result if isinstance(result, dict) else json.loads(result)


def commit(sc: Scope, action: str, *, entity_type: str | None = None, entity_id: uuid.UUID | str | None = None) -> int:
    """Finish a change: enforce entitlements on increases, bump the version, write the revision and the audit event."""
    entitlements.enforce_company_limits(sc.conn, sc.org_id, sc.company_id, sc.before_usage)
    version = sc.conn.execute(
        text(
            "UPDATE company_configurations SET config_version = config_version + 1, workspace_state = 'configured',"
            " updated_at = now() WHERE company_id = :c RETURNING config_version"
        ),
        {"c": sc.company_id},
    ).scalar_one()
    sc.conn.execute(
        text(
            "INSERT INTO workspace_revisions (id, organization_id, company_id, version, action, changes, snapshot, actor_id)"
            " VALUES (:id, :o, :c, :v, :a, CAST(:ch AS jsonb), CAST(:sn AS jsonb), :u)"
        ),
        {
            "id": new_id(),
            "o": sc.org_id,
            "c": sc.company_id,
            "v": version,
            "a": action,
            "ch": _j(sc.changes),
            "sn": _j(snapshot(sc.conn, sc.company_id)),
            "u": sc.actor_id,
        },
    )
    audit.write_audit(
        sc.conn,
        action=f"workspace.{action}",
        actor_id=sc.actor_id,
        org_id=sc.org_id,
        company_id=sc.company_id,
        entity_type=entity_type,
        entity_id=entity_id,
        summary={
            "version": version,
            "changes": [{k: c[k] for k in ("action", "entity_type", "entity_id")} for c in sc.changes][:50],
        },
        meta=sc.meta,
    )
    return int(version)


# ---------------------------------------------------------------- access
@dataclass
class Access:
    subject: AccessSubject
    grants: list[Grant]
    nodes: dict[str, dict[str, Any]]  # id -> node row

    def chain(self, node_id: str) -> list[uuid.UUID]:
        out: list[uuid.UUID] = []
        cur: str | None = node_id
        while cur:
            if cur not in self.nodes:
                raise NotFound("Workspace item not found.")
            out.append(uuid.UUID(cur))
            cur = self.nodes[cur]["parent_id"]
        return out

    def can(self, action: str, node_id: str | uuid.UUID | None = None) -> bool:
        chain = self.chain(str(node_id)) if node_id else []
        return decide(self.subject, chain, self.grants, action)[0]

    def reason(self, action: str, node_id: str | uuid.UUID | None = None) -> str:
        chain = self.chain(str(node_id)) if node_id else []
        return decide(self.subject, chain, self.grants, action)[1]

    def visible(self, node_id: str) -> bool:
        return self.can("view", node_id)

    def require(self, action: str, node_id: str | uuid.UUID | None = None) -> None:
        """Hidden nodes are indistinguishable from missing ones (404); visible but forbidden actions are 403."""
        if node_id and not self.visible(str(node_id)):
            raise NotFound("Workspace item not found.")
        if not self.can(action, node_id):
            raise Forbidden(
                "You do not have permission to do this in this part of the workspace.",
                extra={"workspace_action": action},
            )


def load_access(
    conn: Connection, company_id: uuid.UUID, user_id: uuid.UUID, org_role: str | None, base: frozenset[str]
) -> Access:
    mode = (
        conn.execute(
            text("SELECT workspace_access_mode FROM company_configurations WHERE company_id = :c"), {"c": company_id}
        ).scalar()
        or "open"
    )
    nodes = {
        r["id"]: r
        for r in (
            row_dict(x)
            for x in conn.execute(
                text("SELECT id, parent_id, node_type, archived_at FROM workspace_nodes WHERE company_id = :c"),
                {"c": company_id},
            )
        )
    }
    grants = [
        Grant(
            uuid.UUID(g["node_id"]) if g["node_id"] else None,
            g["subject_type"],
            uuid.UUID(g["subject_user_id"]) if g["subject_user_id"] else None,
            g["subject_role"],
            g["permission"],
            g["effect"],
        )
        for g in (
            row_dict(x)
            for x in conn.execute(
                text(
                    "SELECT node_id, subject_type, subject_user_id, subject_role, permission, effect FROM workspace_permissions WHERE company_id = :c"
                ),
                {"c": company_id},
            )
        )
    ]
    return Access(AccessSubject(user_id, org_role, base, mode), grants, nodes)


# ---------------------------------------------------------------- node queries
NODE_COLS = (
    "id, parent_id, node_type, key, name, display_name, description, position, depth, config, is_active, archived_at,"
    " created_by, created_at, updated_by, updated_at, row_version"
)


def fetch_nodes(conn: Connection, company_id: uuid.UUID, *, include_archived: bool = True) -> list[dict[str, Any]]:
    where = "" if include_archived else " AND archived_at IS NULL"
    rows = conn.execute(
        text(
            f"SELECT {NODE_COLS}, path_ids FROM workspace_nodes WHERE company_id = :c{where} ORDER BY depth, position, created_at, id"
        ),
        {"c": company_id},
    )
    return [row_dict(r) for r in rows]


def get_node(conn: Connection, company_id: uuid.UUID, node_id: uuid.UUID, *, lock: bool = False) -> dict[str, Any]:
    row = conn.execute(
        text(
            f"SELECT {NODE_COLS}, path_ids FROM workspace_nodes WHERE company_id = :c AND id = :n"
            + (" FOR UPDATE" if lock else "")
        ),
        {"c": company_id, "n": node_id},
    ).first()
    if row is None:
        raise NotFound("Workspace item not found.")
    return row_dict(row)


def _sibling_names(
    conn: Connection, company_id: uuid.UUID, parent_id: str | uuid.UUID | None, exclude: str | uuid.UUID | None = None
) -> list[str]:
    rows = conn.execute(
        text(
            "SELECT lower(name) FROM workspace_nodes WHERE company_id = :c AND archived_at IS NULL"
            " AND parent_id IS NOT DISTINCT FROM CAST(:p AS uuid) AND (CAST(:x AS uuid) IS NULL OR id <> CAST(:x AS uuid))"
        ),
        {"c": company_id, "p": parent_id, "x": exclude},
    )
    return [r[0] for r in rows]


def _assert_name_free(conn: Connection, company_id: uuid.UUID, parent_id: Any, name: str, exclude: Any = None) -> None:
    if name.lower() in _sibling_names(conn, company_id, parent_id, exclude):
        raise Conflict(
            f"'{name}' already exists here. Choose another name or archive the existing item.",
            extra={"suggestion": unique_name(name, _sibling_names(conn, company_id, parent_id, exclude))},
        )


def _all_keys(conn: Connection, company_id: uuid.UUID) -> list[str]:
    return [
        r[0] for r in conn.execute(text("SELECT key FROM workspace_nodes WHERE company_id = :c"), {"c": company_id})
    ]


def _insert_node(
    sc: Scope,
    *,
    node_id: uuid.UUID,
    parent: dict[str, Any] | None,
    name: str,
    display_name: str | None,
    description: str,
    config: dict[str, Any],
    position: int,
    key: str,
) -> dict[str, Any]:
    path = [*map(str, parent["path_ids"] if parent else []), str(node_id)]
    sc.conn.execute(
        text(
            "INSERT INTO workspace_nodes (id, organization_id, company_id, parent_id, node_type, key, name, display_name,"
            " description, position, depth, path_ids, config, created_by, updated_by)"
            " VALUES (:id, :o, :c, :p, :t, :k, :n, :dn, :d, :pos, :dep, CAST(:path AS uuid[]), CAST(:cfg AS jsonb), :u, :u)"
        ),
        {
            "id": node_id,
            "o": sc.org_id,
            "c": sc.company_id,
            "p": parent["id"] if parent else None,
            "t": "folder" if parent else "section",
            "k": key,
            "n": name,
            "dn": display_name,
            "d": description,
            "pos": position,
            "dep": len(path),
            "path": "{" + ",".join(path) + "}",
            "cfg": _j(config),
            "u": sc.actor_id,
        },
    )
    return get_node(sc.conn, sc.company_id, node_id)


def create_node(
    sc: Scope,
    parent_id: uuid.UUID | None,
    name: str,
    display_name: str | None,
    description: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    try:
        clean = validate_node_name(name)
    except ConfigError as exc:
        raise bad(exc) from exc
    parent = get_node(sc.conn, sc.company_id, parent_id, lock=True) if parent_id else None
    if parent is not None:
        if parent["archived_at"] is not None or not parent["is_active"]:
            raise Conflict("You cannot add items to an archived or inactive folder. Restore it first.")
        if parent["depth"] + 1 > MAX_DEPTH_HARD:
            raise Unprocessable(f"Folders can be nested at most {MAX_DEPTH_HARD} levels deep.")
        _guard_depth(sc, parent["depth"] + 1)
    _assert_name_free(sc.conn, sc.company_id, parent_id, clean)
    siblings = sc.conn.execute(
        text(
            "SELECT COALESCE(max(position), -1) + 1 FROM workspace_nodes WHERE company_id = :c AND parent_id IS NOT DISTINCT FROM CAST(:p AS uuid)"
        ),
        {"c": sc.company_id, "p": parent_id},
    ).scalar_one()
    node = _insert_node(
        sc,
        node_id=new_id(),
        parent=parent,
        name=clean,
        display_name=display_name,
        description=description,
        config=config,
        position=int(siblings),
        key=unique_key(
            slugify_key(clean, "section" if parent is None else "folder"), _all_keys(sc.conn, sc.company_id)
        ),
    )
    sc.record("node.created", node["node_type"], node["id"], None, _node_brief(node))
    return node


def _guard_depth(sc: Scope, depth: int) -> None:
    lim = entitlements.effective_limit(sc.conn, sc.org_id, "limit.folder_depth")
    current = sc.before_usage.get("limit.folder_depth", 0)
    if depth > current and not lim.unlimited and depth > (lim.value or 0):
        raise EntitlementExceeded(key="limit.folder_depth", limit=lim.value or 0, current=current, requested=depth)


def _node_brief(n: dict[str, Any]) -> dict[str, Any]:
    return {
        k: n.get(k)
        for k in ("name", "display_name", "description", "parent_id", "position", "is_active", "archived_at")
    }


def update_node(sc: Scope, node_id: uuid.UUID, patch: dict[str, Any]) -> dict[str, Any]:
    node = get_node(sc.conn, sc.company_id, node_id, lock=True)
    if node["archived_at"] is not None:
        raise Conflict("Restore this item before changing it.")
    sets: dict[str, Any] = {}
    if "name" in patch and patch["name"] is not None:
        try:
            clean = validate_node_name(patch["name"])
        except ConfigError as exc:
            raise bad(exc) from exc
        if clean != node["name"]:
            _assert_name_free(sc.conn, sc.company_id, node["parent_id"], clean, node_id)
            sets["name"] = clean
    for k in ("display_name", "description", "is_active"):
        if k in patch and patch[k] != node[k] and (k != "is_active" or patch[k] is not None):
            sets[k] = patch[k]
    if "config" in patch and patch["config"] is not None and patch["config"] != node["config"]:
        sets["config"] = patch["config"]
    if not sets:
        return node
    assigns = ", ".join(f"{k} = CAST(:{k} AS jsonb)" if k == "config" else f"{k} = :{k}" for k in sets)
    params = {k: (_j(v) if k == "config" else v) for k, v in sets.items()}
    sc.conn.execute(
        text(
            f"UPDATE workspace_nodes SET {assigns}, updated_by = :u, updated_at = now(), row_version = row_version + 1 WHERE id = :id AND company_id = :c"
        ),
        {**params, "u": sc.actor_id, "id": node_id, "c": sc.company_id},
    )
    after = get_node(sc.conn, sc.company_id, node_id)
    sc.record(
        "node.renamed" if "name" in sets else "node.updated",
        node["node_type"],
        node_id,
        {k: node[k] for k in sets},
        {k: after[k] for k in sets},
    )
    return after


def _recompute_paths(conn: Connection, company_id: uuid.UUID) -> None:
    """Rebuild depth/path_ids top-down for the whole company (idempotent; O(n) in the number of nodes)."""
    rows = conn.execute(
        text("SELECT id, parent_id, depth, path_ids FROM workspace_nodes WHERE company_id = :c"), {"c": company_id}
    ).all()
    by_parent: dict[Any, list[Any]] = {}
    for r in rows:
        by_parent.setdefault(r.parent_id, []).append(r)
    stack: list[tuple[Any, list[Any]]] = [(r, [r.id]) for r in by_parent.get(None, [])]
    while stack:
        r, path = stack.pop()
        if list(r.path_ids) != path or r.depth != len(path):
            conn.execute(
                text("UPDATE workspace_nodes SET path_ids = CAST(:p AS uuid[]), depth = :d WHERE id = :i"),
                {"p": "{" + ",".join(map(str, path)) + "}", "d": len(path), "i": r.id},
            )
        for ch in by_parent.get(r.id, []):
            stack.append((ch, [*path, ch.id]))


def _subtree_height(nodes: list[dict[str, Any]], root_id: str) -> int:
    root = next(n for n in nodes if n["id"] == root_id)
    return int(
        max((n["depth"] for n in nodes if root_id in [str(p) for p in n["path_ids"]]), default=root["depth"])
        - root["depth"]
        + 1
    )


def move_node(sc: Scope, node_id: uuid.UUID, new_parent_id: uuid.UUID, position: int | None) -> dict[str, Any]:
    node = get_node(sc.conn, sc.company_id, node_id, lock=True)
    if node["node_type"] == "section":
        raise Unprocessable("A section is a top-level item and cannot be moved into another item. Reorder it instead.")
    if node["archived_at"] is not None:
        raise Conflict("Restore this item before moving it.")
    parent = get_node(sc.conn, sc.company_id, new_parent_id, lock=True)
    if parent["archived_at"] is not None or not parent["is_active"]:
        raise Conflict("The destination is archived or inactive.")
    if new_parent_id == node_id or str(node_id) in [str(p) for p in parent["path_ids"]]:
        raise Unprocessable("An item cannot be moved into itself or into one of its own subfolders.")
    nodes = fetch_nodes(sc.conn, sc.company_id)
    new_depth = parent["depth"] + _subtree_height(nodes, str(node_id))
    if new_depth > MAX_DEPTH_HARD:
        raise Unprocessable(f"The move would nest folders more than {MAX_DEPTH_HARD} levels deep.")
    _guard_depth(sc, new_depth)
    if str(new_parent_id) != str(node["parent_id"]):
        _assert_name_free(sc.conn, sc.company_id, new_parent_id, node["name"], node_id)
    old_parent = node["parent_id"]
    sc.conn.execute(
        text(
            "UPDATE workspace_nodes SET parent_id = :p, path_ids = CAST(:path AS uuid[]), depth = :d, updated_by = :u,"
            " updated_at = now(), row_version = row_version + 1 WHERE id = :i AND company_id = :c"
        ),
        {
            "p": new_parent_id,
            "path": "{" + ",".join([*map(str, parent["path_ids"]), str(node_id)]) + "}",
            "d": parent["depth"] + 1,
            "u": sc.actor_id,
            "i": node_id,
            "c": sc.company_id,
        },
    )
    _recompute_paths(sc.conn, sc.company_id)
    siblings = [
        n["id"]
        for n in fetch_nodes(sc.conn, sc.company_id, include_archived=False)
        if n["parent_id"] == str(new_parent_id) and n["id"] != str(node_id)
    ]
    idx = len(siblings) if position is None else max(0, min(position, len(siblings)))
    siblings.insert(idx, str(node_id))
    _write_positions(sc, siblings)
    sc.record("node.moved", "folder", node_id, {"parent_id": old_parent}, {"parent_id": str(new_parent_id)})
    return get_node(sc.conn, sc.company_id, node_id)


def _write_positions(sc: Scope, ordered_ids: list[str]) -> None:
    for i, nid in enumerate(ordered_ids):
        sc.conn.execute(
            text(
                "UPDATE workspace_nodes SET position = :p, updated_at = now() WHERE id = :i AND company_id = :c AND position <> :p"
            ),
            {"p": i, "i": nid, "c": sc.company_id},
        )


def reorder(sc: Scope, parent_id: uuid.UUID | None, ordered_ids: list[uuid.UUID]) -> None:
    sibs = [
        n["id"]
        for n in fetch_nodes(sc.conn, sc.company_id, include_archived=False)
        if n["parent_id"] == (str(parent_id) if parent_id else None)
    ]
    if parent_id:
        get_node(sc.conn, sc.company_id, parent_id)
    wanted = [str(i) for i in ordered_ids]
    if len(set(wanted)) != len(wanted) or sorted(wanted) != sorted(sibs):
        raise Unprocessable(
            "The order must list every active item at this level exactly once.", extra={"expected_ids": sibs}
        )
    _write_positions(sc, wanted)
    sc.record("node.reordered", "folder" if parent_id else "section", parent_id or "root", None, {"order": wanted})


def descendants(conn: Connection, company_id: uuid.UUID, node_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            f"SELECT {NODE_COLS}, path_ids FROM workspace_nodes WHERE company_id = :c AND path_ids @> ARRAY[CAST(:n AS uuid)] AND id <> :n ORDER BY depth, position"
        ),
        {"c": company_id, "n": node_id},
    )
    return [row_dict(r) for r in rows]


def impact(conn: Connection, company_id: uuid.UUID, node_id: uuid.UUID) -> dict[str, Any]:
    """Everything that references this node or its subtree. `documents` counts every non-purged document filed in the
    subtree (Phase 6); the foreign key from documents to workspace_nodes is RESTRICT, so such a node can only be archived."""
    ids = [node_id, *[uuid.UUID(d["id"]) for d in descendants(conn, company_id, node_id)]]

    def one(q: str) -> int:
        return int(conn.execute(text(q), {"c": company_id, "ids": ids}).scalar_one())

    return {
        "node_id": str(node_id),
        "subfolders": len(ids) - 1,
        "documents": one(
            "SELECT count(*) FROM documents WHERE company_id = :c AND node_id = ANY(:ids) AND purged_at IS NULL"
        ),
        "categories": one(
            "SELECT count(*) FROM document_categories WHERE company_id = :c AND node_id = ANY(:ids) AND archived_at IS NULL"
        ),
        "filing_rules": one(
            "SELECT count(*) FROM filing_rules WHERE company_id = :c AND target_node_id = ANY(:ids) AND archived_at IS NULL"
        ),
        "field_applicability": one(
            "SELECT count(*) FROM custom_field_applicability WHERE company_id = :c AND node_id = ANY(:ids)"
        ),
        "permission_grants": one(
            "SELECT count(*) FROM workspace_permissions WHERE company_id = :c AND node_id = ANY(:ids)"
        ),
    }


def archive_node(sc: Scope, node_id: uuid.UUID, confirmed: bool) -> dict[str, Any]:
    node = get_node(sc.conn, sc.company_id, node_id, lock=True)
    if node["archived_at"] is not None:
        raise Conflict("This item is already archived.")
    imp = impact(sc.conn, sc.company_id, node_id)
    if not confirmed and (imp["categories"] or imp["filing_rules"] or imp["subfolders"] or imp["documents"]):
        raise ConfirmationRequired(
            "Archiving hides this item and everything inside it from new filing. Existing content is kept.",
            extra={"impact": imp},
        )
    stamp = sc.conn.execute(text("SELECT now()")).scalar_one()
    sc.conn.execute(
        text(
            "UPDATE workspace_nodes SET archived_at = :t, archived_by = :u, updated_at = :t, updated_by = :u, row_version = row_version + 1"
            " WHERE company_id = :c AND archived_at IS NULL AND path_ids @> ARRAY[CAST(:n AS uuid)]"
        ),
        {"t": stamp, "u": sc.actor_id, "c": sc.company_id, "n": node_id},
    )
    # a filing rule must never point at something that cannot receive new filing
    sc.conn.execute(
        text(
            "UPDATE filing_rules SET is_active = false, updated_at = now(), updated_by = :u WHERE company_id = :c AND is_active"
            " AND target_node_id IN (SELECT id FROM workspace_nodes WHERE company_id = :c AND archived_at = :t)"
        ),
        {"u": sc.actor_id, "c": sc.company_id, "t": stamp},
    )
    sc.record(
        "node.archived", node["node_type"], node_id, {"archived_at": None}, {"archived_at": str(stamp), "impact": imp}
    )
    return get_node(sc.conn, sc.company_id, node_id)


def restore_node(sc: Scope, node_id: uuid.UUID) -> dict[str, Any]:
    node = get_node(sc.conn, sc.company_id, node_id, lock=True)
    if node["archived_at"] is None:
        raise Conflict("This item is not archived.")
    if node["parent_id"]:
        parent = get_node(sc.conn, sc.company_id, uuid.UUID(node["parent_id"]))
        if parent["archived_at"] is not None:
            raise Conflict("Restore the folder that contains this item first.", extra={"parent_id": node["parent_id"]})
    _assert_name_free(sc.conn, sc.company_id, node["parent_id"], node["name"], node_id)
    sc.conn.execute(
        text(
            "UPDATE workspace_nodes SET archived_at = NULL, archived_by = NULL, updated_at = now(), updated_by = :u, row_version = row_version + 1"
            " WHERE company_id = :c AND archived_at = (SELECT archived_at FROM workspace_nodes WHERE id = :n)"
            " AND path_ids @> ARRAY[CAST(:n AS uuid)]"
        ),
        {"u": sc.actor_id, "c": sc.company_id, "n": node_id},
    )
    sc.record("node.restored", node["node_type"], node_id, {"archived_at": node["archived_at"]}, {"archived_at": None})
    return get_node(sc.conn, sc.company_id, node_id)


def delete_node(sc: Scope, node_id: uuid.UUID) -> None:
    """Hard delete is only possible for a node nothing has ever referenced. Anything else must be archived: the
    database refuses (RESTRICT foreign keys) even if this pre-check were bypassed."""
    node = get_node(sc.conn, sc.company_id, node_id, lock=True)
    n_children = sc.conn.execute(
        text("SELECT count(*) FROM workspace_nodes WHERE parent_id = :n"), {"n": node_id}
    ).scalar_one()
    imp = impact(sc.conn, sc.company_id, node_id)
    archived_refs = sc.conn.execute(
        text(
            "SELECT (SELECT count(*) FROM document_categories WHERE node_id = :n) + (SELECT count(*) FROM filing_rules WHERE target_node_id = :n)"
            " + (SELECT count(*) FROM custom_field_applicability WHERE node_id = :n) + (SELECT count(*) FROM workspace_permissions WHERE node_id = :n)"
        ),
        {"n": node_id},
    ).scalar_one()
    if n_children or archived_refs or imp["documents"]:
        raise Conflict(
            "This item is in use (it contains items, documents, or is referenced by categories, rules, fields or permissions)."
            " It cannot be deleted. Archive it instead: its content is kept, and you can restore it.",
            extra={"code_detail": "node_in_use", "impact": imp, "options": ["archive", "move_contents"]},
        )
    try:
        with sc.conn.begin_nested():
            sc.conn.execute(
                text("DELETE FROM workspace_nodes WHERE id = :n AND company_id = :c"),
                {"n": node_id, "c": sc.company_id},
            )
    except IntegrityError as exc:
        raise Conflict(
            "This item is referenced by stored records and cannot be deleted. Archive it instead.",
            extra={"code_detail": "node_in_use"},
        ) from exc
    sc.record("node.deleted", node["node_type"], node_id, _node_brief(node), None)


def duplicate_node(sc: Scope, node_id: uuid.UUID, new_name: str | None) -> dict[str, Any]:
    """Copy a section or folder with all non-archived subfolders as a sibling. Structure only: categories, field
    scopes, rules and permissions are never copied, so the copy starts with no behaviour of its own."""
    src = get_node(sc.conn, sc.company_id, node_id, lock=True)
    if src["archived_at"] is not None:
        raise Conflict("Restore this item before duplicating it.")
    parent = get_node(sc.conn, sc.company_id, uuid.UUID(src["parent_id"])) if src["parent_id"] else None
    base = new_name or f"{src['name']} (copy)"
    try:
        clean = validate_node_name(base)
    except ConfigError as exc:
        raise bad(exc) from exc
    clean = unique_name(clean, _sibling_names(sc.conn, sc.company_id, src["parent_id"])) if not new_name else clean
    _assert_name_free(sc.conn, sc.company_id, src["parent_id"], clean)
    live = list(fetch_nodes(sc.conn, sc.company_id, include_archived=False))
    subtree = [n for n in live if str(node_id) in [str(p) for p in n["path_ids"]]]
    height = max(n["depth"] for n in subtree) - src["depth"] + 1
    if (parent["depth"] if parent else 0) + height > MAX_DEPTH_HARD:
        raise Unprocessable("The copy would be nested too deeply.")
    _guard_depth(sc, (parent["depth"] if parent else 0) + height)
    keys = _all_keys(sc.conn, sc.company_id)
    idmap: dict[str, dict[str, Any]] = {}
    next_pos = sc.conn.execute(
        text(
            "SELECT COALESCE(max(position), -1) + 1 FROM workspace_nodes WHERE company_id = :c AND parent_id IS NOT DISTINCT FROM CAST(:p AS uuid)"
        ),
        {"c": sc.company_id, "p": src["parent_id"]},
    ).scalar_one()
    for n in sorted(subtree, key=lambda x: (x["depth"], x["position"])):
        is_root = n["id"] == str(node_id)
        par = parent if is_root else idmap[n["parent_id"]]
        key = unique_key(slugify_key(clean if is_root else n["name"]), keys)
        keys.append(key)
        created = _insert_node(
            sc,
            node_id=new_id(),
            parent=par,
            name=clean if is_root else n["name"],
            display_name=n["display_name"],
            description=n["description"],
            config=n["config"],
            position=int(next_pos) if is_root else n["position"],
            key=key,
        )
        idmap[n["id"]] = created
    root = idmap[str(node_id)]
    sc.record("node.duplicated", root["node_type"], root["id"], {"source_id": str(node_id)}, {"nodes": len(idmap)})
    return root
