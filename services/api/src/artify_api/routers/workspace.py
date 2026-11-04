"""Workspace structure & customization API (Phase 4): sections/folders, categories, custom fields, naming rules,
filing rules, permissions, templates, history. Authorization is two-stage: the route requires `workspace.read` on the
company (organization role + scope), then each action is checked per node by the allow/deny engine
(`workspace_engine.decide`). Hidden nodes answer 404."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from artify_api import audit, entitlements
from artify_api import workspace as ws
from artify_api import workspace_config as wc
from artify_api import workspace_templates as wt
from artify_api.context import RequestCtx, Requires
from artify_api.errors import Conflict, Forbidden, NotFound, Unprocessable
from artify_api.permissions import SYSTEM_ROLES
from artify_api.policy import authorize
from artify_api.workspace_engine import (
    BUILTIN_TOKENS,
    DOCUMENT_KINDS,
    FIELD_TYPES,
    NODE_ACTIONS,
    AccessSubject,
    ConfigError,
    RuleDef,
    analyze_rules,
    decide,
    ensure_unique_name,
    evaluate_rules,
    fallback_name,
    parse_pattern,
    render_name,
    validate_values,
)
from artify_api.workspace_templates_data import TemplateDefinition

router = APIRouter(tags=["workspace"])
Read = Annotated[RequestCtx, Depends(Requires("workspace.read", company=True))]
OrgMember = Annotated[RequestCtx, Depends(Requires("companies.list"))]
TemplateAdmin = Annotated[RequestCtx, Depends(Requires("workspace_templates.manage"))]
Page = dict[str, Any]
_WS_KEYS = ("workspace.read", "workspace.manage", "workspace.permissions.manage")


# ---------------------------------------------------------------- helpers
def _base(ctx: RequestCtx) -> frozenset[str]:
    p = ctx.principal
    assert p is not None and ctx.company_id is not None
    perms = set(SYSTEM_ROLES[p.org_role][2]) if p.org_role else set()
    cr = p.company_roles.get(ctx.company_id)
    if cr:
        perms |= SYSTEM_ROLES[cr][2]
    return frozenset(k for k in _WS_KEYS if k in perms)


def access(ctx: RequestCtx) -> ws.Access:
    assert ctx.principal is not None and ctx.company_id is not None
    return ws.load_access(ctx.conn, ctx.company_id, ctx.user_id, ctx.principal.org_role, _base(ctx))


def begin(ctx: RequestCtx, base_version: int | None = None) -> ws.Scope:
    assert ctx.org_id is not None and ctx.company_id is not None
    return ws.begin_change(ctx.conn, ctx.org_id, ctx.company_id, ctx.user_id, ctx.meta, base_version)


def done(sc: ws.Scope, action: str, item: Any, entity: str | None = None, entity_id: Any = None) -> Page:
    version = ws.commit(sc, action, entity_type=entity, entity_id=entity_id)
    return {"version": version, "item": item}


def _etag(request: Request, response: Response, payload: Any) -> bool:
    tag = 'W/"' + hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:24] + '"'
    response.headers["ETag"] = tag
    return request.headers.get("if-none-match") == tag


def _node_out(n: dict[str, Any], acc: ws.Access | None = None, names: dict[str, str] | None = None) -> dict[str, Any]:
    out = {
        k: n[k]
        for k in (
            "id",
            "parent_id",
            "node_type",
            "key",
            "name",
            "display_name",
            "description",
            "position",
            "depth",
            "is_active",
            "archived_at",
            "config",
            "updated_at",
        )
    }
    out["label"] = n["display_name"] or n["name"]
    if acc is not None:
        out["can"] = {a: acc.can(a, n["id"]) for a in NODE_ACTIONS if a != "view"}
    return out


def visible_nodes(ctx: RequestCtx, acc: ws.Access, include_archived: bool) -> list[dict[str, Any]]:
    assert ctx.company_id is not None
    allow_arch = acc.can("configure")
    nodes = ws.fetch_nodes(ctx.conn, ctx.company_id, include_archived=include_archived)
    out = []
    for n in nodes:
        if not acc.visible(n["id"]):
            continue
        if n["archived_at"] is not None and not (allow_arch or acc.can("archive", n["id"])):
            continue
        out.append(n)
    return out


def _reroot(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A node whose parent the caller may not see is returned as a root: the hidden parent's id is not disclosed."""
    seen = {n["id"] for n in nodes}
    return [
        {**n, "parent_id": None, "detached": True} if n["parent_id"] and n["parent_id"] not in seen else n
        for n in nodes
    ]


def _cats(ctx: RequestCtx, acc: ws.Access, include_archived: bool) -> list[dict[str, Any]]:
    assert ctx.company_id is not None
    rows = [
        ws.row_dict(r)
        for r in ctx.conn.execute(
            text(f"SELECT {wc.CATEGORY_COLS} FROM document_categories WHERE company_id = :c ORDER BY lower(name)"),
            {"c": ctx.company_id},
        )
    ]
    return [
        r
        for r in rows
        if (include_archived or r["archived_at"] is None) and (r["node_id"] is None or acc.visible(r["node_id"]))
    ]


def _fields(ctx: RequestCtx, include_archived: bool) -> list[dict[str, Any]]:
    assert ctx.company_id is not None
    rows = [
        ws.row_dict(r)
        for r in ctx.conn.execute(
            text("SELECT * FROM custom_field_definitions WHERE company_id = :c ORDER BY display_order, created_at"),
            {"c": ctx.company_id},
        )
    ]
    return [
        wc.field_with_children(ctx.conn, ctx.company_id, r)
        for r in rows
        if include_archived or r["archived_at"] is None
    ]


def _rows(ctx: RequestCtx, table: str, order: str, include_archived: bool) -> list[dict[str, Any]]:
    assert ctx.company_id is not None
    rows = [
        ws.row_dict(r)
        for r in ctx.conn.execute(
            text(f"SELECT * FROM {table} WHERE company_id = :c ORDER BY {order}"), {"c": ctx.company_id}
        )
    ]
    return [
        {k: v for k, v in r.items() if k not in ("organization_id", "company_id")}
        for r in rows
        if include_archived or r["archived_at"] is None
    ]


