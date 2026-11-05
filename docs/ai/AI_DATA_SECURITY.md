# AI Data Security (Phase 7)

* **Isolation**: every new table carries `organization_id` + `company_id` with composite FKs, RLS enabled and forced; the pipeline runs as a system actor inside the tenant context (`run_in_tenant_context`). Cross-company and cross-organization reads return 404 (tested for jobs, OCR text, extractions, page images).
* **Authorisation**: two stages as for documents (permission, then workspace engine on the document's node). Extraction text is as sensitive as the document.
* **Secrets**: provider keys exist only in server settings (`SecretStr`).
* **Logs**: structured logs carry ids, stages, durations and error codes — never recognised text, field values, amounts or credentials. Bank account numbers/IBANs are stored masked.
* **Untrusted files**: Phase 6 upload checks (type/content match, structure, size) still apply; decoding is bounded by page and time limits; the OCR binary runs with a timeout. No file content is executed.
* **External processing**: opt-in per organization with an audited choice; text only; see [AI_PROVIDER_ARCHITECTURE.md](AI_PROVIDER_ARCHITECTURE.md).
* **No training**: Artify does not train on customer documents; no code path exports documents or corrections.
* **Quotas**: monthly documents, OCR pages, bank statements, concurrent jobs and advanced AI are enforced by the backend `EntitlementService`; reaching a limit blocks new processing and never deletes data.
* **Audit**: processing requested/retried/cancelled/completed/failed/needs-manual, provider configuration, and review actions (`extraction.<action>` events) are recorded; every correction keeps old value, new value, reason and actor in `field_corrections`.
* **Limitations**: not penetration-tested; the Tesseract subprocess is not sandboxed beyond a timeout; the external provider is unverified; page images are streamed through the API (no public URLs) but are cached in the desktop's memory while open.
