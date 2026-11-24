"""Domain glossaries for the zh-reader-annotate skill (PRD section 6).

A glossary is a user-curated UTF-8 TSV with the columns
`term, kind, pinyin, english, note, source`. Only `term` and `override` rows gloss tokens;
`name` rows name things in sentence translations and never become word glosses.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .text import canonical_simplified, is_han_word

COLUMNS = ("term", "kind", "pinyin", "english", "note", "source")
KINDS = ("term", "name", "override")
TOKEN_KINDS = ("term", "override")
MAX_ENGLISH_WORDS = 12


class GlossaryError(ValueError):
    pass


@dataclass(frozen=True)
class GlossaryRow:
    term: str
    kind: str
    pinyin: str
    english: str
    note: str
    source: str
    file: str
    line: int

    @property
    def marker(self) -> dict[str, str]:
        """The provenance recorded on a word glossed from this row (file name, not path)."""
        return {"file": self.file, "source": self.source}


def load_rows(path: Path) -> list[GlossaryRow]:
    """Parse and check one glossary file. Raises GlossaryError naming the file and line."""
    path = path.expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise GlossaryError(f"Glossary {path} does not exist.") from None
    except UnicodeDecodeError:
        raise GlossaryError(f"Glossary {path} is not UTF-8.") from None

    rows: list[GlossaryRow] = []
    seen: dict[str, int] = {}
    header_seen = False
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = [field.strip() for field in line.split("\t")]
        where = f"{path.name} line {number}"
        if not header_seen and tuple(field.lower() for field in fields) == COLUMNS:
            header_seen = True
            continue
        if len(fields) != len(COLUMNS):
            raise GlossaryError(
                f"{where}: expected {len(COLUMNS)} tab-separated columns "
                f"({', '.join(COLUMNS)}), found {len(fields)}."
            )
        term, kind, pinyin, english, note, source = fields
        if kind not in KINDS:
            raise GlossaryError(f"{where}: kind {kind!r} must be one of {', '.join(KINDS)}.")
        if not term:
            raise GlossaryError(f"{where}: the term is empty.")
        if kind in TOKEN_KINDS and not is_han_word(term):
            # Only term/override rows become tokens; a name (a quest title) may hold punctuation.
            raise GlossaryError(
                f"{where}: {kind} {term!r} must be Chinese characters only (use kind `name` "
                "for a full title)."
            )
        if not english:
            raise GlossaryError(f"{where}: {term} has no english.")
        if len(english.split()) > MAX_ENGLISH_WORDS:
            raise GlossaryError(f"{where}: {term} has english over {MAX_ENGLISH_WORDS} words.")
        if not source:
            raise GlossaryError(f"{where}: {term} has no source (a URL, or `derived`).")
        key = canonical_simplified(term)
        if key in seen:
            raise GlossaryError(f"{where}: {term} duplicates line {seen[key]}.")
        seen[key] = number
        rows.append(GlossaryRow(term, kind, pinyin, english, note, source, path.name, number))
    return rows


class Glossary:
    """`term` and `override` rows from one or more files, indexed by simplified key."""

    def __init__(self, paths: list[Path] | tuple[Path, ...] = ()):
        self.paths = [Path(path).expanduser().resolve() for path in paths]
        self.rows: dict[str, GlossaryRow] = {}
        names = [path.name for path in self.paths]
        if len(set(names)) != len(names):
            raise GlossaryError("Glossary files must have different file names.")
        for path in self.paths:
            for row in load_rows(path):
                if row.kind not in TOKEN_KINDS:
                    continue
                key = canonical_simplified(row.term)
                other = self.rows.get(key)
                if other is not None:
                    raise GlossaryError(
                        f"{row.term} is in both {other.file} (line {other.line}) and "
                        f"{row.file} (line {row.line})."
                    )
                self.rows[key] = row

    def __bool__(self) -> bool:
        return bool(self.rows)

    def get(self, key: str) -> GlossaryRow | None:
        return self.rows.get(key)