# ---------------------------------------------------------------- overview and distribution
@router.get("/companies/{company_id}/workspace", operation_id="workspace_get")
def get_workspace(
    company_id: uuid.UUID, request: Request, response: Response, ctx: Read, include_archived: bool = False
) -> Any:
    """The caller's view of one company's workspace: only nodes they may view, with what they may do on each."""
    acc = access(ctx)
    cfg = ctx.conn.execute(
        text(
            "SELECT workspace_state, config_version, workspace_access_mode, applied_template_id, applied_template_version FROM company_configurations WHERE company_id = :c"
        ),
        {"c": company_id},
    ).one()
    can_cfg = acc.can("configure")
    body: dict[str, Any] = {
        "company_id": str(company_id),
        "version": cfg.config_version,
        "workspace_state": cfg.workspace_state,
        "access_mode": cfg.workspace_access_mode,
        "applied_template": {"template_id": str(cfg.applied_template_id), "version": cfg.applied_template_version}
        if cfg.applied_template_id
        else None,
        "nodes": _reroot([_node_out(n, acc) for n in visible_nodes(ctx, acc, include_archived)]),
        "categories": _cats(ctx, acc, include_archived),
        "fields": _fields(ctx, include_archived),
        "naming_rules": _rows(ctx, "naming_rules", "created_at, id", include_archived) if can_cfg else [],
        "filing_rules": _rows(ctx, "filing_rules", "priority, created_at, id", include_archived) if can_cfg else [],
        "capabilities": {
            "configure": can_cfg,
            "manage_permissions": acc.can("manage_permissions"),
            "templates": "workspace_templates.manage" in SYSTEM_ROLES[ctx.principal.org_role][2]
            if ctx.principal and ctx.principal.org_role
            else False,
        },
    }
    if _etag(request, response, body):
        return Response(status_code=304, headers={"ETag": response.headers["ETag"]})
    return body


@router.get("/companies/{company_id}/workspace/upload-config", operation_id="workspace_upload_config")
def upload_config(company_id: uuid.UUID, request: Request, response: Response, ctx: Read) -> Any:
    """Distribution contract for Desktop/Mobile: ONLY active, non-archived targets the caller may upload to, with the
    categories that file there. Nothing is hard-coded; an empty workspace returns empty lists."""
    acc = access(ctx)
    nodes = [
        n
        for n in ws.fetch_nodes(ctx.conn, company_id, include_archived=False)
        if n["is_active"] and acc.visible(n["id"]) and acc.can("upload", n["id"])
    ]
    ids = {n["id"] for n in nodes}
    cats = [c for c in _cats(ctx, acc, False) if c["is_active"] and (c["node_id"] is None or c["node_id"] in ids)]
    version = ws.current_version(ctx.conn, company_id)
    body = {
        "company_id": str(company_id),
        "version": version,
        "targets": [
            {
                "id": n["id"],
                "parent_id": n["parent_id"] if n["parent_id"] in ids else None,
                "node_type": n["node_type"],
                "key": n["key"],
                "label": n["display_name"] or n["name"],
                "position": n["position"],
                "depth": n["depth"],
            }
            for n in nodes
        ],
        "categories": [
            {
                "id": c["id"],
                "key": c["key"],
                "name": c["name"],
                "document_kind": c["document_kind"],
                "node_id": c["node_id"],
                "allowed_file_types": c["allowed_file_types"],
            }
            for c in cats
        ],
        "fields": [
            {
                "key": f["key"],
                "label": f["label"],
                "data_type": f["data_type"],
                "required": f["required"],
                "category_ids": f["category_ids"],
                "node_ids": f["node_ids"],
                "options": [o for o in f["options"] if o["archived_at"] is None],
            }
            for f in _fields(ctx, False)
            if f["is_active"]
        ],
    }
    if _etag(request, response, body):
        return Response(status_code=304, headers={"ETag": response.headers["ETag"]})
    return body


@router.get("/companies/{company_id}/workspace/usage", operation_id="workspace_usage")
def workspace_usage(company_id: uuid.UUID, ctx: Read) -> Any:
    assert ctx.org_id is not None
    return {
        "limits": entitlements.company_limits(ctx.conn, ctx.org_id, company_id),
        "templates": {
            "current": entitlements.measure(ctx.conn, ctx.org_id, "limit.workspace_templates"),
            "limit": entitlements.effective_limit(ctx.conn, ctx.org_id, "limit.workspace_templates").value,
        },
    }


class SettingsIn(BaseModel):
    access_mode: Literal["open", "restricted"]
    base_version: int | None = None


@router.patch("/companies/{company_id}/workspace/settings", operation_id="workspace_settings_update")
def update_settings(company_id: uuid.UUID, body: SettingsIn, ctx: Read) -> Page:
    access(ctx).require("manage_permissions")
    sc = begin(ctx, body.base_version)
    before = ctx.conn.execute(
        text("SELECT workspace_access_mode FROM company_configurations WHERE company_id = :c"), {"c": company_id}
    ).scalar_one()
    ctx.conn.execute(
        text("UPDATE company_configurations SET workspace_access_mode = :m WHERE company_id = :c"),
        {"m": body.access_mode, "c": company_id},
    )
    sc.record(
        "settings.access_mode", "workspace", company_id, {"access_mode": before}, {"access_mode": body.access_mode}
    )
    return done(sc, "settings_changed", {"access_mode": body.access_mode}, "workspace", company_id)


# ---------------------------------------------------------------- nodes
class NodeCreate(BaseModel):
    parent_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=120)
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    config: dict[str, Any] = Field(default_factory=dict)
    base_version: int | None = None


class NodeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    display_name: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    config: dict[str, Any] | None = None
    is_active: bool | None = None
    base_version: int | None = None


class MoveIn(BaseModel):
    parent_id: uuid.UUID
    position: int | None = Field(default=None, ge=0)
    base_version: int | None = None


class ReorderIn(BaseModel):
    parent_id: uuid.UUID | None = None
    ordered_ids: list[uuid.UUID] = Field(max_length=2000)
    base_version: int | None = None


class ArchiveIn(BaseModel):
    confirm_impact: bool = False
    base_version: int | None = None


class DuplicateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_version: int | None = None


def _check_json_size(value: Any) -> None:
    if len(json.dumps(value)) > 8000:
        raise Unprocessable("Settings are too large (8 KB maximum).")


@router.post("/companies/{company_id}/workspace/nodes", status_code=201, operation_id="workspace_node_create")
def create_node(company_id: uuid.UUID, body: NodeCreate, ctx: Read) -> Page:
    acc = access(ctx)
    if body.parent_id is None:
        acc.require("configure")
    else:
        acc.require("edit", body.parent_id)
    _check_json_size(body.config)
    sc = begin(ctx, body.base_version)
    node = ws.create_node(sc, body.parent_id, body.name, body.display_name, body.description, body.config)
    return done(sc, "node_created", _node_out(node), node["node_type"], node["id"])


@router.patch("/companies/{company_id}/workspace/nodes/{node_id}", operation_id="workspace_node_update")
def update_node(company_id: uuid.UUID, node_id: uuid.UUID, body: NodeUpdate, ctx: Read) -> Page:
    acc = access(ctx)
    patch = body.model_dump(exclude={"base_version"}, exclude_unset=True)
    acc.require("rename" if "name" in patch else "edit", node_id)
    if "name" in patch and len(patch) > 1:
        acc.require("edit", node_id)
    if patch.get("config") is not None:
        _check_json_size(patch["config"])
    sc = begin(ctx, body.base_version)
    node = ws.update_node(sc, node_id, patch)
    return done(sc, "node_renamed" if "name" in patch else "node_updated", _node_out(node), node["node_type"], node_id)


