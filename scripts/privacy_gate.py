#!/usr/bin/env python3
"""Block publication when current files or reachable Git history expose private data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


DEFAULT_ROOT = Path(__file__).resolve().parents[1]
ALLOWED_IDENTITIES = {
    ("nature-wx-lab", "nature-wx-lab@users.noreply.github.com"),
    ("github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com"),
}
ALLOWED_EMAILS = {email for _, email in ALLOWED_IDENTITIES}
TEXT_SUFFIXES = {
    ".css", ".csv", ".geojson", ".html", ".js", ".json", ".md", ".mjs",
    ".py", ".svg", ".txt", ".xml", ".yaml", ".yml",
}
FORBIDDEN_NAMES = {".DS_Store", ".env"}
FORBIDDEN_PARTS = {
    ".cache", ".staging", ".venv", "__pycache__", "fixtures", "logs",
    "node_modules", "outputs", "private", "screenshots", "state",
}
FORBIDDEN_SUFFIXES = {
    ".db", ".dmg", ".gz", ".heic", ".jpeg", ".jpg", ".key", ".log",
    ".p12", ".pdf", ".pem", ".pfx", ".sqlite", ".tiff", ".xls",
    ".xlsx", ".zip",
}
STATIC_BINARY_ASSETS = {
    "vendor/leaflet-1.9.4/images/marker-icon.png": {"574c3a5cca85f4114085b6841596d62f00d7c892c7b03f28cbfa301deb1dc437"},
    "vendor/leaflet-1.9.4/images/marker-icon-2x.png": {"00179c4c1ee830d3a108412ae0d294f55776cfeb085c60129a39aa6fc4ae2528"},
    "vendor/leaflet-1.9.4/images/marker-shadow.png": {"264f5c640339f042dd729062cfc04c17f8ea0f29882b538e3848ed8f10edb4da"},
}
MAX_FILE_BYTES = 95 * 1024 * 1024
MAX_TEXT_BYTES = 20 * 1024 * 1024
PATTERNS = {
    "absolute-user-path": re.compile(r"/" r"Users/|[A-Za-z]:\\\\Users\\\\", re.IGNORECASE),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "private-key": re.compile(r"-----BEGIN " r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credential-assignment": re.compile(
        r"\b(?:api[_-]?key|client[_-]?secret|password|passwd|access[_-]?token|auth[_-]?token)\b"
        r"\s*[:=]\s*[\"']?[^\s\"']{8,}",
        re.IGNORECASE,
    ),
    "github-token": re.compile(r"\b(?:github_" r"pat_|gh[pousr]_)[A-Za-z0-9_]{16,}\b"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "bearer-token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
}
BINARY_MARKERS = {
    "absolute-user-path": b"/" + b"Users/",
    "personal-mail-domain": b"@" + b"gmail.com",
    "github-token": b"github_" + b"pat_",
    "github-classic-token": b"gh" + b"p_",
    "private-key": b"-----BEGIN " + b"PRIVATE KEY-----",
}
BINARY_EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def git(root: Path, *args: str, text: bool = True) -> str | bytes:
    return subprocess.check_output(["git", "-C", str(root), *args], text=text)


def manifest_binary_assets(raw: bytes) -> dict[str, set[str]]:
    manifest = json.loads(raw.decode("utf-8"))
    assets: dict[str, set[str]] = {}
    entries = [*manifest["chunks"].values()]
    for group in manifest["rasters"]["files"].values():
        entries.extend(group.values())
    for entry in entries:
        relative = entry.get("path")
        checksum = entry.get("sha256")
        if (
            not isinstance(relative, str)
            or "\\" in relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not isinstance(checksum, str)
            or not re.fullmatch(r"[0-9a-f]{64}", checksum)
        ):
            raise ValueError("climate manifest contains an unsafe binary asset entry")
        public_path = f"data/climate/{relative}"
        if Path(public_path).suffix.lower() not in {".bin", ".webp"}:
            raise ValueError("climate manifest binary asset has an unexpected suffix")
        assets.setdefault(public_path, set()).add(checksum)
    return assets


def current_binary_assets(root: Path) -> dict[str, set[str]]:
    assets = {path: set(checksums) for path, checksums in STATIC_BINARY_ASSETS.items()}
    manifest = root / "data/climate/manifest.json"
    if manifest.is_file():
        assets.update(manifest_binary_assets(manifest.read_bytes()))
    return assets


def forbidden_path(path: Path, allowed_assets: dict[str, set[str]]) -> str | None:
    if path.name in FORBIDDEN_NAMES or path.name.startswith(".env."):
        return "forbidden-file"
    if any(part in FORBIDDEN_PARTS for part in path.parts):
        return "forbidden-directory"
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return "forbidden-suffix"
    if path.suffix.lower() in {".bin", ".png", ".webp"} and path.as_posix() not in allowed_assets:
        return "unapproved-binary-asset"
    return None


def scan_text(label: str, text: str, denylist: tuple[str, ...]) -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for kind, pattern in PATTERNS.items():
            for match in pattern.finditer(line):
                if kind == "email" and match.group(0).lower() in ALLOWED_EMAILS:
                    continue
                findings.append((label, line_number, kind))
        if any(value.casefold() in line.casefold() for value in denylist):
            findings.append((label, line_number, "private-denylist"))
    return findings


def scan_bytes(
    label: str,
    raw: bytes,
    denylist: tuple[str, ...],
    source_path: Path | None = None,
    allowed_assets: dict[str, set[str]] | None = None,
) -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    lower = raw.lower()
    for kind, marker in BINARY_MARKERS.items():
        if marker.lower() in lower:
            findings.append((label, 0, kind))
    for match in BINARY_EMAIL.finditer(raw):
        email = match.group(0).decode("ascii").lower()
        if email not in ALLOWED_EMAILS:
            findings.append((label, 0, "email"))
    if source_path and allowed_assets and source_path.as_posix() in allowed_assets:
        if hashlib.sha256(raw).hexdigest() not in allowed_assets[source_path.as_posix()]:
            findings.append((label, 0, "allowlisted-asset-checksum"))
    for value in denylist:
        if value.encode("utf-8").lower() in lower:
            findings.append((label, 0, "private-denylist"))
    if len(raw) <= MAX_TEXT_BYTES:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = ""
        if text:
            findings.extend(scan_text(label, text, denylist))
    return findings


def scan_current_files(root: Path, denylist: tuple[str, ...]) -> tuple[list[tuple[str, int, str]], int]:
    findings: list[tuple[str, int, str]] = []
    scanned = 0
    allowed_assets = current_binary_assets(root)
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if ".git" in rel.parts:
            continue
        if path.is_symlink():
            findings.append((rel.as_posix(), 0, "symlink"))
            continue
        if path.is_dir():
            continue
        scanned += 1
        findings.extend(scan_text(f"filename:{rel.as_posix()}", rel.as_posix(), denylist))
        path_problem = forbidden_path(rel, allowed_assets)
        if path_problem:
            findings.append((rel.as_posix(), 0, path_problem))
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            findings.append((rel.as_posix(), 0, "oversized-file"))
            continue
        findings.extend(scan_bytes(rel.as_posix(), path.read_bytes(), denylist, rel, allowed_assets))
    return findings, scanned


def scan_git_history(root: Path, denylist: tuple[str, ...]) -> tuple[list[tuple[str, int, str]], int, int]:
    if not (root / ".git").exists():
        return [], 0, 0
    findings: list[tuple[str, int, str]] = []
    commits = 0
    records = str(git(root, "log", "--all", "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B%x1e"))
    for record in records.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        commits += 1
        parts = record.split("\x1f", 5)
        if len(parts) != 6:
            findings.append(("git-log", 0, "unparseable-commit"))
            continue
        commit, author_name, author_email, committer_name, committer_email, message = parts
        if (author_name, author_email) not in ALLOWED_IDENTITIES:
            findings.append((commit[:12], 0, "disallowed-author-identity"))
        if (committer_name, committer_email) not in ALLOWED_IDENTITIES:
            findings.append((commit[:12], 0, "disallowed-committer-identity"))
        findings.extend(scan_text(commit[:12], message, denylist))

    for row in str(git(root, "for-each-ref", "--format=%(refname)%00%(contents)")).splitlines():
        ref_name, _, contents = row.partition("\x00")
        findings.extend(scan_text(f"ref:{ref_name}", f"{ref_name}\n{contents}", denylist))

    object_rows = [row for row in str(git(root, "rev-list", "--objects", "--all")).splitlines() if " " in row]
    allowed_assets = {path: set(checksums) for path, checksums in STATIC_BINARY_ASSETS.items()}
    for row in object_rows:
        object_id, name = row.split(" ", 1)
        if name != "data/climate/manifest.json" or str(git(root, "cat-file", "-t", object_id)).strip() != "blob":
            continue
        raw_manifest = git(root, "cat-file", "blob", object_id, text=False)
        assert isinstance(raw_manifest, bytes)
        for path, checksums in manifest_binary_assets(raw_manifest).items():
            allowed_assets.setdefault(path, set()).update(checksums)

    objects = 0
    seen: set[str] = set()
    for row in object_rows:
        object_id, name = row.split(" ", 1)
        if object_id in seen or str(git(root, "cat-file", "-t", object_id)).strip() != "blob":
            continue
        seen.add(object_id)
        objects += 1
        findings.extend(scan_text(f"historical-filename:{name}", name, denylist))
        path_problem = forbidden_path(Path(name), allowed_assets)
        if path_problem:
            findings.append((f"{object_id[:12]}:{name}", 0, f"historical-{path_problem}"))
            continue
        size = int(str(git(root, "cat-file", "-s", object_id)).strip())
        if size > MAX_FILE_BYTES:
            findings.append((f"{object_id[:12]}:{name}", 0, "historical-oversized-file"))
            continue
        raw = git(root, "cat-file", "blob", object_id, text=False)
        assert isinstance(raw, bytes)
        findings.extend(scan_bytes(f"{object_id[:12]}:{name}", raw, denylist, Path(name), allowed_assets))
    return findings, commits, objects


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--skip-history", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    denylist = tuple(value.strip() for value in os.environ.get("PRIVACY_DENYLIST", "").splitlines() if value.strip())
    findings, file_count = scan_current_files(root, denylist)
    commit_count = 0
    object_count = 0
    if not args.skip_history:
        historical, commit_count, object_count = scan_git_history(root, denylist)
        findings.extend(historical)
    if findings:
        for label, line_number, kind in sorted(set(findings)):
            location = f"{label}:{line_number}" if line_number else label
            print(f"privacy gate: {location} [{kind}]")
        raise SystemExit(1)
    print(
        "privacy gate passed: "
        f"files={file_count}, commits={commit_count}, reachable_blobs={object_count}, "
        "identities=allowlisted"
    )


if __name__ == "__main__":
    main()
