"""Optional publication checks; GoFind's operating work needs no Python runtime."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


LOCAL_DIRS = {"workspaces", "private", ".codex", ".venv", "venv", "__pycache__"}
SKIP_DIRS = LOCAL_DIRS | {".git"}
LINK = re.compile(r"!?\[[^\]\n]*\]\((?:<([^>\n]+)>|([^\s)]+))(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
ABSOLUTE = re.compile(r"(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]|/(?:Users|home|mnt|root)/)")
SECRETS = [
    re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}"),
]


def private_path(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    return bool(parts & LOCAL_DIRS) or (
        name == ".env" or (name.startswith(".env.") and name != ".env.example")
        or name in {"claude.local.md", "settings.local.json"}
        or name.endswith((".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3", ".db"))
        or (name.endswith(".json") and any(x in name for x in ("credentials", "cookies", "storage-state")))
    )


def public_files(root: Path) -> list[Path]:
    """Git includes accidentally tracked ignored files; source archives need no Git."""
    if (root / ".git").exists():
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        names = result.stdout.decode("utf-8").split("\0")
        return sorted({Path(name) for name in names if name})
    return sorted(
        path.relative_to(root) for path in root.rglob("*")
        if path.is_file() and not (set(path.relative_to(root).parts) & SKIP_DIRS)
    )


def prose(text: str) -> str:
    lines = []
    marker = ""
    size = 0
    for line in text.splitlines():
        match = FENCE.match(line)
        if match:
            run = match.group(1)
            if not marker:
                marker, size = run[0], len(run)
            elif run[0] == marker and len(run) >= size and line.strip() == run:
                marker = ""
            continue
        if not marker:
            lines.append(line)
    return "\n".join(lines)


def anchors(text: str) -> set[str]:
    ids = set(re.findall(r'<a\s+id=["\']([^"\']+)["\']', text))
    counts: dict[str, int] = {}
    for heading in re.findall(r"^#{1,6}\s+(.+)$", prose(text), re.MULTILINE):
        slug = re.sub(r"[^\w\- ]", "", heading.lower().strip())
        slug = slug.replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        ids.add(f"{slug}-{count}" if count else slug)
    return ids


def link_errors(root: Path, relative: Path, text: str) -> list[str]:
    root = root.resolve()
    errors = []
    for match in LINK.finditer(prose(text)):
        target = match.group(1) or match.group(2)
        parsed = urlsplit(target)
        if parsed.scheme in {"http", "https", "mailto"}:
            continue
        if parsed.scheme or parsed.netloc or target.startswith(("/", "\\")):
            errors.append(f"{relative}: non-portable local link")
            continue
        linked = (root / relative.parent / unquote(parsed.path)).resolve() if parsed.path else root / relative
        if not linked.is_relative_to(root):
            errors.append(f"{relative}: link leaves project")
        elif private_path(linked.relative_to(root)):
            errors.append(f"{relative}: public link points at private data")
        elif not linked.exists():
            errors.append(f"{relative}: missing link target {target}")
        elif parsed.fragment and linked.suffix == ".md":
            fragment = unquote(parsed.fragment)
            if fragment not in anchors(linked.read_text(encoding="utf-8")):
                errors.append(f"{relative}: missing anchor {target}")
    return errors


def check(root: Path) -> tuple[list[str], int]:
    root = root.resolve()
    errors = []
    required = ("AGENTS.md", "README.md", "LICENSE", "knowledge/INDEX.md", "knowledge/sources.md")
    for name in required:
        if not (root / name).is_file():
            errors.append(f"missing entry: {name}")
    files = public_files(root)
    for relative in files:
        if private_path(relative):
            errors.append(f"private file in publication set: {relative}")
            continue
        path = root / relative
        if not path.resolve().is_relative_to(root):
            errors.append(f"external symlink in publication set: {relative}")
            continue
        if not path.is_file():
            continue  # Git may still list a deliberately deleted file.
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # Not a media/metadata privacy audit.
        if any(pattern.search(text) for pattern in SECRETS):
            errors.append(f"possible credential in {relative} (value withheld)")
        if relative.suffix.lower() == ".md":
            if ABSOLUTE.search(text):
                errors.append(f"machine-specific absolute path in {relative}")
            errors.extend(link_errors(root, relative, text))
    return errors, len(files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        errors, count = check(args.root.resolve())
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Check could not complete ({type(exc).__name__}).", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"PASS: {count} public files; local links, portability and common credential checks.")
    print("Market conclusions, remote link availability and all personal data still need human review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