@router.post("/companies/{company_id}/workspace/nodes/{node_id}/move", operation_id="workspace_node_move")
def move_node(company_id: uuid.UUID, node_id: uuid.UUID, body: MoveIn, ctx: Read) -> Page:
    acc = access(ctx)
    acc.require("move", node_id)
    acc.require("edit", body.parent_id)
    sc = begin(ctx, body.base_version)
    node = ws.move_node(sc, node_id, body.parent_id, body.position)
    return done(sc, "node_moved", _node_out(node), "folder", node_id)


@router.post("/companies/{company_id}/workspace/nodes/reorder", operation_id="workspace_node_reorder")
def reorder_nodes(company_id: uuid.UUID, body: ReorderIn, ctx: Read) -> Page:
    acc = access(ctx)
    acc.require("configure") if body.parent_id is None else acc.require("edit", body.parent_id)
    sc = begin(ctx, body.base_version)
    ws.reorder(sc, body.parent_id, body.ordered_ids)
    return done(
        sc,
        "nodes_reordered",
        {"ordered_ids": [str(i) for i in body.ordered_ids]},
        "folder" if body.parent_id else "section",
        body.parent_id,
    )


@router.get("/companies/{company_id}/workspace/nodes/{node_id}/impact", operation_id="workspace_node_impact")
def node_impact(company_id: uuid.UUID, node_id: uuid.UUID, ctx: Read) -> Any:
    access(ctx).require("view", node_id)
    ws.get_node(ctx.conn, company_id, node_id)
    return ws.impact(ctx.conn, company_id, node_id)


@router.post("/companies/{company_id}/workspace/nodes/{node_id}/archive", operation_id="workspace_node_archive")
def archive_node(company_id: uuid.UUID, node_id: uuid.UUID, body: ArchiveIn, ctx: Read) -> Page:
    access(ctx).require("archive", node_id)
    sc = begin(ctx, body.base_version)
    node = ws.archive_node(sc, node_id, body.confirm_impact)
    return done(sc, "node_archived", _node_out(node), node["node_type"], node_id)


@router.post("/companies/{company_id}/workspace/nodes/{node_id}/restore", operation_id="workspace_node_restore")
def restore_node(company_id: uuid.UUID, node_id: uuid.UUID, body: ArchiveIn, ctx: Read) -> Page:
    access(ctx).require("archive", node_id)
    sc = begin(ctx, body.base_version)
    node = ws.restore_node(sc, node_id)
    return done(sc, "node_restored", _node_out(node), node["node_type"], node_id)


@router.delete("/companies/{company_id}/workspace/nodes/{node_id}", operation_id="workspace_node_delete")
def delete_node(company_id: uuid.UUID, node_id: uuid.UUID, ctx: Read, base_version: int | None = None) -> Page:
    access(ctx).require("delete", node_id)
    sc = begin(ctx, base_version)
    ws.delete_node(sc, node_id)
    return done(sc, "node_deleted", {"id": str(node_id)}, "node", node_id)


@router.post(
    "/companies/{company_id}/workspace/nodes/{node_id}/duplicate",
    status_code=201,
    operation_id="workspace_node_duplicate",
)
def duplicate_node(company_id: uuid.UUID, node_id: uuid.UUID, body: DuplicateIn, ctx: Read) -> Page:
    acc = access(ctx)
    n = ws.get_node(ctx.conn, company_id, node_id)
    acc.require("view", node_id)
    acc.require("configure") if n["parent_id"] is None else acc.require("edit", n["parent_id"])
    sc = begin(ctx, body.base_version)
    root = ws.duplicate_node(sc, node_id, body.name)
    return done(sc, "node_duplicated", _node_out(root), root["node_type"], root["id"])


# ---------------------------------------------------------------- categories
class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    document_kind: str
    accounting_domain: str | None = None
    processing_type: str | None = None
    allowed_file_types: list[str] = Field(default_factory=list, max_length=30)
    processing_settings: dict[str, Any] = Field(default_factory=dict)
    node_id: uuid.UUID | None = None
    is_active: bool = True
    base_version: int | None = None


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    document_kind: str | None = None
    processing_type: str | None = None
    allowed_file_types: list[str] | None = Field(default=None, max_length=30)
    processing_settings: dict[str, Any] | None = None
    node_id: uuid.UUID | None = None
    is_active: bool | None = None
    base_version: int | None = None


class VersionOnly(BaseModel):
    base_version: int | None = None


@router.get("/reference/document-kinds", operation_id="reference_document_kinds")
def document_kinds(
    ctx: Annotated[RequestCtx, Depends(Requires(reason="Artify-controlled reference catalog; no tenant data"))],
) -> Any:
    return {
        "items": [
            {
                "key": k.key,
                "name": k.name,
                "description": k.description,
                "accounting_domain": k.accounting_domain,
                "processing_type": k.processing_type,
                "creates_financial_records": k.creates_financial_records,
            }
            for k in DOCUMENT_KINDS
        ],
        "field_types": list(FIELD_TYPES),
        "naming_tokens": BUILTIN_TOKENS,
    }


@router.get("/companies/{company_id}/workspace/categories", operation_id="workspace_categories_list")
def list_categories(company_id: uuid.UUID, ctx: Read, include_archived: bool = False) -> Any:
    return {"items": _cats(ctx, access(ctx), include_archived)}


@router.post("/companies/{company_id}/workspace/categories", status_code=201, operation_id="workspace_category_create")
def create_category(company_id: uuid.UUID, body: CategoryIn, ctx: Read) -> Page:
    acc = access(ctx)
    acc.require("configure")
    if body.node_id:
        acc.require("edit", body.node_id)
    sc = begin(ctx, body.base_version)
    row = wc.create_category(sc, body.model_dump(exclude={"base_version"}))
    return done(sc, "category_created", row, "category", row["id"])


@router.patch("/companies/{company_id}/workspace/categories/{category_id}", operation_id="workspace_category_update")
def update_category(company_id: uuid.UUID, category_id: uuid.UUID, body: CategoryPatch, ctx: Read) -> Page:
    acc = access(ctx)
    acc.require("configure")
    d = body.model_dump(exclude={"base_version"}, exclude_unset=True)
    if d.get("node_id"):
        acc.require("edit", d["node_id"])
    sc = begin(ctx, body.base_version)
    row = wc.update_category(sc, category_id, d)
    return done(sc, "category_updated", row, "category", category_id)


