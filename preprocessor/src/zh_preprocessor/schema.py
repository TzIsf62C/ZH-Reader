from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 1
WORD_TOKEN_SOURCE = {"claude", "local"}
UNAVAILABLE = "[definition unavailable]"
# Optional word-entry provenance (schema-v1 addendum; written by the skill, ignored by the reader).
DEF_SOURCES = ("dictionary", "contextual", "unknown")
BASIS_KINDS = ("components", "transparent_compound", "sentence_context", "identified_referent")


def _provenance_errors(key: str, entry: dict) -> list[str]:
    if "defSource" not in entry:
        return [f'Word entry "{key}" has a basis without defSource.'] if "basis" in entry else []
    source = entry["defSource"]
    if source not in DEF_SOURCES:
        return [f'Word entry "{key}" has an invalid defSource.']
    if source != "contextual":
        errors = [f'Word entry "{key}" has a basis but is not contextual.'] if "basis" in entry else []
        if source == "unknown" and entry["def"] != UNAVAILABLE:
            errors.append(f'Unknown word entry "{key}" must keep the def "{UNAVAILABLE}".')
        return errors
    basis = entry.get("basis")
    if (
        not isinstance(basis, dict)
        or basis.get("kind") not in BASIS_KINDS
        or not isinstance(basis.get("note"), str)
        or not basis["note"].strip()
    ):
        return [f'Contextual word entry "{key}" has a missing or invalid basis.']
    if entry["def"] != UNAVAILABLE:
        # The entry def is the dictionary field; a contextual gloss lives on the tokens only.
        return [f'Contextual word entry "{key}" must keep the def "{UNAVAILABLE}".']
    return []


def validate_text_file(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["The file is not a text export (expected a JSON object)."]
    if data.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"Unsupported schemaVersion: expected {SCHEMA_VERSION}.")
    for field in ("textId", "title", "createdAt"):
        if not isinstance(data.get(field), str) or not data[field]:
            errors.append(f"Missing or invalid {field}.")
    if data.get("script") not in {"simp", "trad", "mixed"}:
        errors.append("Invalid script (expected simp, trad or mixed).")

    stats = data.get("stats")
    if not isinstance(stats, dict):
        errors.append("Missing stats.")
    elif (
        not isinstance(stats.get("wordCount"), int)
        or not isinstance(stats.get("uniqueWords"), int)
        or not isinstance(stats.get("hskCoverage"), dict)
    ):
        errors.append("Malformed stats.")

    words = data.get("words")
    paragraphs = data.get("paragraphs")
    if not isinstance(words, dict):
        errors.append("Missing words.")
        words = {}
    if not isinstance(paragraphs, list) or not paragraphs:
        errors.append("The text has no paragraphs.")
        return errors

    sentence_ids: set[str] = set()
    observed_counts: dict[str, int] = {}
    token_defs: dict[str, list[Any]] = {}
    for paragraph_index, paragraph in enumerate(paragraphs):
        if not isinstance(paragraph, dict) or not isinstance(paragraph.get("sentences"), list):
            errors.append(f"Paragraph {paragraph_index} is malformed.")
            continue
        for sentence in paragraph["sentences"]:
            if (
                not isinstance(sentence, dict)
                or not isinstance(sentence.get("id"), str)
                or not isinstance(sentence.get("tokens"), list)
            ):
                errors.append(f"A sentence in paragraph {paragraph_index} is malformed.")
                continue
            sentence_id = sentence["id"]
            if sentence_id in sentence_ids:
                errors.append(f"Duplicate sentence id {sentence_id}.")
            sentence_ids.add(sentence_id)
            if not isinstance(sentence.get("translation"), str):
                errors.append(f"Sentence {sentence_id} has an invalid translation.")
            for token in sentence["tokens"]:
                if not isinstance(token, dict) or not isinstance(token.get("t"), str) or not token["t"]:
                    errors.append(f"Sentence {sentence_id} has a malformed token.")
                    continue
                if token.get("p") is True:
                    if "k" in token:
                        errors.append(f"Non-word token in {sentence_id} has a vocabulary key.")
                    continue
                key = token.get("k")
                if not isinstance(key, str) or key not in words:
                    errors.append(f'Token "{token["t"]}" in {sentence_id} has no entry in words.')
                    continue
                if not isinstance(token.get("py"), str) or not token["py"]:
                    errors.append(f'Word token "{token["t"]}" in {sentence_id} has no pinyin.')
                if not isinstance(token.get("def"), str) or not token["def"]:
                    errors.append(f'Word token "{token["t"]}" in {sentence_id} has no definition.')
                if token.get("src") not in WORD_TOKEN_SOURCE:
                    errors.append(f'Word token "{token["t"]}" in {sentence_id} has an invalid source.')
                observed_counts[key] = observed_counts.get(key, 0) + 1
                token_defs.setdefault(key, []).append(token.get("def"))

    for key, entry in words.items():
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("simp"), str)
            or not isinstance(entry.get("trad"), str)
            or not isinstance(entry.get("py"), str)
            or not isinstance(entry.get("def"), str)
            or not isinstance(entry.get("countInText"), int)
            or entry.get("hsk") is not None
            and not isinstance(entry.get("hsk"), str)
        ):
            errors.append(f'Word entry "{key}" is malformed.')
            continue
        errors.extend(_provenance_errors(key, entry))
        if entry.get("defSource") == "unknown" and any(
            definition != UNAVAILABLE for definition in token_defs.get(key, ())
        ):
            errors.append(f'Unknown word "{key}" has a token with a definition.')
        if entry["countInText"] != observed_counts.get(key, 0):
            errors.append(f'Word entry "{key}" has an incorrect countInText.')
    if set(words) != set(observed_counts):
        errors.append("Word entries do not match the word tokens.")
    if isinstance(stats, dict) and isinstance(stats.get("wordCount"), int):
        if stats["wordCount"] != sum(observed_counts.values()):
            errors.append("stats.wordCount does not match the word tokens.")
    if isinstance(stats, dict) and isinstance(stats.get("uniqueWords"), int):
        if stats["uniqueWords"] != len(observed_counts):
            errors.append("stats.uniqueWords does not match the word entries.")
    return errors
