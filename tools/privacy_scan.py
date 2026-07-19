from __future__ import annotations

import argparse
import re
from pathlib import Path


TEXT_SUFFIXES = {".md", ".py", ".toml", ".yml", ".yaml", ".json", ".txt"}
SKIP_PARTS = {".git", ".salience", "__pycache__", ".pytest_cache"}
PRIVATE_PROJECT_TERMS = ("Advanced" + "GridEA", "Human" + "Draw", "XAU" + "USD", "XAU" + "USDc")
PATTERNS = {
    "windows_private_path": re.compile(r"\b[A-Za-z]:\\"),
    "private_project_name": re.compile(
        r"\b(?:" + "|".join(re.escape(item) for item in PRIVATE_PROJECT_TERMS) + r")\b", re.I
    ),
    "codex_thread_id": re.compile(r"\b019f[0-9a-f]{4}-[0-9a-f-]{27,}\b", re.I),
    "credential_assignment": re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\b\s*[:=]\s*['\"][^'\"]{6,}"
    ),
}


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), 1):
            for name, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append(f"{path.relative_to(root)}:{line_number}:{name}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    findings = scan(Path(args.root).resolve())
    if findings:
        print("PRIVACY_SCAN_FAIL")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("PRIVACY_SCAN_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