@router.post(
    "/companies/{company_id}/workspace/categories/{category_id}/{action}", operation_id="workspace_category_archive"
)
def archive_category(
    company_id: uuid.UUID, category_id: uuid.UUID, action: Literal["archive", "restore"], body: VersionOnly, ctx: Read
) -> Page:
    access(ctx).require("configure")
    sc = begin(ctx, body.base_version)
    row = wc.set_archived(sc, "document_categories", "category", category_id, action == "archive")
    return done(sc, f"category_{action}d", row, "category", category_id)


# ---------------------------------------------------------------- custom fields
class OptionIn(BaseModel):
    value: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=160)


class FieldIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,47}$")
    description: str = Field(default="", max_length=1000)
    data_type: Literal[
        "short_text",
        "long_text",
        "number",
        "decimal",
        "currency",
        "date",
        "dropdown",
        "multi_select",
        "boolean",
        "reference",
    ]
    required: bool = False
    default_value: Any = None
    validation: dict[str, Any] = Field(default_factory=dict)
    reference_target: Literal["workspace_node", "document_category"] | None = None
    options: list[OptionIn] = Field(default_factory=list, max_length=200)
    category_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    node_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    display_order: int | None = None
    is_active: bool = True
    base_version: int | None = None


class FieldPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    required: bool | None = None
    default_value: Any = None
    validation: dict[str, Any] | None = None
    options: list[OptionIn] | None = Field(default=None, max_length=200)
    category_ids: list[uuid.UUID] | None = None
    node_ids: list[uuid.UUID] | None = None
    display_order: int | None = None
    is_active: bool | None = None
    base_version: int | None = None


@router.get("/companies/{company_id}/workspace/fields", operation_id="workspace_fields_list")
def list_fields(
    company_id: uuid.UUID,
    ctx: Read,
    category_id: uuid.UUID | None = None,
    node_id: uuid.UUID | None = None,
    include_archived: bool = False,
) -> Any:
    acc = access(ctx)
    if node_id:
        acc.require("view", node_id)
    if category_id or node_id:
        chain = [str(c) for c in acc.chain(str(node_id))] if node_id else []
        return {"items": wc.applicable_fields(ctx.conn, company_id, category_id, chain)}
    return {"items": _fields(ctx, include_archived)}


@router.post("/companies/{company_id}/workspace/fields", status_code=201, operation_id="workspace_field_create")
def create_field(company_id: uuid.UUID, body: FieldIn, ctx: Read) -> Page:
    access(ctx).require("configure")
    sc = begin(ctx, body.base_version)
    out = wc.create_field(sc, body.model_dump(exclude={"base_version"}))
    return done(sc, "field_created", out, "field", out["id"])


@router.patch("/companies/{company_id}/workspace/fields/{field_id}", operation_id="workspace_field_update")
def update_field(company_id: uuid.UUID, field_id: uuid.UUID, body: FieldPatch, ctx: Read) -> Page:
    access(ctx).require("configure")
    sc = begin(ctx, body.base_version)
    out = wc.update_field(sc, field_id, body.model_dump(exclude={"base_version"}, exclude_unset=True))
    return done(sc, "field_updated", out, "field", field_id)


@router.post("/companies/{company_id}/workspace/fields/{field_id}/{action}", operation_id="workspace_field_archive")
def archive_field(
    company_id: uuid.UUID, field_id: uuid.UUID, action: Literal["archive", "restore"], body: VersionOnly, ctx: Read
) -> Page:
    access(ctx).require("configure")
    sc = begin(ctx, body.base_version)
    row = wc.set_archived(sc, "custom_field_definitions", "field", field_id, action == "archive")
    return done(sc, f"field_{action}d", wc.field_with_children(ctx.conn, company_id, row), "field", field_id)


class ValidateValuesIn(BaseModel):
    category_id: uuid.UUID | None = None
    node_id: uuid.UUID | None = None
    values: dict[str, Any] = Field(max_length=200)


@router.post("/companies/{company_id}/workspace/fields/validate-values", operation_id="workspace_field_validate_values")
def validate_field_values(company_id: uuid.UUID, body: ValidateValuesIn, ctx: Read) -> Any:
    """Validate document metadata against the fields that apply to a category/folder. Nothing is stored."""
    acc = access(ctx)
    if body.node_id:
        acc.require("view", body.node_id)
    chain = [str(c) for c in acc.chain(str(body.node_id))] if body.node_id else []
    defs = [wc.to_field_def(f) for f in wc.applicable_fields(ctx.conn, company_id, body.category_id, chain)]
    normalized, problems = validate_values(defs, body.values, wc.ref_exists_fn(ctx.conn, company_id))
    return {"valid": not problems, "values": normalized, "problems": problems}


# ---------------------------------------------------------------- naming rules
class NamingIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    pattern: str = Field(min_length=1, max_length=300)
    category_id: uuid.UUID | None = None
    is_active: bool = True
    base_version: int | None = None


class NamingPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    pattern: str | None = Field(default=None, min_length=1, max_length=300)
    category_id: uuid.UUID | None = None
    is_active: bool | None = None
    base_version: int | None = None


class PatternIn(BaseModel):
    pattern: str = Field(min_length=1, max_length=300)


class PreviewIn(BaseModel):
    pattern: str | None = Field(default=None, max_length=300)
    category_id: uuid.UUID | None = None
    values: dict[str, Any] = Field(default_factory=dict, max_length=100)
    original_filename: str | None = Field(default=None, max_length=255)
    existing_names: list[str] = Field(default_factory=list, max_length=500)


@router.get("/companies/{company_id}/workspace/naming-rules", operation_id="workspace_naming_list")
def list_naming(company_id: uuid.UUID, ctx: Read, include_archived: bool = False) -> Any:
    access(ctx).require("configure")
    return {"items": _rows(ctx, "naming_rules", "created_at, id", include_archived)}


@router.post("/companies/{company_id}/workspace/naming-rules", status_code=201, operation_id="workspace_naming_create")
def create_naming(company_id: uuid.UUID, body: NamingIn, ctx: Read) -> Page:
    access(ctx).require("configure")
    sc = begin(ctx, body.base_version)
    row = wc.create_naming_rule(sc, body.model_dump(exclude={"base_version"}))
    return done(sc, "naming_rule_created", row, "naming_rule", row["id"])


@router.patch("/companies/{company_id}/workspace/naming-rules/{rule_id}", operation_id="workspace_naming_update")
def update_naming(company_id: uuid.UUID, rule_id: uuid.UUID, body: NamingPatch, ctx: Read) -> Page:
    access(ctx).require("configure")
    sc = begin(ctx, body.base_version)
    row = wc.update_naming_rule(sc, rule_id, body.model_dump(exclude={"base_version"}, exclude_unset=True))
    return done(sc, "naming_rule_updated", row, "naming_rule", rule_id)


