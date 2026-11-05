# Documentation Index

## Reading order

1. [Glossary](architecture/GLOSSARY.md) — canonical terms (all documents conform to it)
2. [Decision log](architecture/DECISIONS.md) — accepted ADRs and **decisions requiring owner approval**
3. [Product architecture](architecture/PRODUCT_ARCHITECTURE.md)
4. [SaaS tenant model](architecture/SAAS_TENANT_MODEL.md)
5. [System architecture](architecture/SYSTEM_ARCHITECTURE.md)
6. [Application architecture](architecture/APPLICATION_ARCHITECTURE.md) (includes Super Admin vs Control Center)
7. [Technology stack](architecture/TECHNOLOGY_STACK.md)
8. [Workspace customization architecture](architecture/WORKSPACE_CUSTOMIZATION_ARCHITECTURE.md)
9. [Accounting data mapping](architecture/ACCOUNTING_DATA_MAPPING.md)
10. [Workflows and approvals](architecture/WORKFLOW_AND_APPROVALS.md)
11. [Storage and sync](architecture/STORAGE_AND_SYNC_ARCHITECTURE.md)
12. [AI and document processing](architecture/AI_AND_DOCUMENT_PROCESSING.md)
13. [Excel engine design](architecture/EXCEL_ENGINE_DESIGN.md)
14. [Subscription and entitlements](architecture/SUBSCRIPTION_AND_ENTITLEMENTS.md)
15. Database: [design](database/DATABASE_DESIGN.md) · [entity catalog](database/ENTITY_CATALOG.md) · [isolation and RLS](database/ISOLATION_AND_RLS.md)
16. [Security architecture](security/SECURITY_ARCHITECTURE.md)
17. [Design system](design/DESIGN_SYSTEM.md)
18. Development: [roadmap](development/DEVELOPMENT_ROADMAP.md) · [testing strategy](development/TESTING_STRATEGY.md) · [test scenarios](development/TEST_SCENARIOS.md) · [project skills plan](development/PROJECT_SKILLS_PLAN.md)
19. Phases: [1](development/PHASE_1.md) · [2](development/PHASE_2.md) · [3](development/PHASE_3.md) · [4](development/PHASE_4.md) · [5](development/PHASE_5.md) · [6](development/PHASE_6.md) · [7](development/PHASE_7.md) · [8](development/PHASE_8.md) · [9](development/PHASE_9.md) · [10](development/PHASE_10.md) · [11](development/PHASE_11.md) · [12](development/PHASE_12.md)
20. [Phase 0 completion report](development/PHASE_0_COMPLETION_REPORT.md) · [Phase 1 completion report](development/PHASE_1_COMPLETION_REPORT.md)
21. [Authentication threat model](security/THREAT_MODEL_AUTH.md) · [Authentication and session design](security/AUTHENTICATION_AND_SESSION_DESIGN.md) · [Role and permission matrix](security/ROLE_AND_PERMISSION_MATRIX.md)
23. Phase 2 (Super Admin): [implementation](development/PHASE_2_IMPLEMENTATION.md) · [test report](development/PHASE_2_TEST_REPORT.md) · [completion report](development/PHASE_2_COMPLETION_REPORT.md) · [platform admin security](security/PLATFORM_ADMIN_SECURITY.md) · [support access policy](security/SUPPORT_ACCESS_POLICY.md) · [subscription and entitlement implementation](development/SUBSCRIPTION_AND_ENTITLEMENT_IMPLEMENTATION.md) · [platform monitoring](development/PLATFORM_MONITORING.md)
24. Phase 3 (Firm Control Center): [overview](firm-control-center/OVERVIEW.md) · [organization](firm-control-center/ORGANIZATION_MANAGEMENT.md) · [companies](firm-control-center/COMPANY_MANAGEMENT.md) · [team and accountants](firm-control-center/TEAM_AND_ACCOUNTANT_MANAGEMENT.md) · [roles and permissions](firm-control-center/ROLE_AND_PERMISSION_MODEL.md) · [API reference](firm-control-center/API_REFERENCE.md) · [Phase 4 integration](firm-control-center/PHASE_4_INTEGRATION.md) · [security test results](firm-control-center/SECURITY_TEST_RESULTS.md) · [completion report](development/PHASE_3_COMPLETION_REPORT.md)
25. Phase 4 (Workspace structure & customization engine): [architecture](workspace/WORKSPACE_ARCHITECTURE.md) · [data model](workspace/WORKSPACE_DATA_MODEL.md) · [builder](workspace/WORKSPACE_BUILDER.md) · [custom fields](workspace/CUSTOM_METADATA_FIELDS.md) · [naming rules](workspace/DOCUMENT_NAMING_RULES.md) · [filing rules](workspace/AUTOMATIC_FILING_RULES.md) · [templates](workspace/WORKSPACE_TEMPLATES.md) · [permissions](workspace/WORKSPACE_PERMISSIONS.md) · [versioning](workspace/WORKSPACE_VERSIONING.md) · [future integration contracts](workspace/FUTURE_INTEGRATION_CONTRACTS.md) · [security test results](workspace/SECURITY_TEST_RESULTS.md) · [completion report](development/PHASE_4_COMPLETION_REPORT.md)
26. Phase 5 (Windows desktop foundation): [architecture](desktop/DESKTOP_ARCHITECTURE.md) · [Tauri configuration](desktop/TAURI_CONFIGURATION.md) · [authentication](desktop/AUTHENTICATION.md) · [company selection](desktop/COMPANY_SELECTION.md) · [workspace integration](desktop/WORKSPACE_INTEGRATION.md) · [local storage](desktop/LOCAL_STORAGE.md) · [SQLite](desktop/SQLITE_DATABASE.md) · [API integration](desktop/API_INTEGRATION.md) · [synchronization](desktop/SYNCHRONIZATION_FOUNDATION.md) · [device registration](desktop/DEVICE_REGISTRATION.md) · [security](desktop/SECURITY.md) · [Windows build](desktop/WINDOWS_BUILD_AND_INSTALLATION.md) · [release and updates](desktop/RELEASE_AND_UPDATE_PROCESS.md) · [completion report](development/PHASE_5_COMPLETION_REPORT.md)
27. Phase 6 (Document management and storage engine): [architecture](documents/DOCUMENT_MANAGEMENT_ARCHITECTURE.md) · [data model](documents/DOCUMENT_DATA_MODEL.md) · [import and upload](documents/DOCUMENT_IMPORT_AND_UPLOAD.md) · [cloud storage](documents/CLOUD_STORAGE_ARCHITECTURE.md) · [local storage](documents/LOCAL_STORAGE_INTEGRATION.md) · [versioning](documents/DOCUMENT_VERSIONING.md) · [metadata](documents/DOCUMENT_METADATA.md) · [search](documents/DOCUMENT_SEARCH.md) · [naming and filing](documents/DOCUMENT_NAMING_AND_FILING.md) · [duplicates](documents/DUPLICATE_DETECTION.md) · [storage security](documents/STORAGE_SECURITY.md) · [entitlements](documents/STORAGE_ENTITLEMENTS.md) · [API reference](documents/DOCUMENT_API_REFERENCE.md) · [Phase 7 integration](documents/PHASE_7_INTEGRATION.md) · [security test results](documents/SECURITY_TEST_RESULTS.md) · [completion report](development/PHASE_6_COMPLETION_REPORT.md)
28. Phase 7 (OCR and AI document intelligence): [OCR engine](ai/OCR_ENGINE.md) · [classification](ai/DOCUMENT_CLASSIFICATION.md) · [financial data extraction](ai/FINANCIAL_DATA_EXTRACTION.md) · [bank statements](ai/BANK_STATEMENT_INTELLIGENCE.md) · [AI providers](ai/AI_PROVIDER_ARCHITECTURE.md) · [human review](ai/HUMAN_REVIEW_WORKFLOW.md) · [AI data security](ai/AI_DATA_SECURITY.md) · [completion report](development/PHASE_7_COMPLETION_REPORT.md)
22. Phase 1 implementation: [overview](development/PHASE_1_IMPLEMENTATION.md) · [test report](development/PHASE_1_TEST_REPORT.md) · [database implementation](database/INITIAL_DATABASE_IMPLEMENTATION.md) · [local setup guide](development/LOCAL_SETUP_GUIDE.md)

