#!/usr/bin/env python3
"""Documentation consistency checker for Artify Co-Accountant (Phase 0).

Standard library only. Run from anywhere:  python3 tools/check_docs.py

Checks (structure and links only; it cannot judge whether the architecture is correct):
  1. Required Phase 0 files exist and are non-empty.
  2. Relative Markdown links resolve to files, and #anchors resolve to headings.
  3. Every docs/development/PHASE_1..12.md contains the eleven mandatory sections.
  4. Scenario IDs (TS-XXX-NN) referenced anywhere exist in TEST_SCENARIOS.md
     (wildcards like TS-ISO-* are checked by prefix).
  5. SQL code blocks in docs do not declare float/real/double/money types.
  6. Every ADR/decision id referenced (ADR-NNN, D-N) is defined in DECISIONS.md.

Exit code 0 = all checks passed, 1 = at least one failure.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_FILES = [
    "README.md",
    "CLAUDE.md",
    ".gitignore",
    "docs/README.md",
    "docs/architecture/PRODUCT_ARCHITECTURE.md",
    "docs/architecture/SYSTEM_ARCHITECTURE.md",
    "docs/architecture/SAAS_TENANT_MODEL.md",
    "docs/architecture/APPLICATION_ARCHITECTURE.md",
    "docs/architecture/TECHNOLOGY_STACK.md",
    "docs/architecture/WORKSPACE_CUSTOMIZATION_ARCHITECTURE.md",
    "docs/architecture/ACCOUNTING_DATA_MAPPING.md",
    "docs/architecture/STORAGE_AND_SYNC_ARCHITECTURE.md",
    "docs/architecture/AI_AND_DOCUMENT_PROCESSING.md",
    "docs/architecture/EXCEL_ENGINE_DESIGN.md",
    "docs/architecture/SUBSCRIPTION_AND_ENTITLEMENTS.md",
    "docs/architecture/WORKFLOW_AND_APPROVALS.md",
    "docs/architecture/GLOSSARY.md",
    "docs/architecture/DECISIONS.md",
    "docs/database/DATABASE_DESIGN.md",
    "docs/database/ENTITY_CATALOG.md",
    "docs/database/ISOLATION_AND_RLS.md",
    "docs/security/SECURITY_ARCHITECTURE.md",
    "docs/design/DESIGN_SYSTEM.md",
    "docs/development/DEVELOPMENT_ROADMAP.md",
    "docs/development/TESTING_STRATEGY.md",
    "docs/development/TEST_SCENARIOS.md",
    "docs/development/PROJECT_SKILLS_PLAN.md",
    "docs/development/PHASE_0_COMPLETION_REPORT.md",
    "docs/development/PHASE_1_COMPLETION_REPORT.md",
    "docs/security/THREAT_MODEL_AUTH.md",
    "docs/security/AUTHENTICATION_AND_SESSION_DESIGN.md",
    "docs/security/ROLE_AND_PERMISSION_MATRIX.md",
    "docs/database/INITIAL_DATABASE_IMPLEMENTATION.md",
    "docs/development/PHASE_1_IMPLEMENTATION.md",
    "docs/development/PHASE_1_TEST_REPORT.md",
    "docs/development/LOCAL_SETUP_GUIDE.md",
    "docs/development/PHASE_2_IMPLEMENTATION.md",
    "docs/development/PHASE_2_TEST_REPORT.md",
    "docs/development/PHASE_2_COMPLETION_REPORT.md",
    "docs/security/PLATFORM_ADMIN_SECURITY.md",
    "docs/security/SUPPORT_ACCESS_POLICY.md",
    "docs/development/SUBSCRIPTION_AND_ENTITLEMENT_IMPLEMENTATION.md",
    "docs/development/PLATFORM_MONITORING.md",
    ".claude/skills/README.md",
    "tools/check_docs.py",
] + [f"docs/development/PHASE_{n}.md" for n in range(1, 13)]

PHASE_SECTIONS = [
    "Objectives",
    "Functional scope",
    "Technical implementation",
    "Database changes",
    "API requirements",
    "Dependencies",
    "Security requirements",
    "Testing requirements",
    "Acceptance criteria",
    "Completion checklist",
    "Known risks",
]

failures: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def md_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "CLAUDE.md", ROOT / ".claude/skills/README.md"]
    files += sorted((ROOT / ".claude/skills").glob("*/SKILL.md"))
    files += sorted((ROOT / "docs").rglob("*.md"))
    return [f for f in files if f.exists()]


def slugify(heading: str) -> str:
    """GitHub-style heading anchor."""
    text = heading.strip().lower()
    text = re.sub(r"`", "", text)
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def anchors_of(path: Path) -> set[str]:
    anchors: set[str] = set()
    in_code = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            anchors.add(slugify(m.group(2)))
    return anchors


def strip_code_blocks(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


def check_required_files() -> None:
    for rel in REQUIRED_FILES:
        p = ROOT / rel
        if not p.exists():
            fail(f"missing required file: {rel}")
        elif p.stat().st_size == 0:
            fail(f"empty required file: {rel}")


def check_links() -> None:
    link_re = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)\)")
    anchor_cache: dict[Path, set[str]] = {}
    for f in md_files():
        text = strip_code_blocks(f.read_text(encoding="utf-8"))
        # also ignore inline code spans
        text = re.sub(r"`[^`\n]*`", "", text)
        for target in link_re.findall(text):
            if re.match(r"^[a-z]+://|^mailto:", target):
                continue
            path_part, _, anchor = target.partition("#")
            dest = f if path_part == "" else (f.parent / path_part).resolve()
            if not dest.exists():
                fail(f"{f.relative_to(ROOT)}: broken link -> {target}")
                continue
            if anchor and dest.suffix == ".md":
                anchor_cache.setdefault(dest, anchors_of(dest))
                if anchor not in anchor_cache[dest]:
                    fail(f"{f.relative_to(ROOT)}: broken anchor -> {target}")


def check_phase_sections() -> None:
    for n in range(1, 13):
        p = ROOT / f"docs/development/PHASE_{n}.md"
        if not p.exists():
            continue
        headings = {
            m.group(1).strip()
            for m in re.finditer(r"^##\s+(.*)$", p.read_text(encoding="utf-8"), flags=re.M)
        }
        for sec in PHASE_SECTIONS:
            if sec not in headings:
                fail(f"PHASE_{n}.md: missing section '## {sec}'")


def check_scenario_ids() -> None:
    sc = ROOT / "docs/development/TEST_SCENARIOS.md"
    if not sc.exists():
        return
    defined = set(re.findall(r"\|\s*(TS-[A-Z]+-\d+)\s*\|", sc.read_text(encoding="utf-8")))
    if not defined:
        fail("TEST_SCENARIOS.md: no scenario ids found")
        return
    ref_re = re.compile(r"TS-[A-Z]+-(?:\d+|\*)")
    for f in md_files():
        for ref in set(ref_re.findall(f.read_text(encoding="utf-8"))):
            if ref.endswith("*"):
                prefix = ref[:-1]
                if not any(d.startswith(prefix) for d in defined):
                    fail(f"{f.relative_to(ROOT)}: scenario prefix {ref} has no definitions")
            elif ref not in defined:
                fail(f"{f.relative_to(ROOT)}: undefined scenario id {ref}")


def check_sql_money_types() -> None:
    banned = re.compile(r"\b(float|float4|float8|real|double\s+precision|money)\b", re.I)
    for f in md_files():
        text = f.read_text(encoding="utf-8")
        for block in re.findall(r"```sql\n(.*?)```", text, flags=re.S):
            for line in block.splitlines():
                if banned.search(line):
                    fail(f"{f.relative_to(ROOT)}: banned numeric type in SQL block: {line.strip()}")


def check_decision_refs() -> None:
    dec = ROOT / "docs/architecture/DECISIONS.md"
    if not dec.exists():
        return
    dtext = dec.read_text(encoding="utf-8")
    adrs = set(re.findall(r"^###\s+(ADR-\d+)", dtext, flags=re.M))
    ds = set(re.findall(r"\|\s*\*\*(D-\d+)\*\*", dtext))
    for f in md_files():
        text = f.read_text(encoding="utf-8")
        for ref in set(re.findall(r"\bADR-\d+\b", text)):
            if ref not in adrs:
                fail(f"{f.relative_to(ROOT)}: undefined {ref}")
        for ref in set(re.findall(r"\bD-\d+\b", text)):
            if ref not in ds:
                fail(f"{f.relative_to(ROOT)}: undefined decision {ref}")


def main() -> int:
    check_required_files()
    check_links()
    check_phase_sections()
    check_scenario_ids()
    check_sql_money_types()
    check_decision_refs()
    if failures:
        print(f"FAILED: {len(failures)} problem(s)")
        for msg in failures:
            print(" -", msg)
        return 1
    print(f"OK: {len(REQUIRED_FILES)} required files present; {len(md_files())} markdown files checked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
