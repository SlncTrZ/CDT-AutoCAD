"""Dependency lock workflow — derive platform lock inputs from pyproject.toml only.
Wing: code | Topic: mp0-t00-reproducibility | Updated: 2026-09-10 23:31
"""

from __future__ import annotations

import argparse
import importlib.metadata
import subprocess
import sys
import tomllib
from pathlib import Path

LOCK_PIP_VERSION = "26.1.2"


def collect_lock_requirements(pyproject_path: str | Path) -> tuple[str, ...]:
    """Collect runtime, all optional, dev-group and build requirements from pyproject."""
    path = Path(pyproject_path)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    requirements: list[str] = []
    requirements.extend(str(item) for item in data.get("project", {}).get("dependencies", []))
    for group in data.get("project", {}).get("optional-dependencies", {}).values():
        requirements.extend(str(item) for item in group)
    requirements.extend(str(item) for item in data.get("dependency-groups", {}).get("dev", []))
    requirements.extend(str(item) for item in data.get("build-system", {}).get("requires", []))

    seen: set[str] = set()
    ordered: list[str] = []
    for requirement in requirements:
        normalized = requirement.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return tuple(ordered)


def canonical_lock_filename() -> str:
    if sys.platform == "win32":
        return "pylock.windows.toml"
    if sys.platform.startswith("linux"):
        return "pylock.linux.toml"
    raise RuntimeError(f"unsupported lock platform: {sys.platform}")


def normalize_lock_newlines(lock_path: str | Path) -> Path:
    """Normalize generated lock text to LF so Git identity is platform-stable."""
    path = Path(lock_path)
    normalized = path.read_text(encoding="utf-8")
    path.write_text(normalized, encoding="utf-8", newline="\n")
    return path


def generate_lock(repo_root: str | Path, *, output: str | Path | None = None) -> Path:
    """Generate the current platform PEP 751 dependency lock using the pinned pip tool."""
    root = Path(repo_root).resolve()
    actual_pip = importlib.metadata.version("pip")
    if actual_pip != LOCK_PIP_VERSION:
        raise RuntimeError(
            f"pip {LOCK_PIP_VERSION} is required to generate canonical locks; found {actual_pip}"
        )
    target = Path(output) if output is not None else Path(canonical_lock_filename())
    if not target.is_absolute():
        target = root / target
    requirements = collect_lock_requirements(root / "pyproject.toml")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "lock",
            "--quiet",
            "--output",
            str(target),
            *requirements,
        ],
        cwd=root,
        check=True,
    )
    return normalize_lock_newlines(target)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate CDT-AutoCAD canonical dependency lock")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    target = generate_lock(args.repo, output=args.output)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