## Phase 0 acceptance criteria → where answered

| # | Criterion | Document |
|---|---|---|
| 1 | Super Admin vs Control Center | [Application architecture §1](architecture/APPLICATION_ARCHITECTURE.md#1-artify-super-admin-vs-firm-control-center) |
| 2 | Firm / independent subscription | [Tenant model §1, §4](architecture/SAAS_TENANT_MODEL.md), [ADR-001](architecture/DECISIONS.md) |
| 3 | Companies belong to tenants | [Tenant model §1](architecture/SAAS_TENANT_MODEL.md) |
| 4 | User/accountant assignment | [Tenant model §3](architecture/SAAS_TENANT_MODEL.md#3-how-users-and-accountants-are-assigned-to-companies) |
| 5 | Isolation enforcement | [Isolation and RLS](database/ISOLATION_AND_RLS.md), [Tenant model §5](architecture/SAAS_TENANT_MODEL.md) |
| 6–8 | Per-company workspace, section counts, nested folders, categories | [Workspace architecture §1–5](architecture/WORKSPACE_CUSTOMIZATION_ARCHITECTURE.md) |
| 9 | Company-specific metadata fields | [Workspace architecture §8](architecture/WORKSPACE_CUSTOMIZATION_ARCHITECTURE.md#8-custom-fields-and-metadata) |
| 10 | Template copy semantics | [Workspace architecture §6](architecture/WORKSPACE_CUSTOMIZATION_ARCHITECTURE.md#6-templates-and-presets) |
| 11 | Mapping to accounting entities | [Accounting data mapping](architecture/ACCOUNTING_DATA_MAPPING.md) |
| 12 | Workflows and approvals | [Workflows and approvals](architecture/WORKFLOW_AND_APPROVALS.md) |
| 13 | Independent Excel templates | [Excel engine design §1](architecture/EXCEL_ENGINE_DESIGN.md) |
| 14 | Entitlement enforcement | [Subscription and entitlements §3](architecture/SUBSCRIPTION_AND_ENTITLEMENTS.md) |
| 15 | Desktop/Mobile config retrieval | [Application architecture §5](architecture/APPLICATION_ARCHITECTURE.md#5-how-desktop-and-mobile-retrieve-and-apply-the-workspace-configuration) |
| 16 | Local Windows folders | [Storage and sync §3](architecture/STORAGE_AND_SYNC_ARCHITECTURE.md) |
| 17 | Cloud storage and sync | [Storage and sync](architecture/STORAGE_AND_SYNC_ARCHITECTURE.md) |
| 18 | Financial accuracy and traceability | [Accounting data mapping §5–6](architecture/ACCOUNTING_DATA_MAPPING.md) |
| 19 | Security and audit | [Security architecture](security/SECURITY_ARCHITECTURE.md) |
| 20 | Later phases | [Roadmap](development/DEVELOPMENT_ROADMAP.md), phase documents |
| — | Required test scenarios | [Test scenarios](development/TEST_SCENARIOS.md) (specified, not executed) |