@router.post("/companies/{company_id}/workspace/naming-rules/validate", operation_id="workspace_naming_validate")
def validate_naming(company_id: uuid.UUID, body: PatternIn, ctx: Read) -> Any:
    access(ctx).require("configure")
    try:
        parts = parse_pattern(body.pattern, wc.active_field_keys(ctx.conn, company_id))
    except ConfigError as exc:
        return {"valid": False, "error": str(exc), "tokens": []}
    return {"valid": True, "error": None, "tokens": [p.raw for p in parts if not isinstance(p, str)]}


@router.post("/companies/{company_id}/workspace/naming-rules/preview", operation_id="workspace_naming_preview")
def preview_naming(company_id: uuid.UUID, body: PreviewIn, ctx: Read) -> Any:
    """Render a file name with sample values. Storage and overwriting are Phase 6; this only shows what the rule would
    produce. The original file name is returned separately and never altered."""
    access(ctx).require("configure")
    pattern = body.pattern
    if pattern is None:
        row = (
            ctx.conn.execute(
                text(
                    "SELECT pattern FROM naming_rules WHERE company_id = :c AND archived_at IS NULL AND is_active AND category_id IS NOT DISTINCT FROM CAST(:cat AS uuid)"
                ),
                {"c": company_id, "cat": body.category_id},
            ).first()
            or ctx.conn.execute(
                text(
                    "SELECT pattern FROM naming_rules WHERE company_id = :c AND archived_at IS NULL AND is_active AND category_id IS NULL"
                ),
                {"c": company_id},
            ).first()
        )
        pattern = row.pattern if row else None
    ext = (
        body.original_filename.rpartition(".")[2] if body.original_filename and "." in body.original_filename else None
    )
    fy = ctx.conn.execute(
        text("SELECT financial_year_start_month, name, code FROM companies WHERE id = :c"), {"c": company_id}
    ).one()
    values = {"company": fy.name, "companycode": fy.code, **body.values}
    if pattern is None:
        name = fallback_name(
            values.get("documenttype") or values.get("DocumentType"),
            values.get("documentdate") or values.get("DocumentDate"),
            "00000000",
            ext,
        )
        used_fallback, warnings = True, ["No naming rule applies; the deterministic fallback name is used."]
    else:
        try:
            parts = parse_pattern(pattern, wc.active_field_keys(ctx.conn, company_id))
        except ConfigError as exc:
            raise Unprocessable(str(exc)) from exc
        name, warnings = render_name(
            parts, values, extension=ext, financial_year_start_month=fy.financial_year_start_month
        )
        used_fallback = False
    final = ensure_unique_name(name, [n.lower() for n in body.existing_names])
    return {
        "original_filename": body.original_filename,
        "generated_filename": final,
        "pattern": pattern,
        "used_fallback": used_fallback,
        "renamed_to_avoid_overwrite": final != name,
        "warnings": warnings,
    }


@router.post(
    "/companies/{company_id}/workspace/naming-rules/{rule_id}/{action}", operation_id="workspace_naming_archive"
)
def archive_naming(
    company_id: uuid.UUID, rule_id: uuid.UUID, action: Literal["archive", "restore"], body: VersionOnly, ctx: Read
) -> Page:
    access(ctx).require("configure")
    sc = begin(ctx, body.base_version)
    row = wc.set_archived(sc, "naming_rules", "naming_rule", rule_id, action == "archive")
    return done(sc, f"naming_rule_{action}d", row, "naming_rule", rule_id)


# ---------------------------------------------------------------- filing rules
class FilingIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    priority: int | None = Field(default=None, ge=1, le=100000)
    conditions: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    target_node_id: uuid.UUID
    target_category_id: uuid.UUID | None = None
    is_active: bool = True
    base_version: int | None = None


class FilingPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    priority: int | None = Field(default=None, ge=1, le=100000)
    conditions: list[dict[str, Any]] | None = Field(default=None, min_length=1, max_length=20)
    target_node_id: uuid.UUID | None = None
    target_category_id: uuid.UUID | None = None
    is_active: bool | None = None
    base_version: int | None = None


class FilingReorder(BaseModel):
    ordered_ids: list[uuid.UUID] = Field(max_length=1000)
    base_version: int | None = None


class FilingCheck(BaseModel):
    conditions: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    target_node_id: uuid.UUID
    target_category_id: uuid.UUID | None = None


class EvaluateIn(BaseModel):
    attributes: dict[str, Any] = Field(max_length=100)


@router.get("/companies/{company_id}/workspace/filing-rules", operation_id="workspace_filing_list")
def list_filing(company_id: uuid.UUID, ctx: Read, include_archived: bool = False) -> Any:
    access(ctx).require("configure")
    return {"items": _rows(ctx, "filing_rules", "priority, created_at, id", include_archived)}


@router.post("/companies/{company_id}/workspace/filing-rules", status_code=201, operation_id="workspace_filing_create")
def create_filing(company_id: uuid.UUID, body: FilingIn, ctx: Read) -> Page:
    access(ctx).require("configure")
    sc = begin(ctx, body.base_version)
    row = wc.create_filing_rule(sc, body.model_dump(exclude={"base_version"}))
    return done(sc, "filing_rule_created", row, "filing_rule", row["id"])


@router.patch("/companies/{company_id}/workspace/filing-rules/{rule_id}", operation_id="workspace_filing_update")
def update_filing(company_id: uuid.UUID, rule_id: uuid.UUID, body: FilingPatch, ctx: Read) -> Page:
    access(ctx).require("configure")
    sc = begin(ctx, body.base_version)
    row = wc.update_filing_rule(sc, rule_id, body.model_dump(exclude={"base_version"}, exclude_unset=True))
    return done(sc, "filing_rule_updated", row, "filing_rule", rule_id)


@router.post("/companies/{company_id}/workspace/filing-rules/reorder", operation_id="workspace_filing_reorder")
def reorder_filing(company_id: uuid.UUID, body: FilingReorder, ctx: Read) -> Page:
    access(ctx).require("configure")
    sc = begin(ctx, body.base_version)
    wc.reorder_filing_rules(sc, body.ordered_ids)
    return done(sc, "filing_rules_reordered", {"ordered_ids": [str(i) for i in body.ordered_ids]}, "filing_rule", None)


@router.post("/companies/{company_id}/workspace/filing-rules/validate", operation_id="workspace_filing_validate")
def validate_filing(company_id: uuid.UUID, body: FilingCheck, ctx: Read) -> Any:
    access(ctx).require("configure")
    try:
        wc.check_filing(ctx.conn, company_id, body.conditions, body.target_node_id, body.target_category_id)
    except (Unprocessable, Conflict, NotFound) as exc:
        return {"valid": False, "error": exc.detail}
    return {"valid": True, "error": None}


