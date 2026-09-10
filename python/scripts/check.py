"""Syntax-check Python source without importing it or reading .env."""

import ast
from pathlib import Path

root = Path(__file__).resolve().parents[1]
files = sorted(path for folder in ("agent", "tests", "scripts") for path in (root / folder).glob("**/*.py"))
for path in files:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
print(f"Python syntax check passed: {len(files)} files")
