# Excel Security

| Threat | Control |
|---|---|
| Macros / active content | Never executed (no macro engine is invoked). `.xlsm` only with `feature.macro_templates`; VBA in a `.xlsx` is refused. Macro code is carried through untouched and flagged. |
| Zip bombs / huge files | Size cap while streaming, entry-count, uncompressed-size and ratio limits, row limit on export. |
| XML entity attacks / XXE | `defusedxml` for package metadata; `openpyxl` loads with `keep_links=False`; DOCTYPE/entities refused. |
| Formula injection | Text starting with `= + - @ TAB CR` is stored as a string (`quotePrefix`). Verified through the API with a hostile narration. |
| Arbitrary SQL / expressions | Mapping and report definitions are declarative JSON validated against the catalogue; SQL is fixed per dataset. |
| Cross-tenant / cross-company access | `organization_id` + `company_id` composite FKs, forced RLS, explicit `company_id` filters; ids from the client are looked up under the company (404). Tested between companies of one firm and between firms. |
| Unauthorised download | `excel.export` per request, audited; no public URLs; `Cache-Control: private, no-store`. Control Center has *no* download. |
| Tampering | Checksums on versions and exports; `verify`; immutability triggers and grants. |
| Platform admins | No default access to workbooks or exports (support grants only). |
| Paths | Storage keys are generated (`object_key`); client file names are only metadata and are sanitised in headers. |

**Not covered:** antivirus scanning of templates (the Phase 6 scanner interface is not applied to workbooks), content-based DLP, signing of exported files, and review by a security professional.
