"""Excel template and reporting API (Phase 9).

Every route declares its permission. Company routes use company scope (out-of-scope companies answer 404); the organization
overview needs `excel.monitor` and returns no file content. Template files are streamed in (raw body) and out (authorized
download); nothing is ever served from a public location."""

from __future__ import annotations

import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from artify_api import excel as X
from artify_api.context import RequestCtx, Requires
from artify_api.errors import Unprocessable
from artify_api.settings import get_settings

router = APIRouter(tags=["excel"])
C = "/companies/{company_id}/excel"
Read = Annotated[RequestCtx, Depends(Requires("excel.read", company=True))]
Manage = Annotated[RequestCtx, Depends(Requires("excel.templates.manage", company=True))]
Export = Annotated[RequestCtx, Depends(Requires("excel.export", company=True))]
Configure = Annotated[RequestCtx, Depends(Requires("excel.reports.configure", company=True))]
Monitor = Annotated[RequestCtx, Depends(Requires("excel.monitor", org_source="header"))]
Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]
Key = Field(min_length=8, max_length=128)


def _disposition(filename: str) -> str:
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", filename)[:150] or "workbook"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


# ------------------------------------------------------------------ catalogue
@router.get(C + "/catalogue", operation_id="excel_catalogue")
def catalogue(company_id: uuid.UUID, ctx: Read) -> Any:
    """Governed datasets, fields, filters, header values and the safe calculation list. There is no free-form SQL."""
    return X.catalogue()


# ------------------------------------------------------------------ templates
class TemplatePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    category: str | None = Field(default=None, min_length=2, max_length=60)
    base_row_version: int | None = None


class TemplateAction(BaseModel):
    action: Literal["activate", "deactivate", "archive", "restore"]


class DuplicateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.get(C + "/templates", operation_id="excel_templates")
def templates(
    company_id: uuid.UUID,
    ctx: Read,
    status: str | None = None,
    category: str | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> Any:
    return X.list_templates(ctx, status=status, category=category, q=q)


@router.put(C + "/templates/upload", status_code=201, operation_id="excel_template_upload")
async def upload_template(
    company_id: uuid.UUID,
    request: Request,
    ctx: Manage,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    name: Annotated[str | None, Query(max_length=120)] = None,
    category: Annotated[str | None, Query(max_length=60)] = None,
    description: Annotated[str | None, Query(max_length=1000)] = None,
    template_id: uuid.UUID | None = None,
    change_note: Annotated[str | None, Query(max_length=500)] = None,
) -> Any:
    """Register a workbook (raw body) as a new template, or as a new version of `template_id`. The file is size-capped while
    streaming, inspected without executing anything, analysed and stored; macros are refused unless the plan allows them."""
    cap = get_settings().excel_max_template_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise Unprocessable("The workbook is larger than the allowed size.", extra={"code": "file_too_large"})
    n = 0
    with tempfile.TemporaryDirectory(dir=get_settings().storage_tmp_dir) as td:
        tmp = Path(td) / "upload.bin"
        with tmp.open("wb") as f:
            async for chunk in request.stream():
                n += len(chunk)
                if n > cap:
                    raise Unprocessable(
                        "The workbook is larger than the allowed size.", extra={"code": "file_too_large"}
                    )
                f.write(chunk)
        return await run_in_threadpool(
            X.upload_template,
            ctx,
            tmp,
            filename=filename,
            name=name,
            category=category,
            description=description,
            template_id=template_id,
            change_note=change_note,
        )


@router.get(C + "/templates/{template_id}", operation_id="excel_template")
def template(company_id: uuid.UUID, template_id: uuid.UUID, ctx: Read) -> Any:
    return X.get_template(ctx, template_id)


@router.patch(C + "/templates/{template_id}", operation_id="excel_template_update")
def update_template(company_id: uuid.UUID, template_id: uuid.UUID, body: TemplatePatch, ctx: Manage) -> Any:
    patch = body.model_dump(exclude_unset=True)
    base = patch.pop("base_row_version", None)
    return X.update_template(ctx, template_id, patch, base)


@router.post(C + "/templates/{template_id}/status", operation_id="excel_template_status")
def template_status(company_id: uuid.UUID, template_id: uuid.UUID, body: TemplateAction, ctx: Manage) -> Any:
    return X.set_template_status(ctx, template_id, body.action)


@router.post(C + "/templates/{template_id}/duplicate", status_code=201, operation_id="excel_template_duplicate")
def duplicate_template(company_id: uuid.UUID, template_id: uuid.UUID, body: DuplicateIn, ctx: Manage) -> Any:
    return X.duplicate_template(ctx, template_id, body.name)


@router.delete(C + "/templates/{template_id}", operation_id="excel_template_delete")
def delete_template(company_id: uuid.UUID, template_id: uuid.UUID, ctx: Manage) -> Any:
    return X.delete_template(ctx, template_id)


@router.get(C + "/templates/{template_id}/analysis", operation_id="excel_template_analysis")
def template_analysis(
    company_id: uuid.UUID, template_id: uuid.UUID, ctx: Read, version: Annotated[int | None, Query(ge=1)] = None
) -> Any:
    return X.analysis_of(ctx, template_id, version)


@router.get(C + "/templates/{template_id}/versions/{version_no}/file", operation_id="excel_template_file")
def template_file(company_id: uuid.UUID, template_id: uuid.UUID, version_no: int, ctx: Read) -> Any:
    stream, filename, size, media = X.download_version(ctx, template_id, version_no)
    return StreamingResponse(
        stream,
        media_type=media,
        headers={
            "Content-Disposition": _disposition(filename),
            "Content-Length": str(size),
            "Cache-Control": "private, no-store",
        },
    )


# ------------------------------------------------------------------ mappings
class MappingIn(BaseModel):
    definition: dict[str, Any]
    note: str | None = Field(default=None, max_length=500)
    mapping_id: uuid.UUID | None = None


class ActivateIn(BaseModel):
    acknowledge_risks: bool = False


class ParamsIn(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


@router.get(C + "/templates/{template_id}/mappings", operation_id="excel_mappings")
def mappings(company_id: uuid.UUID, template_id: uuid.UUID, ctx: Read) -> Any:
    return X.list_mappings(ctx, template_id)


@router.post(C + "/templates/{template_id}/mappings", status_code=201, operation_id="excel_mapping_save")
def save_mapping(company_id: uuid.UUID, template_id: uuid.UUID, body: MappingIn, ctx: Manage) -> Any:
    """Save a draft. Drafts may be incomplete; the response carries the validation result."""
    return X.save_mapping(ctx, template_id, body.definition, body.note, body.mapping_id)


@router.get(C + "/mappings/{mapping_id}", operation_id="excel_mapping")
def mapping(company_id: uuid.UUID, mapping_id: uuid.UUID, ctx: Read) -> Any:
    return X.get_mapping(ctx, mapping_id)


@router.post(C + "/mappings/{mapping_id}/validate", operation_id="excel_mapping_validate")
def validate_mapping(company_id: uuid.UUID, mapping_id: uuid.UUID, ctx: Read) -> Any:
    return X.validate_mapping_endpoint(ctx, mapping_id)


@router.post(C + "/mappings/{mapping_id}/activate", operation_id="excel_mapping_activate")
def activate_mapping(company_id: uuid.UUID, mapping_id: uuid.UUID, body: ActivateIn, ctx: Manage) -> Any:
    return X.activate_mapping(ctx, mapping_id, body.acknowledge_risks)


@router.post(C + "/mappings/{mapping_id}/preview", operation_id="excel_mapping_preview")
def preview_mapping(
    company_id: uuid.UUID,
    mapping_id: uuid.UUID,
    body: ParamsIn,
    ctx: Read,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Any:
    """Real approved data through the same mapping code the export uses (no file is created)."""
    return X.preview_mapping(ctx, mapping_id, body.params, limit)


# ------------------------------------------------------------------ exports
class ExportIn(BaseModel):
    template_id: uuid.UUID
    mapping_id: uuid.UUID | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class ExportCreateIn(ExportIn):
    idempotency_key: str = Key
    acknowledge_repeat: bool = False
    reason: str | None = Field(default=None, max_length=500)


class RegenerateIn(BaseModel):
    idempotency_key: str = Key
    reason: str = Field(min_length=3, max_length=500)


@router.post(C + "/exports/validate", operation_id="excel_export_validate")
def validate_export(company_id: uuid.UUID, body: ExportIn, ctx: Export) -> Any:
    return X.validate_export(ctx, body.model_dump())


@router.post(C + "/exports/preview", operation_id="excel_export_preview")
def preview_export(
    company_id: uuid.UUID, body: ExportIn, ctx: Export, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> Any:
    return X.preview_export(ctx, body.model_dump(), limit)


@router.post(C + "/exports", status_code=202, operation_id="excel_export_create")
def create_export(
    company_id: uuid.UUID, body: ExportCreateIn, ctx: Export, bg: BackgroundTasks, response: Response
) -> Any:
    """Queue an export. The file exists only when `status` becomes `completed` (validated and stored). The same idempotency
    key returns the first export (HTTP 200)."""
    created, exp = X.create_export(ctx, body.model_dump())
    if created:
        assert ctx.org_id is not None
        bg.add_task(X.dispatch, ctx.org_id, company_id, exp["id"])
    else:
        response.status_code = 200
    return exp


@router.get(C + "/exports", operation_id="excel_exports")
def exports(
    company_id: uuid.UUID,
    ctx: Read,
    template_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Any:
    return X.list_exports(ctx, template_id=template_id, status=status, limit=limit, offset=offset)


@router.get(C + "/exports/compare", operation_id="excel_exports_compare")
def compare(company_id: uuid.UUID, a: uuid.UUID, b: uuid.UUID, ctx: Read) -> Any:
    return X.compare_exports(ctx, a, b)


@router.get(C + "/exports/{export_id}", operation_id="excel_export")
def get_export(company_id: uuid.UUID, export_id: uuid.UUID, ctx: Read) -> Any:
    return X.get_export(ctx, export_id)


@router.get(C + "/exports/{export_id}/transactions", operation_id="excel_export_transactions")
def export_transactions(
    company_id: uuid.UUID, export_id: uuid.UUID, ctx: Read, limit: Limit = 100, offset: Offset = 0
) -> Any:
    return X.export_transactions(ctx, export_id, limit, offset)


@router.get(C + "/exports/{export_id}/file", operation_id="excel_export_file")
def export_file(company_id: uuid.UUID, export_id: uuid.UUID, ctx: Export) -> Any:
    """Authorized download. Every download is audited; the recorded checksum is returned in `X-Export-Sha256`."""
    stream, filename, size, media, sha = X.download_export(ctx, export_id)
    return StreamingResponse(
        stream,
        media_type=media,
        headers={
            "Content-Disposition": _disposition(filename),
            "Content-Length": str(size),
            "X-Export-Sha256": sha,
            "Cache-Control": "private, no-store",
        },
    )


@router.post(C + "/exports/{export_id}/verify", operation_id="excel_export_verify")
def verify_export(company_id: uuid.UUID, export_id: uuid.UUID, ctx: Read) -> Any:
    return X.verify_export(ctx, export_id)


@router.post(C + "/exports/{export_id}/regenerate", status_code=202, operation_id="excel_export_regenerate")
def regenerate(
    company_id: uuid.UUID,
    export_id: uuid.UUID,
    body: RegenerateIn,
    ctx: Export,
    bg: BackgroundTasks,
    response: Response,
) -> Any:
    """A new, separately recorded export with the same template version, mapping and parameters; the original is untouched."""
    created, exp = X.regenerate_export(ctx, export_id, body.model_dump())
    if created:
        assert ctx.org_id is not None
        bg.add_task(X.dispatch, ctx.org_id, company_id, exp["id"])
    else:
        response.status_code = 200
    return exp


@router.get(C + "/transactions/{transaction_id}/exports", operation_id="excel_transaction_exports")
def transaction_exports(company_id: uuid.UUID, transaction_id: uuid.UUID, ctx: Read) -> Any:
    return X.transaction_exports(ctx, transaction_id)


# ------------------------------------------------------------------ reports
class RunIn(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class ReportIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    category: str | None = Field(default=None, max_length=60)
    definition: dict[str, Any]
    template_id: uuid.UUID | None = None


class ReportPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    category: str | None = Field(default=None, max_length=60)
    definition: dict[str, Any] | None = None
    template_id: uuid.UUID | None = None
    base_row_version: int | None = None


class ReportValidateIn(BaseModel):
    definition: dict[str, Any]
    template_id: uuid.UUID | None = None


class ReportExportIn(BaseModel):
    idempotency_key: str = Key
    params: dict[str, Any] = Field(default_factory=dict)
    acknowledge_repeat: bool = False
    reason: str | None = Field(default=None, max_length=500)


@router.get(C + "/reports/standard", operation_id="excel_standard_reports")
def standard_reports(company_id: uuid.UUID, ctx: Read) -> Any:
    return X.standard_reports()


@router.post(C + "/reports/standard/{key}/run", operation_id="excel_standard_report_run")
def run_standard(
    company_id: uuid.UUID, key: str, body: RunIn, ctx: Read, limit: Limit = 100, offset: Offset = 0
) -> Any:
    return X.run_standard_report(ctx, key, body.params, limit, offset)


@router.get(C + "/reports/custom", operation_id="excel_custom_reports")
def custom_reports(company_id: uuid.UUID, ctx: Read, include_archived: bool = False) -> Any:
    return X.list_reports(ctx, include_archived)


@router.post(C + "/reports/custom/validate", operation_id="excel_custom_report_validate")
def validate_report(company_id: uuid.UUID, body: ReportValidateIn, ctx: Configure) -> Any:
    return X.validate_report_definition(ctx, body.definition, body.template_id)


@router.post(C + "/reports/custom", status_code=201, operation_id="excel_custom_report_create")
def create_report(company_id: uuid.UUID, body: ReportIn, ctx: Configure) -> Any:
    return X.create_report(ctx, body.model_dump())


@router.patch(C + "/reports/custom/{report_id}", operation_id="excel_custom_report_update")
def update_report(company_id: uuid.UUID, report_id: uuid.UUID, body: ReportPatch, ctx: Configure) -> Any:
    return X.update_report(ctx, report_id, body.model_dump(exclude_unset=True))


@router.post(C + "/reports/custom/{report_id}/archive", operation_id="excel_custom_report_archive")
def archive_report(company_id: uuid.UUID, report_id: uuid.UUID, ctx: Configure, restore: bool = False) -> Any:
    return X.archive_report(ctx, report_id, restore)


@router.post(C + "/reports/custom/{report_id}/run", operation_id="excel_custom_report_run")
def run_report(
    company_id: uuid.UUID, report_id: uuid.UUID, body: RunIn, ctx: Read, limit: Limit = 100, offset: Offset = 0
) -> Any:
    return X.run_custom_report(ctx, report_id, body.params, limit, offset)


@router.post(C + "/reports/custom/{report_id}/export", status_code=202, operation_id="excel_custom_report_export")
def export_report(
    company_id: uuid.UUID,
    report_id: uuid.UUID,
    body: ReportExportIn,
    ctx: Export,
    bg: BackgroundTasks,
    response: Response,
) -> Any:
    created, exp = X.export_report(ctx, report_id, body.model_dump())
    if created:
        assert ctx.org_id is not None
        bg.add_task(X.dispatch, ctx.org_id, company_id, exp["id"])
    else:
        response.status_code = 200
    return exp


class ExcelOverviewTemplate(BaseModel):
    id: str
    company_id: str
    company_name: str
    name: str
    category: str
    status: str
    file_kind: str
    current_version_no: int
    risk_level: str
    exports: int
    last_export_at: datetime | None


class ExcelOverviewExport(BaseModel):
    id: str
    company_id: str
    company_name: str
    template_name: str
    status: str
    created_at: datetime
    filename: str | None
    row_counts: dict[str, int] | None
    include_unapproved: bool
    error_message: str | None
    created_by_name: str | None


class ExcelOverviewStats(BaseModel):
    completed: int
    failed: int
    running: int
    this_month: int


class ExcelOverviewStorage(BaseModel):
    template_bytes: int
    export_bytes: int


class ExcelOverviewUsage(BaseModel):
    key: str
    used: int
    limit: int | None
    unlimited: bool


class ExcelOverviewOut(BaseModel):
    templates: list[ExcelOverviewTemplate]
    recent_exports: list[ExcelOverviewExport]
    stats: ExcelOverviewStats
    storage: ExcelOverviewStorage
    usage: list[ExcelOverviewUsage]
    custom_reports: int


# ------------------------------------------------------------------ Control Center (organization level, read-only)
@router.get("/excel/overview", operation_id="excel_overview", response_model=ExcelOverviewOut)
def overview(ctx: Monitor) -> Any:
    """Templates, recent exports and usage across the organization's companies. No file content and no downloads here."""
    return X.overview(ctx)
