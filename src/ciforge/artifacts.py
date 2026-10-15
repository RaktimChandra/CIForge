"""Artifact rules in TeamCity's form: ``source-glob => target-dir``.

The path below the first wildcard segment is preserved under the target, so
``suites/*/target/surefire-reports/*.xml => reports`` publishes
``reports/api-suite/target/surefire-reports/TEST-x.xml``. Without ``=>`` files keep their
path relative to the glob's fixed prefix at the artifacts root.
"""

from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath


def _split(rule: str) -> tuple[str, str]:
    if "=>" in rule:
        src, dst = rule.split("=>", 1)
        return src.strip(), dst.strip().strip("/")
    return rule.strip(), ""


def _fixed_prefix(glob: str) -> str:
    parts = []
    for part in PurePosixPath(glob).parts:
        if any(ch in part for ch in "*?["):
            break
        parts.append(part)
    if len(parts) == len(PurePosixPath(glob).parts):
        parts = parts[:-1]  # a plain file path: keep its name
    return "/".join(parts)


def publish(workspace: Path, rules: list[str], store_dir: Path) -> list[tuple[str, int]]:
    """Copies files matching ``rules`` from ``workspace`` into ``store_dir``; returns (path, size) pairs."""
    published: dict[str, int] = {}
    for rule in rules:
        src, dst = _split(rule)
        prefix = _fixed_prefix(src)
        for f in sorted(workspace.glob(src)):
            if not f.is_file():
                continue
            rel = f.relative_to(workspace / prefix) if prefix else f.relative_to(workspace)
            target = PurePosixPath(dst) / PurePosixPath(*rel.parts) if dst else PurePosixPath(*rel.parts)
            out = store_dir / target
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out)
            published[str(target)] = out.stat().st_size
    return sorted(published.items())


def fetch(store_dir: Path, patterns: list[str], into: Path) -> list[str]:
    """Copies a dependency's published artifacts matching ``patterns`` into ``into``."""
    copied: list[str] = []
    if not store_dir.exists():
        return copied
    for pattern in patterns:
        for f in sorted(store_dir.glob(pattern)):
            if f.is_file():
                rel = f.relative_to(store_dir)
                out = into / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, out)
                copied.append(str(PurePosixPath(*rel.parts)))
    return sorted(set(copied))


def safe_path(root: Path, rel: str) -> Path:
    """Resolves ``rel`` under ``root``, refusing anything that escapes it (path traversal)."""
    p = (root / rel).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError(f"path escapes the artifact directory: {rel}")
    return p
