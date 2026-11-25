"""Repository hygiene: no file carries a raw bidi, zero-width or other invisible formatting character.

GitHub warns on such characters, and they can make code read differently from how it runs. Code that must match
them builds them from their code points with chr()."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INVISIBLE = {0x00AD, 0x061C, 0x180E, 0xFEFF, *range(0x200B, 0x2010), *range(0x202A, 0x202F), *range(0x2060, 0x206A)}


def test_no_raw_invisible_characters():
    found = []
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT)
        if not path.is_file() or ".git" in rel.parts or "__pycache__" in rel.parts or ".pytest_cache" in rel.parts:
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), 1):
            found += [f"{rel.as_posix()}:{number}: U+{ord(ch):04X}" for ch in line if ord(ch) in INVISIBLE]
    assert found == []
