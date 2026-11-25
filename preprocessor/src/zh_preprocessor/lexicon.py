from __future__ import annotations

import gzip
import json
import re
import unicodedata
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
# Cross-reference lines ("variant of 著|着[zhao1]", "old variant of 沉", "see 節骨眼") make
# poor word entries when the same key has a real sense.
_CROSS_REFERENCE = re.compile(r"^(?:\S+ ){0,2}variant of\b|^see\b", re.IGNORECASE)


def _exact_pinyin(py: str) -> str:
    """Pinyin compared with tones: lower case, letters only ("Jiē zhe" -> "jiēzhe")."""
    return "".join(
        character for character in unicodedata.normalize("NFC", py.lower())
        if character.isalpha()
    )


def _toneless_pinyin(py: str) -> str:
    """Pinyin compared without tones ("zhù" -> "zhu")."""
    decomposed = unicodedata.normalize("NFD", py.lower())
    return "".join(character for character in decomposed if character.isascii() and character.isalpha())


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
        # Every line under a key, with its short definition, for choosing a word entry.
        self.headlines: dict[str, list[DictionaryEntry]] = {}
        self.senses: dict[str, list[DictionaryEntry]] = {}
        # CC-CEDICT's own traditional headword -> its readings. OpenCC keeps some traditional
        # characters that CC-CEDICT simplifies (著 stays 著, but 接著 is filed under 接着), so
        # a key from conversion alone can miss the entry.
        self.trad_senses: dict[str, list[DictionaryEntry]] = {}
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
            self.headlines.setdefault(key, []).append(entry)
            reading = DictionaryEntry(
                simp=key,
                trad=entry.trad,
                py=entry.py,
                definition="; ".join(
                    [sense for sense in definitions.split("/") if sense][:MAX_SENSES]
                ),
            )
            self.senses.setdefault(key, []).append(reading)
            self.trad_senses.setdefault(trad, []).append(reading)
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

    def readings(self, token: str) -> list[DictionaryEntry]:
        """Every CC-CEDICT reading a token can stand for; empty when CC-CEDICT has none.

        A token matches by OpenCC conversion and by CC-CEDICT's traditional headwords
        (著 is 着 for zhe/zháo/zhuó and 著 for zhù). OpenCC's readings come first, because the
        token may be simplified text (宁 is also a traditional headword for 㝉); readings
        under another key follow only where their traditional headword is the token itself.
        """
        converted = canonical_simplified(token)
        own = self.senses.get(converted, [])
        return own + [
            entry for entry in self.trad_senses.get(token, []) if entry.simp != converted
        ]

    def keys_for(self, token: str) -> list[str]:
        """The CC-CEDICT keys a token can stand for, OpenCC's first; empty when none."""
        return list(dict.fromkeys(entry.simp for entry in self.readings(token)))

    def key(self, token: str, py: str | None = None) -> str:
        """The simplified key for a token: its CC-CEDICT entry, chosen by pinyin if given.

        Falls back to OpenCC conversion for words missing from CC-CEDICT.
        """
        readings = self.readings(token)
        if not readings:
            return canonical_simplified(token)
        if py:
            for normalize in (_exact_pinyin, _toneless_pinyin):
                wanted = normalize(py)
                for entry in readings:
                    if normalize(entry.py) == wanted:
                        return entry.simp
        return readings[0].simp

    def headline(self, key: str, py: str | None = None) -> DictionaryEntry | None:
        """The CC-CEDICT line that best describes a key, for its word entry.

        Prefers the reading the text uses (`py`, compared with tones, then without), then a
        real sense over a cross-reference such as "variant of ...", then a common word over
        a proper name (CC-CEDICT lists 以 "abbr. for Israel" first) unless `py` is capitalized.
        """
        lines = self.headlines.get(key)
        if not lines:
            return None
        if py:
            for normalize in (_exact_pinyin, _toneless_pinyin):
                matches = [line for line in lines if normalize(line.py) == normalize(py)]
                if matches:
                    lines = matches
                    break
        senses = [line for line in lines if not _CROSS_REFERENCE.match(line.definition)]
        proper = bool(py and py[:1].isupper())
        return sorted(senses or lines, key=lambda line: line.py[:1].isupper() != proper)[0]

    def annotate(self, token: str) -> dict[str, str | bool]:
        if not is_han_word(token):
            return {"t": token, "p": True}
        key = self.key(token)
        dictionary_entry = self.headline(key)
        py = dictionary_entry.py if dictionary_entry else " ".join(
            lazy_pinyin(token, style=Style.TONE, neutral_tone_with_five=True)
        )
        definition = (
            dictionary_entry.definition
            if dictionary_entry and dictionary_entry.definition
            else "[definition unavailable]"
        )
        return {"t": token, "k": key, "py": py, "def": definition, "src": "local"}

    def word_entry(self, token: str, py: str | None = None) -> dict[str, str | int | None]:
        """The `words` entry for a key; `py` is the reading the text uses most."""
        key = token if token in self.entries else self.key(token, py)
        dictionary_entry = self.headline(key, py)
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
        entries = self.readings(token)
        if not entries:
            return None
        return [{"py": entry.py, "def": entry.definition} for entry in entries]

    def __contains__(self, key: str) -> bool:
        return key in self.entries

    def unmatched_hsk(self, keys: Iterable[str]) -> list[str]:
        return sorted(key for key in set(keys) if key not in self.hsk_levels)
