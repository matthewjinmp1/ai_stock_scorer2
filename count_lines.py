#!/usr/bin/env python3
"""Count source lines in this project without external dependencies."""

import argparse
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent

LANGUAGES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".html": "HTML",
    ".css": "CSS",
    ".sh": "Shell",
}

EXCLUDED_DIRECTORIES = {
    ".git",
    ".idea",
    ".pytest_cache",
    ".venv",
    ".vscode",
    "__pycache__",
    "data",
    "dist",
    "build",
    "node_modules",
    "venv",
}


def source_files():
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in LANGUAGES:
            continue
        if any(part in EXCLUDED_DIRECTORIES for part in path.relative_to(ROOT).parts):
            continue
        yield path


def count_file(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    return {
        "file": str(path.relative_to(ROOT)),
        "language": LANGUAGES[path.suffix.lower()],
        "lines": len(lines),
        "nonblank": sum(bool(line.strip()) for line in lines),
        "blank": sum(not line.strip() for line in lines),
    }


def collect_counts():
    files = [count_file(path) for path in source_files()]
    languages = defaultdict(lambda: {"files": 0, "lines": 0, "nonblank": 0, "blank": 0})
    for item in files:
        totals = languages[item["language"]]
        totals["files"] += 1
        totals["lines"] += item["lines"]
        totals["nonblank"] += item["nonblank"]
        totals["blank"] += item["blank"]

    return {
        "root": str(ROOT),
        "files": files,
        "languages": dict(sorted(languages.items())),
        "totals": {
            "files": len(files),
            "lines": sum(item["lines"] for item in files),
            "nonblank": sum(item["nonblank"] for item in files),
            "blank": sum(item["blank"] for item in files),
        },
    }


def print_table(title, rows, name_key):
    print(title)
    print(f"{'Name':<40} {'Files':>7} {'Lines':>9} {'Nonblank':>10} {'Blank':>8}")
    print("-" * 78)
    for row in rows:
        print(
            f"{row[name_key]:<40} "
            f"{row.get('files', 1):>7,} "
            f"{row['lines']:>9,} "
            f"{row['nonblank']:>10,} "
            f"{row['blank']:>8,}"
        )
    print()


def print_report(report):
    file_rows = sorted(report["files"], key=lambda item: (-item["lines"], item["file"]))
    language_rows = [
        {"language": language, **counts}
        for language, counts in report["languages"].items()
    ]
    print_table("By module", file_rows, "file")
    print_table("By language", language_rows, "language")

    totals = report["totals"]
    print(f"Source files:   {totals['files']:,}")
    print(f"Physical lines: {totals['lines']:,}")
    print(f"Nonblank lines: {totals['nonblank']:,}")
    print(f"Blank lines:    {totals['blank']:,}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()
    report = collect_counts()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)


if __name__ == "__main__":
    main()
