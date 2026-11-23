from __future__ import annotations

import gzip
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import jieba
from pypinyin import Style, lazy_pinyin
from pypinyin.contrib.tone_convert import to_tone

from .text import HAN_PATTERN, canonical_simplified, canonical_traditional, is_han_word

MAX_SENSES = 8  # per reading, keeps skill chunk files context-sized
_CEDICT_ENTRY = re.compile(r"^(\S+)\s+(\S+)\s+\[([^\]]+)\]\s+/(.*)/$")


@dataclass(frozen=True)
class DictionaryEntry:
    simp: str
    trad: str
    py: str
    definition: str


def _read_text_file(path: Path) -> str:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as file:
            return file.read()
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            if len(names) != 1:
                raise ValueError(f"{path} must contain exactly one dictionary text file.")
            return archive.read(names[0]).decode("utf-8")
    return path.read_text(encoding="utf-8")


class Lexicon:
    def __init__(self, cedict_path: Path, hsk_path: Path):
        self.cedict_path = cedict_path
        self.hsk_path = hsk_path
        self.entries: dict[str, DictionaryEntry] = {}
        self.senses: dict[str, list[DictionaryEntry]] = {}
        self.hsk_levels: dict[str, str] = {}
        self._load_cedict()
        self._load_hsk()

    def _load_cedict(self) -> None:
        if not self.cedict_path.is_file():
            raise FileNotFoundError(
                f"CC-CEDICT data not found at {self.cedict_path}. Run `zh-preprocessor setup-data` "
                "or pass --cedict."
            )
        loaded = 0
        for line in _read_text_file(self.cedict_path).splitlines():
            match = _CEDICT_ENTRY.match(line)
            if not match:
                continue
            trad, simp, numeric_pinyin, definitions = match.groups()
            key = canonical_simplified(simp)
            entry = DictionaryEntry(
                simp=key,
                trad=canonical_traditional(trad),
                py=" ".join(to_tone(syllable) for syllable in numeric_pinyin.split()),
                definition="; ".join(definitions.split("/")[:3]),
            )
            self.entries.setdefault(key, entry)
            self.senses.setdefault(key, []).append(
                DictionaryEntry(
                    simp=key,
                    trad=entry.trad,
                    py=entry.py,
                    definition="; ".join(
                        [sense for sense in definitions.split("/") if sense][:MAX_SENSES]
                    ),
                )
            )
            loaded += 1
        if not loaded:
            raise ValueError(f"No valid CC-CEDICT entries found in {self.cedict_path}.")

    def _load_hsk(self) -> None:
        if not self.hsk_path.is_file():
            raise FileNotFoundError(
                f"HSK vocabulary data not found at {self.hsk_path}. Run `zh-preprocessor setup-data` "
                "or pass --hsk."
            )
        try:
            rows = json.loads(self.hsk_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid HSK JSON file: {self.hsk_path}") from error
        if not isinstance(rows, list):
            raise ValueError("HSK data must be an array of complete-hsk-vocabulary entries.")
        if not rows:
            raise ValueError("HSK vocabulary file is empty.")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("simplified"), str):
                continue
            levels = row.get("level", [])
            if not isinstance(levels, list):
                continue
            matches = []
            for level in levels:
                match = re.fullmatch(r"new(?:est)?-(\d)", str(level))
                if match:
                    matches.append(int(match.group(1)))
            if matches:
                self.hsk_levels[row["simplified"]] = (
                    "7-9" if min(matches) >= 7 else str(min(matches))
                )

    def segment(self, text: str) -> list[str]:
        tokens: list[str] = []
        cursor = 0
        for match in HAN_PATTERN.finditer(text):
            if match.start() > cursor:
                tokens.append(text[cursor : match.start()])
            tokens.extend(token for token in jieba.cut(match.group(), HMM=True) if token)
            cursor = match.end()
        if cursor < len(text):
            tokens.append(text[cursor:])
        return tokens

    def annotate(self, token: str) -> dict[str, str | bool]:
        if not is_han_word(token):
            return {"t": token, "p": True}
        key = canonical_simplified(token)
        dictionary_entry = self.entries.get(key)
        py = dictionary_entry.py if dictionary_entry else " ".join(
            lazy_pinyin(token, style=Style.TONE, neutral_tone_with_five=True)
        )
        definition = (
            dictionary_entry.definition
            if dictionary_entry and dictionary_entry.definition
            else "[definition unavailable]"
        )
        return {"t": token, "k": key, "py": py, "def": definition, "src": "local"}

    def word_entry(self, token: str) -> dict[str, str | int | None]:
        key = canonical_simplified(token)
        dictionary_entry = self.entries.get(key)
        py = dictionary_entry.py if dictionary_entry else " ".join(
            lazy_pinyin(key, style=Style.TONE, neutral_tone_with_five=True)
        )
        definition = (
            dictionary_entry.definition
            if dictionary_entry and dictionary_entry.definition
            else "[definition unavailable]"
        )
        return {
            "simp": key,
            "trad": dictionary_entry.trad if dictionary_entry else canonical_traditional(key),
            "py": py,
            "def": definition,
            "countInText": 0,
            "hsk": self.hsk_levels.get(key),
        }

    def candidates(self, token: str) -> list[dict[str, str]] | None:
        """All CC-CEDICT readings for a word, or None when CC-CEDICT has no entry."""
        entries = self.senses.get(canonical_simplified(token))
        if not entries:
            return None
        return [{"py": entry.py, "def": entry.definition} for entry in entries]

    def __contains__(self, key: str) -> bool:
        return key in self.entries

    def unmatched_hsk(self, keys: Iterable[str]) -> list[str]:
        return sorted(key for key in set(keys) if key not in self.hsk_levels)