@router.post("/companies/{company_id}/workspace/filing-rules/evaluate", operation_id="workspace_filing_evaluate")
def evaluate_filing(company_id: uuid.UUID, body: EvaluateIn, ctx: Read) -> Any:
    """Dry run: which rule would win for these attributes? Nothing is filed or moved (that arrives with documents)."""
    access(ctx).require("configure")
    winner, hits = evaluate_rules(wc.rule_defs(ctx.conn, company_id), body.attributes)

    def out(r: RuleDef) -> dict[str, Any]:
        return {
            "rule_id": str(r.id),
            "name": r.name,
            "priority": r.priority,
            "target_node_id": str(r.target_node_id),
            "target_category_id": str(r.target_category_id) if r.target_category_id else None,
        }

    return {
        "winner": out(winner) if winner else None,
        "matches": [out(r) for r in hits],
        "ambiguous": len({(r.target_node_id, r.target_category_id) for r in hits}) > 1,
    }


@router.get("/companies/{company_id}/workspace/filing-rules/analysis", operation_id="workspace_filing_analysis")
def analyze_filing(company_id: uuid.UUID, ctx: Read) -> Any:
    access(ctx).require("configure")
    return {"findings": analyze_rules(wc.rule_defs(ctx.conn, company_id, only_live_targets=False))}


@router.post(
    "/companies/{company_id}/workspace/filing-rules/{rule_id}/{action}", operation_id="workspace_filing_archive"
)
def archive_filing(
    company_id: uuid.UUID, rule_id: uuid.UUID, action: Literal["archive", "restore"], body: VersionOnly, ctx: Read
) -> Page:
    access(ctx).require("configure")
    sc = begin(ctx, body.base_version)
    row = wc.set_archived(sc, "filing_rules", "filing_rule", rule_id, action == "archive")
    return done(sc, f"filing_rule_{action}d", row, "filing_rule", rule_id)


# ---------------------------------------------------------------- permissions
class GrantIn(BaseModel):
    node_id: uuid.UUID | None = None
    subject_type: Literal["user", "role"]
    user_id: uuid.UUID | None = None
    role: str | None = None
    permission: Literal[
        "view", "upload", "edit", "rename", "move", "archive", "delete", "configure", "manage_permissions"
    ]
    effect: Literal["allow", "deny"]
    base_version: int | None = None


class CheckIn(BaseModel):
    user_id: uuid.UUID
    node_id: uuid.UUID | None = None
    action: Literal["view", "upload", "edit", "rename", "move", "archive", "delete", "configure", "manage_permissions"]


@router.get("/companies/{company_id}/workspace/permissions", operation_id="workspace_permissions_list")
def list_permissions(company_id: uuid.UUID, ctx: Read) -> Any:
    access(ctx).require("manage_permissions")
    rows = ctx.conn.execute(
        text(
            "SELECT p.id, p.node_id, p.subject_type, p.subject_user_id, p.subject_role, p.permission, p.effect, p.created_at, u.display_name AS user_name, n.name AS node_name"
            " FROM workspace_permissions p LEFT JOIN users u ON u.id = p.subject_user_id LEFT JOIN workspace_nodes n ON n.id = p.node_id WHERE p.company_id = :c ORDER BY p.created_at, p.id"
        ),
        {"c": company_id},
    )
    mode = ctx.conn.execute(
        text("SELECT workspace_access_mode FROM company_configurations WHERE company_id = :c"), {"c": company_id}
    ).scalar_one()
    return {"access_mode": mode, "items": [ws.row_dict(r) for r in rows]}


@router.post("/companies/{company_id}/workspace/permissions", status_code=201, operation_id="workspace_permission_set")
def set_permission(company_id: uuid.UUID, body: GrantIn, ctx: Read) -> Page:
    acc = access(ctx)
    acc.require("manage_permissions", body.node_id)
    if (body.subject_type == "user") != (body.user_id is not None) or (body.subject_type == "role") != (
        body.role is not None
    ):
        raise Unprocessable("A user grant needs user_id; a role grant needs role.")
    sc = begin(ctx, body.base_version)
    row = wc.set_grant(sc, body.node_id, body.subject_type, body.user_id, body.role, body.permission, body.effect)
    return done(sc, "permission_set", row, "permission", row["id"])


@router.delete("/companies/{company_id}/workspace/permissions/{grant_id}", operation_id="workspace_permission_remove")
def remove_permission(company_id: uuid.UUID, grant_id: uuid.UUID, ctx: Read, base_version: int | None = None) -> Page:
    acc = access(ctx)
    g = ctx.conn.execute(
        text("SELECT node_id FROM workspace_permissions WHERE id = :g AND company_id = :c"),
        {"g": grant_id, "c": company_id},
    ).first()
    if g is None:
        raise NotFound("Grant not found.")
    acc.require("manage_permissions", g.node_id)
    sc = begin(ctx, base_version)
    wc.remove_grant(sc, grant_id)
    return done(sc, "permission_removed", {"id": str(grant_id)}, "permission", grant_id)


@router.get("/companies/{company_id}/workspace/access/me", operation_id="workspace_access_me")
def my_access(company_id: uuid.UUID, ctx: Read) -> Any:
    acc = access(ctx)
    return {
        "company": {a: acc.can(a) for a in NODE_ACTIONS},
        "nodes": {
            n["id"]: {a: acc.can(a, n["id"]) for a in NODE_ACTIONS}
            for n in ws.fetch_nodes(ctx.conn, company_id, include_archived=False)
            if acc.visible(n["id"])
        },
    }


@router.post("/companies/{company_id}/workspace/permissions/check", operation_id="workspace_permission_check")
def check_permission(company_id: uuid.UUID, body: CheckIn, ctx: Read) -> Any:
    """Explain what another member could do (administrators use it to verify a configuration)."""
    acc = access(ctx)
    acc.require("manage_permissions")
    assert ctx.org_id is not None
    m = ctx.conn.execute(
        text(
            "SELECT r.key FROM organization_memberships m JOIN roles r ON r.id = m.role_id WHERE m.organization_id = :o AND m.user_id = :u AND m.status = 'active'"
        ),
        {"o": ctx.org_id, "u": body.user_id},
    ).first()
    if m is None:
        raise NotFound("Member not found.")
    perms = SYSTEM_ROLES[m.key][2]
    reachable = (
        m.key in ("firm_owner", "practice_admin", "practice_manager")
        or ctx.conn.execute(
            text(
                "SELECT 1 FROM accountant_assignments WHERE company_id = :c AND user_id = :u AND starts_on <= current_date AND (ends_on IS NULL OR ends_on >= current_date)"
            ),
            {"c": company_id, "u": body.user_id},
        ).first()
        is not None
    )
    if not reachable or "workspace.read" not in perms:
        return {"allowed": False, "reason": "no_company_access"}
    if body.node_id:
        acc.require("view", body.node_id)
    subject = AccessSubject(body.user_id, m.key, frozenset(k for k in _WS_KEYS if k in perms), acc.subject.access_mode)
    chain = acc.chain(str(body.node_id)) if body.node_id else []
    allowed, reason = decide(subject, chain, acc.grants, body.action)
    return {"allowed": allowed, "reason": reason}


