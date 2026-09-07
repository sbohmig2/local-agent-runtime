"""Validate documentation ownership and task linkage."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPO_ROOT / "docs"
TASKS_ROOT = DOCS_ROOT / "tasks"
COMPONENTS_ROOT = DOCS_ROOT / "architecture" / "components"
ROADMAP_ROOT = DOCS_ROOT / "roadmap"
TASK_STATUSES = {"not-started", "in-progress", "review", "parked", "done", "canceled"}
COMPONENT_STATUSES = {"planned", "active", "paused", "deprecated"}
INITIATIVE_STATUSES = {"planned", "active", "paused", "shipped", "archived"}
NON_TASKS = {"README.md", "next-up.md", "queue-memory.md", "code-review-in-progress.md"}
HUMAN_TASK_STATUSES = {
    "not started": "not-started",
    "in progress": "in-progress",
    "review": "review",
    "parked": "parked",
    "done": "done",
    "canceled": "canceled",
}


def frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    loaded = yaml.safe_load(text[4:end]) or {}
    return loaded if isinstance(loaded, dict) else {}, text


def markdown_links() -> list[str]:
    errors: list[str] = []
    for path in DOCS_ROOT.rglob("*.md"):
        if "templates" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", text):
            reference = match.group(1).strip().strip("<>")
            if not reference or reference.startswith(("#", "http://", "https://", "mailto:")):
                continue
            target_ref = unquote(reference.split("#", 1)[0].split("?", 1)[0])
            target = path.parent / target_ref
            if not target.resolve().exists():
                errors.append(f"{path.relative_to(REPO_ROOT)}: broken link {reference}")
    return errors


def component_tasks(text: str) -> dict[str, str]:
    pairs = re.findall(
        r"^-\s+([A-Z]+-\d{3})\s+[—-]\s+"
        r"(not started|in progress|review|parked|done|canceled)\s+[—-]",
        text,
        re.MULTILINE | re.IGNORECASE,
    )
    return {tracker: HUMAN_TASK_STATUSES[status.lower()] for tracker, status in pairs}


def main() -> int:
    errors = markdown_links()
    initiatives: dict[str, dict[str, Any]] = {}
    for path in ROADMAP_ROOT.glob("*.md"):
        if path.name == "README.md":
            continue
        data, _ = frontmatter(path)
        slug = path.stem
        if data.get("initiative") != slug:
            errors.append(f"{path.relative_to(REPO_ROOT)}: initiative must equal {slug}")
        if data.get("status") not in INITIATIVE_STATUSES:
            errors.append(f"{path.relative_to(REPO_ROOT)}: invalid initiative status")
        initiatives[slug] = data

    components: dict[str, tuple[dict[str, Any], dict[str, str], Path]] = {}
    for path in COMPONENTS_ROOT.glob("*.md"):
        if path.name == "README.md":
            continue
        data, text = frontmatter(path)
        slug = path.stem
        if data.get("component") != slug:
            errors.append(f"{path.relative_to(REPO_ROOT)}: component must equal {slug}")
        if data.get("status") not in COMPONENT_STATUSES:
            errors.append(f"{path.relative_to(REPO_ROOT)}: invalid component status")
        for initiative in data.get("initiatives", []):
            if initiative not in initiatives:
                errors.append(f"{path.relative_to(REPO_ROOT)}: unknown initiative {initiative}")
        components[slug] = (data, component_tasks(text), path)

    tasks: dict[str, tuple[dict[str, Any], Path]] = {}
    for path in TASKS_ROOT.rglob("*.md"):
        if path.parent.name == "templates" or path.name in NON_TASKS:
            continue
        match = re.fullmatch(r"([A-Z]+-\d{3})-[a-z0-9][a-z0-9-]*\.md", path.name)
        if match is None:
            errors.append(f"{path.relative_to(REPO_ROOT)}: invalid task filename")
            continue
        tracker = match.group(1)
        data, text = frontmatter(path)
        component = data.get("component")
        status = data.get("status")
        if data.get("tracker") != tracker:
            errors.append(f"{path.relative_to(REPO_ROOT)}: tracker must equal {tracker}")
        if component not in components:
            errors.append(f"{path.relative_to(REPO_ROOT)}: unknown component {component}")
        elif components[component][1].get(tracker) != status:
            errors.append(
                f"{components[component][2].relative_to(REPO_ROOT)}: "
                f"{tracker} status does not match {status}"
            )
        if status not in TASK_STATUSES:
            errors.append(f"{path.relative_to(REPO_ROOT)}: invalid task status {status}")
        if not data.get("effort") or data.get("priority") not in {"P0", "P1", "P2"}:
            errors.append(f"{path.relative_to(REPO_ROOT)}: missing effort or priority")
        if (path.parent.name == "done") != (status == "done"):
            errors.append(f"{path.relative_to(REPO_ROOT)}: done status/folder mismatch")
        body_status = re.search(r"^\*\*Status:\*\*\s*(.+)$", text, re.MULTILINE)
        normalized = body_status.group(1).strip().lower().replace(" ", "-") if body_status else None
        if normalized and normalized != status:
            errors.append(f"{path.relative_to(REPO_ROOT)}: body status does not match {status}")
        if tracker in tasks:
            errors.append(f"{path.relative_to(REPO_ROOT)}: duplicate tracker {tracker}")
        tasks[tracker] = (data, path)

    queue = (TASKS_ROOT / "next-up.md").read_text(encoding="utf-8")
    for tracker in re.findall(r"LAR-\d{3}", queue):
        if tracker not in tasks:
            errors.append(f"docs/tasks/next-up.md: missing task {tracker}")

    if errors:
        print(f"Documentation validation failed with {len(errors)} issue(s):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(
        f"Documentation clean: {len(initiatives)} initiative, "
        f"{len(components)} component, and {len(tasks)} tasks validated."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
