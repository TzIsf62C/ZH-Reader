from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from opencc import OpenCC

HAN_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002FA1F]+")
_ENDINGS = frozenset("。！？!?")
_CLOSERS = frozenset("」』】）》〉”’\"')")
_HORIZONTAL_SPACE = re.compile(r"[^\S\n]+")
_PARAGRAPH_BREAK = re.compile(r"\n(?:[^\S\n]*\n)+")

_TO_SIMPLIFIED = OpenCC("t2s")
_TO_TRADITIONAL = OpenCC("s2t")


@dataclass(frozen=True)
class Sentence:
    id: str
    text: str


@dataclass(frozen=True)
class ScriptDetection:
    script: str
    ratio: float


def normalize_text(source: str) -> str:
    text = source.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    paragraphs = []
    for raw_paragraph in _PARAGRAPH_BREAK.split(text):
        paragraph = _HORIZONTAL_SPACE.sub(" ", raw_paragraph)
        paragraph = "\n".join(line.strip() for line in paragraph.split("\n")).strip()
        if paragraph:
            paragraphs.append(paragraph)
    return "\n\n".join(paragraphs)


def detect_script(text: str) -> ScriptDetection:
    han_characters = [char for char in text if HAN_PATTERN.fullmatch(char)]
    if not han_characters:
        return ScriptDetection("simp", 0.0)

    simplified = _TO_SIMPLIFIED.convert(text)
    traditional = _TO_TRADITIONAL.convert(text)
    changed_to_simplified = sum(
        count
        for character, count in Counter(han_characters).items()
        if character != _TO_SIMPLIFIED.convert(character)
    )
    ratio = round(changed_to_simplified / len(han_characters), 4)
    if simplified == text and traditional != text:
        script = "simp"
    elif traditional == text and simplified != text:
        script = "trad"
    elif simplified == text and traditional == text:
        script = "simp"
    else:
        script = "mixed"
    return ScriptDetection(script, ratio)


def split_sentences(paragraph: str, paragraph_index: int) -> list[Sentence]:
    """Split at sentence-ending punctuation (with any closing quotes) and at line breaks.

    A line break ends a sentence even without punctuation, so headings, list items and table
    rows are their own sentences. The newline begins the next sentence, so the sentences
    still join to the paragraph exactly.
    """
    sentences: list[str] = []
    start = 0
    index = 0
    while index < len(paragraph):
        if paragraph[index] == "\n":
            if index > start:
                sentences.append(paragraph[start:index])
                start = index
            index += 1
            continue
        if paragraph[index] not in _ENDINGS:
            index += 1
            continue
        end = index + 1
        while end < len(paragraph) and paragraph[end] in _ENDINGS:
            end += 1
        while end < len(paragraph) and paragraph[end] in _CLOSERS:
            end += 1
        sentences.append(paragraph[start:end])
        start = end
        index = end
    if start < len(paragraph):
        sentences.append(paragraph[start:])
    if not sentences and paragraph:
        sentences.append(paragraph)
    return [
        Sentence(id=f"p{paragraph_index}s{sentence_index}", text=text)
        for sentence_index, text in enumerate(sentences)
        if text
    ]


def split_paragraphs(text: str) -> list[str]:
    return [paragraph for paragraph in _PARAGRAPH_BREAK.split(text) if paragraph]


def canonical_simplified(text: str) -> str:
    return _TO_SIMPLIFIED.convert(text)


def canonical_traditional(text: str) -> str:
    return _TO_TRADITIONAL.convert(text)


def is_han_word(text: str) -> bool:
    return bool(HAN_PATTERN.fullmatch(text))


def contains_han(text: str) -> bool:
    return bool(HAN_PATTERN.search(text))