# ---------------------------------------------------------------- templates
class TemplateApplyIn(BaseModel):
    template_id: uuid.UUID
    version: int | None = Field(default=None, ge=1)
    mode: Literal["add", "replace"] = "add"
    confirm_replace: bool = False
    dry_run: bool = False
    base_version: int | None = None


class CopyFromIn(BaseModel):
    source_company_id: uuid.UUID
    mode: Literal["add", "replace"] = "add"
    confirm_replace: bool = False
    dry_run: bool = False
    base_version: int | None = None


class TemplateCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    source_company_id: uuid.UUID | None = None
    definition: dict[str, Any] | None = None


class TemplateVersionIn(BaseModel):
    source_company_id: uuid.UUID | None = None
    definition: dict[str, Any] | None = None
    changelog: str = Field(default="", max_length=1000)


def _definition(body_def: dict[str, Any] | None) -> TemplateDefinition:
    try:
        d = TemplateDefinition.model_validate(body_def or {})
        d.check()
    except ConfigError as exc:
        raise Unprocessable(str(exc)) from exc
    except ValueError as exc:
        raise Unprocessable(f"The template definition is not valid: {exc}") from exc
    return d


def _source_definition(ctx: RequestCtx, company_id: uuid.UUID) -> tuple[TemplateDefinition, list[str]]:
    """A company's configuration as a definition. The caller needs workspace.read and company reach for that company."""
    assert ctx.principal is not None
    if not authorize(ctx.principal, "workspace.read", company_id).allowed:
        raise NotFound("Company not found.")
    exists = ctx.conn.execute(text("SELECT 1 FROM companies WHERE id = :c"), {"c": company_id}).scalar()
    if not exists:
        raise NotFound("Company not found.")
    return wt.export_definition(ctx.conn, company_id)


@router.get("/orgs/{org_id}/workspace-templates", operation_id="workspace_templates_list")
def list_templates(org_id: uuid.UUID, ctx: OrgMember) -> Any:
    return {"items": wt.list_templates(ctx.conn)}


@router.get("/orgs/{org_id}/workspace-templates/{template_id}", operation_id="workspace_template_get")
def get_template(org_id: uuid.UUID, template_id: uuid.UUID, ctx: OrgMember, version: int | None = None) -> Any:
    t, v, definition = wt.get_version(ctx.conn, template_id, version)
    d = TemplateDefinition.model_validate(definition)
    return {
        "template": t,
        "version": v,
        "definition": definition,
        "counts": d.counts(),
        "versions": wt.template_versions(ctx.conn, template_id),
    }


@router.post("/orgs/{org_id}/workspace-templates", status_code=201, operation_id="workspace_template_create")
def create_template(org_id: uuid.UUID, body: TemplateCreateIn, ctx: TemplateAdmin) -> Any:
    """Save a company's workspace (or a supplied definition) as a reusable organization template. Configuration only."""
    warnings: list[str] = []
    if body.source_company_id:
        definition, warnings = _source_definition(ctx, body.source_company_id)
    else:
        definition = _definition(body.definition)
    created = wt.create_template(
        ctx.conn, org_id, ctx.user_id, body.name.strip(), body.description, definition, body.source_company_id
    )
    audit.write_audit(
        ctx.conn,
        action="workspace_template.created",
        actor_id=ctx.user_id,
        org_id=org_id,
        entity_type="workspace_template",
        entity_id=created["id"],
        summary={
            "name": body.name,
            "source_company_id": str(body.source_company_id) if body.source_company_id else None,
        },
        meta=ctx.meta,
    )
    return {**created, "warnings": warnings}


@router.post(
    "/orgs/{org_id}/workspace-templates/{template_id}/versions",
    status_code=201,
    operation_id="workspace_template_version_create",
)
def create_template_version(
    org_id: uuid.UUID, template_id: uuid.UUID, body: TemplateVersionIn, ctx: TemplateAdmin
) -> Any:
    warnings: list[str] = []
    if body.source_company_id:
        definition, warnings = _source_definition(ctx, body.source_company_id)
    else:
        definition = _definition(body.definition)
    v = wt.add_version(ctx.conn, org_id, ctx.user_id, template_id, definition, body.changelog)
    audit.write_audit(
        ctx.conn,
        action="workspace_template.version_created",
        actor_id=ctx.user_id,
        org_id=org_id,
        entity_type="workspace_template",
        entity_id=template_id,
        summary={"version": v},
        meta=ctx.meta,
    )
    return {"version": v, "warnings": warnings}


@router.post("/orgs/{org_id}/workspace-templates/{template_id}/archive", operation_id="workspace_template_archive")
def archive_template(org_id: uuid.UUID, template_id: uuid.UUID, ctx: TemplateAdmin) -> Any:
    row = ctx.conn.execute(
        text("SELECT organization_id, archived_at FROM workspace_templates WHERE id = :t"), {"t": template_id}
    ).first()
    if row is None or row.archived_at is not None:
        raise NotFound("Template not found.")
    if row.organization_id is None:
        raise Conflict("Artify system templates cannot be archived.")
    ctx.conn.execute(text("UPDATE workspace_templates SET archived_at = now() WHERE id = :t"), {"t": template_id})
    entitlements.refresh_counter(ctx.conn, org_id, "limit.workspace_templates")
    audit.write_audit(
        ctx.conn,
        action="workspace_template.archived",
        actor_id=ctx.user_id,
        org_id=org_id,
        entity_type="workspace_template",
        entity_id=template_id,
        meta=ctx.meta,
    )
    return {"id": str(template_id), "archived": True}


def run_apply(
    ctx: RequestCtx,
    company_id: uuid.UUID,
    definition: TemplateDefinition,
    mode: str,
    confirm_replace: bool,
    dry_run: bool,
    base_version: int | None,
    provenance: tuple[uuid.UUID, int] | None,
) -> Any:
    assert ctx.org_id is not None
    access(ctx).require("configure")
    if mode == "replace" and not confirm_replace and not dry_run:
        raise ws.ConfirmationRequired(
            "Replacing archives every current section, folder, category, field and rule (nothing is deleted and documents stay where they are). Confirm to continue.",
            extra={"counts": definition.counts()},
        )
    fit = wt.fit_report(ctx.conn, ctx.org_id, company_id, definition, mode)
    if dry_run:
        return {"dry_run": True, "counts": definition.counts(), "fit": fit, "fits": all(f["fits"] for f in fit)}
    sc = begin(ctx, base_version)
    result = wt.apply_definition(sc, definition, mode)
    if provenance:
        ctx.conn.execute(
            text(
                "UPDATE company_configurations SET applied_template_id = :t, applied_template_version = :v WHERE company_id = :c"
            ),
            {"t": provenance[0], "v": provenance[1], "c": company_id},
        )
    return done(sc, "template_applied", result, "workspace", company_id)


