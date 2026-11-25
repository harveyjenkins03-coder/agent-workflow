"""A text screen for anything an agent asks to store or pass on: secret-shaped text and instruction-shaped text.

What it does: pattern checks on one piece of text after zero-width and soft characters are removed.

What it does not do, and must not be relied on for:
- It is not a defence against a determined prompt injection. It catches common phrasings; a paraphrase, another
  language, an encoding (base64, HTML entities, homoglyphs) or text split across fields will get through.
- It does not prove text is safe. A clean result means only that none of these patterns matched.
- It never sees what a model does with the text. Recalled text must still be treated as data, never as an
  instruction, by whoever reads it.
The memory store uses it to refuse secrets and to quarantine instruction-shaped text; the profile gate uses it to
refuse both.
"""
from __future__ import annotations

import math
import re

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("api key", re.compile(r"\bsk-[A-Za-z0-9_-]{12,}")),
    ("forge token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"
                               r"|\bglpat-[A-Za-z0-9_-]{16,}")),
    ("chat token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("cloud key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer header", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}")),
    ("signed web token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("assigned secret", re.compile(r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|token)\s*[=:]\s*\S+")),
    ("credential url", re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s/]+@")),
    ("secret path", re.compile(r"(?i)[\\/]\.ssh[\\/]|\bid_(?:rsa|ed25519|ecdsa)\b|\.netrc\b|\.pgpass\b"
                               r"|\.env\.local\b|\.git-credentials\b")),
]
_CANDIDATE = re.compile(r"[A-Za-z0-9+/=_-]{24,}")
_HEX = re.compile(r"^[0-9a-fA-F]+$")

# A few words may sit between the verb and its object: "ignore all of the previous rules".
INSTRUCTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\b(?:ignore|disregard|forget)\W+(?:\w+\W+){0,4}?(?:previous|prior|above|earlier|your|all)\W+"
               r"(?:\w+\W+){0,3}?(?:instructions|rules|prompts?|guidance|directions)\b"),
    re.compile(r"(?i)\b(?:bypass|disable|skip|turn\s+off|work\s+round|work\s+around)\s+(?:the\s+|a\s+)?"
               r"(?:\w+\s+)?(?:guard|hooks?|review|gate|deny\s+rules?|safety|checks?)\b"),
    re.compile(r"(?i)\b(?:owner|user|maintainer|lead|human|admin|boss)\s+(?:has\s+)?(?:authori[sz]ed|approved|allows?|"
               r"permits?|pre-?approved|okayed|signed\s+off)\W+(?:\w+\W+){0,3}?(?:all|every|any|future|blanket|force|"
               r"push(?:es)?|merges?|releases?)\b"),
    re.compile(r"(?i)\byou\s+are\s+now\b|\bnew\s+system\s+prompt\b|\bsystem\s*prompt\s*:|^\s*system\s*:|<\s*/?\s*system\s*>"),
    re.compile(r"(?i)\b(?:always|automatically)\s+(?:push|merge|release|force[- ]push|approve)\b"),
    re.compile(r"(?i)\boverride\s+(?:the\s+rules|the\s+policy|agents\.md|claude\.md|your\s+instructions)"),
    re.compile(r"(?i)\|\s*(?:ba|z)?sh\b|\|\s*iex\b|\binvoke-expression\b|\biex\s*\(|\$\([^)]*\)"),
]


def _chars(*points: int | tuple[int, int]) -> str:
    """A regex character class built from code points, so this file holds no raw invisible character."""
    parts = []
    for p in points:
        lo, hi = p if isinstance(p, tuple) else (p, p)
        parts.append(chr(lo) + ("-" + chr(hi) if hi != lo else ""))
    return "[" + "".join(parts) + "]"


# Zero-width and soft characters: removed before matching.
_HIDDEN = re.compile(_chars((0x200B, 0x200D), 0x2060, 0xFEFF, 0x061C, 0x00AD))
# Control and direction-changing characters: each becomes a space in flat().
_CONTROL = re.compile(_chars((0x00, 0x1F), 0x7F, 0x200E, 0x200F, (0x202A, 0x202E), (0x2066, 0x2069)))


def entropy(s: str) -> float:
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in (s.count(ch) for ch in set(s))) if n else 0.0


def unhide(text: str) -> str:
    """Zero-width and soft characters removed: they split words past a pattern and vanish on screen."""
    return _HIDDEN.sub("", text or "")


def flat(text: str, most: int) -> str:
    """One line: no control or direction-changing characters, runs of space collapsed, at most `most` characters."""
    s = re.sub(r"\s+", " ", _CONTROL.sub(" ", text or "")).strip()
    return s if len(s) <= most else s[: most - 3].rstrip() + "..."


def secret_kind(text: str) -> str | None:
    """The kind of secret-shaped text in `text`, or None. It never returns the text itself."""
    plain = unhide(text)
    for label, rx in SECRET_PATTERNS:
        if rx.search(plain):
            return label
    for tok in _CANDIDATE.findall(plain):
        if _HEX.match(tok):
            if len(tok) in (40, 64):   # a full commit id or a SHA-256 is evidence, not a secret
                continue
            return "hex token"
        classes = sum(bool(re.search(p, tok)) for p in ("[a-z]", "[A-Z]", "[0-9]"))
        if classes >= 3 and entropy(tok) >= 4.0:
            return "high-entropy string"
    return None


def reads_as_instruction(text: str) -> bool:
    """Whether the text matches one of the instruction-shaped patterns. See the module notes for its limits."""
    plain = re.sub(r"\s+", " ", unhide(text))
    return any(rx.search(plain) for rx in INSTRUCTION_PATTERNS)