@router.post("/companies/{company_id}/workspace/templates/apply", operation_id="workspace_template_apply")
def apply_template(company_id: uuid.UUID, body: TemplateApplyIn, ctx: Read) -> Any:
    """Deep-copy a template version into this company with new ids. The company keeps only a provenance note: later
    template changes never reach it."""
    access(ctx).require("configure")
    _t, v, definition = wt.get_version(ctx.conn, body.template_id, body.version)
    return run_apply(
        ctx,
        company_id,
        TemplateDefinition.model_validate(definition),
        body.mode,
        body.confirm_replace,
        body.dry_run,
        body.base_version,
        (body.template_id, v),
    )


@router.post("/companies/{company_id}/workspace/copy-from", operation_id="workspace_copy_from")
def copy_from_company(company_id: uuid.UUID, body: CopyFromIn, ctx: Read) -> Any:
    """Copy another company's CONFIGURATION (structure, categories, fields, naming and filing rules). Never documents,
    users, permissions or money. The copy is independent immediately."""
    if body.source_company_id == company_id:
        raise Unprocessable("Choose a different company to copy from.")
    definition, warnings = _source_definition(ctx, body.source_company_id)
    out = run_apply(ctx, company_id, definition, body.mode, body.confirm_replace, body.dry_run, body.base_version, None)
    out["warnings"] = warnings
    return out


# ---------------------------------------------------------------- history
@router.get("/companies/{company_id}/workspace/revisions", operation_id="workspace_revisions_list")
def list_revisions(
    company_id: uuid.UUID, ctx: Read, limit: int = Query(30, ge=1, le=200), offset: int = Query(0, ge=0)
) -> Any:
    access(ctx).require("configure")
    total = ctx.conn.execute(
        text("SELECT count(*) FROM workspace_revisions WHERE company_id = :c"), {"c": company_id}
    ).scalar_one()
    rows = ctx.conn.execute(
        text(
            "SELECT r.version, r.action, r.created_at, r.actor_id, u.display_name AS actor_name, jsonb_array_length(r.changes) AS change_count"
            " FROM workspace_revisions r LEFT JOIN users u ON u.id = r.actor_id WHERE r.company_id = :c ORDER BY r.version DESC LIMIT :l OFFSET :o"
        ),
        {"c": company_id, "l": limit, "o": offset},
    )
    return {"items": [ws.row_dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/companies/{company_id}/workspace/revisions/{version}", operation_id="workspace_revision_get")
def get_revision(company_id: uuid.UUID, version: int, ctx: Read, include_snapshot: bool = False) -> Any:
    access(ctx).require("configure")
    r = ctx.conn.execute(
        text(
            "SELECT r.version, r.action, r.created_at, r.actor_id, u.display_name AS actor_name, r.changes, r.snapshot FROM workspace_revisions r"
            " LEFT JOIN users u ON u.id = r.actor_id WHERE r.company_id = :c AND r.version = :v"
        ),
        {"c": company_id, "v": version},
    ).first()
    if r is None:
        raise NotFound("Revision not found.")
    out = ws.row_dict(r)
    snap = out.pop("snapshot")
    out["summary"] = {k: len(v) for k, v in snap.items() if isinstance(v, list)}
    if include_snapshot:
        out["snapshot"] = snap
    return out


class RestoreIn(BaseModel):
    dry_run: bool = False
    base_version: int | None = None


@router.post("/companies/{company_id}/workspace/revisions/{version}/restore", operation_id="workspace_revision_restore")
def restore_revision(company_id: uuid.UUID, version: int, body: RestoreIn, ctx: Read) -> Any:
    """Bring structure, categories, fields and rules back to a recorded revision WITHOUT deleting anything (later items
    are archived). Permission grants are not restored. `dry_run` reports the effect and changes nothing."""
    access(ctx).require("configure")
    sc = begin(ctx, body.base_version)
    if body.dry_run:
        sp = ctx.conn.begin_nested()
        try:
            stats = wt.restore_revision(sc, version)
        finally:
            sp.rollback()
        return {"dry_run": True, "effect": stats}
    stats = wt.restore_revision(sc, version)
    return done(sc, "restored", {"from_version": version, "effect": stats}, "workspace", company_id)


@router.get("/companies/{company_id}/workspace/audit", operation_id="workspace_audit_list")
def workspace_audit(
    company_id: uuid.UUID, ctx: Read, limit: int = Query(30, ge=1, le=200), offset: int = Query(0, ge=0)
) -> Any:
    access(ctx).require("configure")
    rows = ctx.conn.execute(
        text(
            "SELECT id, action, actor_id, entity_type, entity_id, summary, created_at FROM audit_events WHERE company_id = :c AND action LIKE 'workspace.%' ORDER BY created_at DESC, id DESC LIMIT :l OFFSET :o"
        ),
        {"c": company_id, "l": limit, "o": offset},
    )
    return {"items": [ws.row_dict(r) for r in rows], "limit": limit, "offset": offset}


def apply_at_creation(ctx: RequestCtx, org_id: uuid.UUID, company_id: uuid.UUID, setup: Any) -> None:
    """Company onboarding: apply the chosen starting workspace inside the company-creation transaction."""
    assert ctx.principal is not None
    if "workspace.manage" not in SYSTEM_ROLES[ctx.principal.org_role or ""][2]:
        raise Forbidden("Your role cannot set up a company's workspace.")
    if setup.mode == "template":
        _t, version, raw = wt.get_version(ctx.conn, setup.template_id, setup.template_version)
        definition, provenance = TemplateDefinition.model_validate(raw), (setup.template_id, version)
    else:
        if setup.source_company_id == company_id:
            raise Unprocessable("Choose a different company to copy from.")
        definition, _warnings = _source_definition(ctx, setup.source_company_id)
        provenance = None
    sc = ws.begin_change(ctx.conn, org_id, company_id, ctx.user_id, ctx.meta)
    wt.apply_definition(sc, definition, "add")
    if provenance:
        ctx.conn.execute(
            text(
                "UPDATE company_configurations SET applied_template_id = :t, applied_template_version = :v WHERE company_id = :c"
            ),
            {"t": provenance[0], "v": provenance[1], "c": company_id},
        )
    ws.commit(
        sc, "template_applied" if provenance else "copied_from_company", entity_type="workspace", entity_id=company_id
    )
