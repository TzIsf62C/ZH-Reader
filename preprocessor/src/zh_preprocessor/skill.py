"""Work-file pipeline for the zh-reader-annotate Claude Code skill.

Claude writes annotations into chunk files inside a session; these commands prepare the
chunks, validate what Claude wrote, and assemble the reader's schema-v1 output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from .claude import AnnotationError, make_tokens
from .glossary import Glossary, GlossaryError, GlossaryRow
from .data import DATA_DIR
from .lexicon import Lexicon
from .pipeline import build_text_file, output_paths
from .schema import BASIS_KINDS, DEF_SOURCES, UNAVAILABLE, validate_text_file
from .text import HAN_PATTERN, canonical_simplified, detect_script, is_han_word, normalize_text
from .text import split_paragraphs, split_sentences

DEFAULT_CHUNK_SIZE = 20
DEFAULT_RETRIES = 2
DEFAULT_OUTPUT_DIR = Path.home() / "Documents" / "zh-reader-out"
DEFAULT_WORK_ROOT = DATA_DIR / "work"
MAX_CONTEXTUAL_WORDS = 12
MAX_BASIS_NOTE = 200
MAX_EVIDENCE_PARTS = 8
MAX_EVIDENCE_READINGS = 3
MAX_EVIDENCE_DEF = 120
MAX_GAP_SENTENCES = 3
MANIFEST = "manifest.json"
_REPOSITORY = Path(__file__).resolve().parents[3]


class SkillError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# File helpers


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.write("\n")
        temporary = Path(file.name)
    os.replace(temporary, path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _chunk_path(work_dir: Path, number: int, kind: str) -> Path:
    return work_dir / "chunks" / f"{number:03d}.{kind}.json"


def _load_manifest(work_dir: Path) -> dict:
    path = work_dir / MANIFEST
    if not path.is_file():
        raise SkillError(f"No manifest in {work_dir}. Run prepare first.")
    return _read_json(path)


def _save_manifest(work_dir: Path, manifest: dict) -> None:
    _write_json(work_dir / MANIFEST, manifest)


def _lexicon(manifest_or_options: dict) -> Lexicon:
    return Lexicon(Path(manifest_or_options["cedict"]), Path(manifest_or_options["hsk"]))


def _glossary(manifest: dict) -> Glossary:
    """The glossaries given to prepare, reloaded so validation sees the files' current rows."""
    try:
        return Glossary([Path(path) for path in manifest.get("glossaries", [])])
    except GlossaryError as error:
        raise SkillError(f"{error} (glossaries are fixed at prepare time)") from None


def _inside_repository(path: Path) -> bool:
    if not (_REPOSITORY / ".git").exists():
        return False
    return path.resolve().is_relative_to(_REPOSITORY)


def _progress(work_dir: Path, manifest: dict) -> dict:
    statuses = Counter(chunk["status"] for chunk in manifest["chunks"])
    json_path, source_path, report_path = output_paths(
        Path(manifest["outputDir"]), manifest["textId"]
    )
    return {
        "workDir": str(work_dir),
        "textId": manifest["textId"],
        "title": manifest["title"],
        "script": manifest["script"],
        "sentenceCount": manifest["sentenceCount"],
        "chunkCount": len(manifest["chunks"]),
        "chunksDone": statuses["done"],
        "chunksFallback": statuses["fallback"],
        "chunksPending": [
            chunk["number"] for chunk in manifest["chunks"] if chunk["status"] == "pending"
        ],
        "outputDir": manifest["outputDir"],
        "outputsExist": any(path.exists() for path in (json_path, source_path, report_path)),
    }


# ---------------------------------------------------------------------------
# Dictionary-gap evidence


def _trim_candidates(candidates: list[dict] | None) -> list[dict]:
    trimmed = []
    for candidate in (candidates or [])[:MAX_EVIDENCE_READINGS]:
        definition = candidate["def"]
        if len(definition) > MAX_EVIDENCE_DEF:
            definition = definition[: MAX_EVIDENCE_DEF - 1] + "…"
        trimmed.append({"py": candidate["py"], "def": definition})
    return trimmed


def gap_evidence(word: str, lexicon: Lexicon) -> list[dict]:
    """CC-CEDICT entries for the sub-words and characters of a word missing from CC-CEDICT.

    Longer sub-words come first. Parts keep the word's own script when conversion preserves
    its length.
    """
    key = canonical_simplified(word)
    shown = word if len(word) == len(key) else key
    parts: list[dict] = []
    seen: set[str] = set()
    for length in range(len(shown) - 1, 0, -1):
        for start in range(len(shown) - length + 1):
            sub = shown[start : start + length]
            if sub in seen or not lexicon.keys_for(sub):
                continue
            seen.add(sub)
            parts.append(
                {
                    "part": sub,
                    "candidates": _trim_candidates(lexicon.candidates(sub)),
                }
            )
            if len(parts) >= MAX_EVIDENCE_PARTS:
                return parts
    return parts


def _gap_list(sentences: list[dict], lexicon: Lexicon, whole_text: list) -> list[dict]:
    """One entry per missing word: its sentences here, elsewhere in the text, and components.

    `elsewhere` lets an earlier introduction (美国总统特朗普) support a later bare mention.
    """
    gaps: dict[str, dict] = {}
    chunk_ids = {sentence["id"] for sentence in sentences}
    for sentence in sentences:
        for token in sentence["tokens"]:
            if "candidates" not in token or token["candidates"] is not None:
                continue
            key = lexicon.key(token["t"])
            gap = gaps.get(key)
            if gap is None:
                gap = gaps[key] = {
                    "word": token["t"],
                    "sentences": [],
                    "elsewhere": [
                        {"id": other.id, "text": other.text}
                        for other in whole_text
                        if other.id not in chunk_ids and token["t"] in other.text
                    ][:MAX_GAP_SENTENCES],
                    "components": gap_evidence(token["t"], lexicon),
                }
            if len(gap["sentences"]) < MAX_GAP_SENTENCES and all(
                item["id"] != sentence["id"] for item in gap["sentences"]
            ):
                gap["sentences"].append({"id": sentence["id"], "text": sentence["text"]})
    return list(gaps.values())


# ---------------------------------------------------------------------------
# prepare


def prepare(
    input_path: Path,
    *,
    title: str | None = None,
    script: str = "auto",
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    work_root: Path = DEFAULT_WORK_ROOT,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    retries: int = DEFAULT_RETRIES,
    cedict: Path | None = None,
    hsk: Path | None = None,
    data_dir: Path = DATA_DIR,
    glossaries: list[Path] | tuple[Path, ...] = (),
) -> dict:
    if chunk_size < 1:
        raise SkillError("--chunk-size must be at least 1.")
    if retries < 0:
        raise SkillError("--retries must be zero or greater.")
    output_dir = output_dir.expanduser().resolve()
    work_root = work_root.expanduser().resolve()
    for label, path in (("Output", output_dir), ("Work", work_root)):
        if _inside_repository(path):
            raise SkillError(
                f"{label} directory {path} is inside the ZH Reader repository; "
                "choose a location outside it."
            )

    source = input_path.expanduser().read_text(encoding="utf-8")
    normalized = normalize_text(source)
    if not normalized:
        raise SkillError("The input contains no text after whitespace normalization.")
    text_id = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
    work_dir = work_root / text_id
    try:
        glossary = Glossary(list(glossaries))
    except GlossaryError as error:
        raise SkillError(str(error)) from None
    glossary_paths = [str(path) for path in glossary.paths]

    if (work_dir / MANIFEST).is_file():
        manifest = _load_manifest(work_dir)
        if manifest.get("glossaries", []) != glossary_paths:
            raise SkillError(
                f"{work_dir} was prepared with glossaries {manifest.get('glossaries', [])}, "
                f"not {glossary_paths}. Use a new --work-root (or delete that work directory) "
                "to prepare with different glossaries."
            )
        result = _progress(work_dir, manifest)
        result["resumed"] = True
        return result

    lexicon = Lexicon(
        cedict or data_dir / "cedict_ts.u8",
        hsk or data_dir / "complete-hsk-vocabulary.json",
    )
    detection = detect_script(normalized)
    resolved_script = detection.script if script == "auto" else script

    sentences = [
        sentence
        for paragraph_index, paragraph in enumerate(split_paragraphs(normalized))
        for sentence in split_sentences(paragraph, paragraph_index)
    ]
    chunks = []
    for offset in range(0, len(sentences), chunk_size):
        number = offset // chunk_size + 1
        batch = sentences[offset : offset + chunk_size]
        claude_input = []
        local_result = []
        for sentence in batch:
            segments = lexicon.segment(sentence.text)
            claude_input.append(
                {
                    "id": sentence.id,
                    "text": sentence.text,
                    "tokens": [
                        {
                            "t": segment,
                            "py": lexicon.annotate(segment)["py"],
                            "candidates": lexicon.candidates(segment),
                        }
                        if is_han_word(segment)
                        else {"t": segment}
                        for segment in segments
                    ],
                }
            )
            local_result.append(
                {
                    "id": sentence.id,
                    "translation": "",
                    "tokens": [lexicon.annotate(segment) for segment in segments],
                }
            )
        _write_json(
            _chunk_path(work_dir, number, "input"),
            {
                "chunk": number,
                "chunkCount": (len(sentences) + chunk_size - 1) // chunk_size,
                "script": resolved_script,
                "sentences": claude_input,
                "gaps": _gap_list(claude_input, lexicon, sentences),
            },
        )
        _write_json(_chunk_path(work_dir, number, "local"), local_result)
        chunks.append(
            {
                "number": number,
                "sentenceIds": [sentence.id for sentence in batch],
                "status": "pending",
                "attempts": 0,
                "errors": [],
            }
        )

    (work_dir / "source.txt").write_text(normalized, encoding="utf-8", newline="\n")
    manifest = {
        "textId": text_id,
        "inputPath": str(input_path.expanduser().resolve()),
        "title": title if title is not None else input_path.stem,
        "scriptOption": script,
        "script": resolved_script,
        "outputDir": str(output_dir),
        "chunkSize": chunk_size,
        "retries": retries,
        "cedict": str(lexicon.cedict_path),
        "hsk": str(lexicon.hsk_path),
        "glossaries": glossary_paths,
        "sentenceCount": len(sentences),
        "chunks": chunks,
    }
    _save_manifest(work_dir, manifest)
    result = _progress(work_dir, manifest)
    result["resumed"] = False
    return result


# ---------------------------------------------------------------------------
# next


def next_chunk(work_dir: Path) -> dict:
    manifest = _load_manifest(work_dir)
    for chunk in manifest["chunks"]:
        if chunk["status"] == "pending":
            number = chunk["number"]
            return {
                "done": False,
                "chunk": number,
                "chunkCount": len(manifest["chunks"]),
                "attempts": chunk["attempts"],
                "lastErrors": chunk["errors"],
                "input": str(_chunk_path(work_dir, number, "input")),
                "output": str(_chunk_path(work_dir, number, "output")),
            }
    return {"done": True, **_progress(work_dir, manifest)}


# ---------------------------------------------------------------------------
# validate


def check_chunk_output(
    chunk_input: dict,
    output: Any,
    lexicon: Lexicon,
    source_text: str | None = None,
    glossary: Glossary | None = None,
) -> tuple[list[dict], list[str]]:
    """Return (accepted sentences, errors). Sentences are only usable when errors is empty.

    `source_text` is the whole text; context quotes in a basis note are checked against it
    (an introduction in an earlier chunk can support a later mention). It defaults to the
    chunk's own sentences.
    """
    expected = chunk_input["sentences"]
    if isinstance(output, dict) and isinstance(output.get("sentences"), list):
        output = output["sentences"]
    if not isinstance(output, list):
        return [], ["Output must be a JSON array of sentence objects."]

    errors: list[str] = []
    expected_ids = [sentence["id"] for sentence in expected]
    output_ids = [item.get("id") if isinstance(item, dict) else None for item in output]
    if output_ids != expected_ids:
        missing = [sentence_id for sentence_id in expected_ids if sentence_id not in output_ids]
        extra = [sentence_id for sentence_id in output_ids if sentence_id not in expected_ids]
        detail = []
        if missing:
            detail.append(f"missing {missing}")
        if extra:
            detail.append(f"unexpected {extra}")
        if not detail:
            detail.append("wrong order")
        errors.append(
            f"Sentence ids must be exactly {expected_ids} in order ({'; '.join(detail)})."
        )

    by_id = {item.get("id"): item for item in output if isinstance(item, dict)}
    context_text = source_text or "\n".join(sentence["text"] for sentence in expected)
    accepted = []
    for sentence in expected:
        item = by_id.get(sentence["id"])
        if item is None:
            continue
        try:
            translation, tokens = make_tokens(
                sentence["text"], item.get("translation"), item.get("tokens"), lexicon.key
            )
        except AnnotationError as error:
            errors.append(f"{sentence['id']}: {error}{_join_hint(sentence['text'], item)}")
            continue
        # make_tokens keeps one token per written item, in order.
        for written, token in zip(item["tokens"], tokens):
            if token.get("p") is True:
                continue
            for error in _definition_errors(written, token, lexicon, context_text, glossary):
                errors.append(f'{sentence["id"]}: "{token["t"]}" {error}')
        accepted.append({"id": sentence["id"], "translation": translation, "tokens": tokens})

    if not errors:
        errors.extend(_schema_errors(accepted, lexicon))
    return accepted, errors


_GLOSS_WORD = re.compile(r"[a-z]+")
_GLOSS_STOPWORDS = frozenset(
    "a an the to of in on at by for with as or and be is are sb sth one one's oneself "
    "its it etc used e g also".split()
)


def _gloss_words(text: str) -> list[str]:
    return _GLOSS_WORD.findall(text.lower().replace("'s", ""))


def gloss_matches_dictionary(gloss: str, key: str, lexicon: Lexicon) -> bool:
    """True when a gloss reuses wording from one of the word's CC-CEDICT senses.

    A gloss may shorten or pick among senses, but it must share a content word (or a
    four-letter stem, so "warning" matches "to warn") with the dictionary. This catches a
    definition replaced by an unrelated one; it cannot judge subtle paraphrase.
    """
    return gloss_shares_wording(gloss, [entry.definition for entry in lexicon.senses.get(key, [])])


def gloss_shares_wording(gloss: str, references: list[str]) -> bool:
    """True when a gloss shares a content word or four-letter stem with any reference text."""
    dictionary = {word for reference in references for word in _gloss_words(reference)}
    written = _gloss_words(gloss)
    content = [word for word in written if word not in _GLOSS_STOPWORDS] or written
    for word in content:
        if word in dictionary:
            return True
        if len(word) >= 4 and any(len(d) >= 4 and d[:4] == word[:4] for d in dictionary):
            return True
    return False


def _evidence_hint(word: str, lexicon: Lexicon) -> str:
    parts = gap_evidence(word, lexicon)
    if not parts:
        return "No CC-CEDICT entries exist for its characters."
    summary = [
        f"{part['part']} = " + " / ".join(c["def"] for c in part["candidates"][:2])
        for part in parts
    ]
    return "Component evidence: " + json.dumps(summary, ensure_ascii=False)


def _glossary_errors(written: dict, token: dict, row: GlossaryRow | None) -> list[str]:
    """Check a token written with defSource "glossary" and record its provenance."""
    if row is None:
        return [
            'has defSource "glossary", but it is not a term or override row in the '
            "glossaries given to prepare. Use the defSource its evidence supports."
        ]
    errors = []
    definition = token["def"]
    if written.get("basis") is not None:
        errors.append('is a glossary gloss; remove its "basis" (the glossary row is the source).')
    if definition == UNAVAILABLE or not gloss_shares_wording(definition, [row.english]):
        errors.append(
            f"has the def {json.dumps(definition)}, which shares no word with its glossary "
            f"english {json.dumps(row.english)} ({row.file} line {row.line}). Keep at least "
            "one of its words."
        )
    elif len(definition.split()) > MAX_CONTEXTUAL_WORDS:
        errors.append(
            f"has a glossary gloss of {len(definition.split())} words; the limit is "
            f"{MAX_CONTEXTUAL_WORDS}."
        )
    if not errors:
        token["defSource"] = "glossary"
        token["glossary"] = row.marker
    return errors


def _definition_errors(
    written: dict,
    token: dict,
    lexicon: Lexicon,
    context_text: str,
    glossary: Glossary | None = None,
) -> list[str]:
    """Check one word token's defSource/basis and record them on the token."""
    key = token["k"]
    source = written.get("defSource")
    basis = written.get("basis")
    definition = token["def"]
    row = (glossary.get(key) or glossary.get(canonical_simplified(token["t"]))) if glossary else None

    if source == "glossary":
        if row is not None and row.kind == "term" and key in lexicon:
            return [
                f"is in CC-CEDICT, and its glossary row ({row.file} line {row.line}) is a "
                '"term", not an "override", so CC-CEDICT wins: use "defSource": "dictionary". '
                f"Candidates: {_candidate_hint(lexicon, token['t'])}"
            ]
        return _glossary_errors(written, token, row)
    if row is not None and (row.kind == "override" or key not in lexicon):
        # A glossary term is tagged "glossary", so the reader can show where it came from.
        if row.kind == "override" and source in (None, "dictionary"):
            pass  # an override may still be used in its CC-CEDICT sense
        else:
            return [
                f"is in the glossary ({row.file} line {row.line}: {json.dumps(row.english)}). "
                'Write "defSource": "glossary" with a def that keeps a word of that english, '
                'and no basis.'
            ]

    if key in lexicon:
        token["defSource"] = "dictionary"
        hint = f"Candidates: {_candidate_hint(lexicon, token['t'])}"
        if source not in (None, "dictionary"):
            return [
                f'is in CC-CEDICT, so its defSource must be "dictionary" (or omitted), not '
                f'{json.dumps(source)}. Gloss it from a dictionary sense. {hint}'
            ]
        if basis is not None:
            return ['is in CC-CEDICT; dictionary words take no "basis". Remove it.']
        if definition == UNAVAILABLE:
            return [
                f'is in CC-CEDICT, so its def must be a gloss from a dictionary sense, not '
                f'"{UNAVAILABLE}". {hint}'
            ]
        if not gloss_matches_dictionary(definition, key, lexicon):
            return [
                f"has the def {json.dumps(definition)}, which matches no CC-CEDICT sense. "
                "Do not replace dictionary definitions: choose the sense that fits and keep "
                f"at least one of its words. {hint}"
            ]
        return []

    fix = (
        f'Write "defSource": "unknown" with def exactly "{UNAVAILABLE}", or, only if the '
        'evidence supports it, "defSource": "contextual" with a short def and a "basis".'
    )
    if source is None:
        return [
            f"is not in CC-CEDICT, so it needs a defSource. {fix} "
            f"{_evidence_hint(token['t'], lexicon)}"
        ]
    if source == "dictionary":
        return [f'is not in CC-CEDICT, so its defSource cannot be "dictionary". {fix}']
    if source not in DEF_SOURCES:
        return [f"has an unrecognized defSource {json.dumps(source)}. {fix}"]
    token["defSource"] = source

    if source == "unknown":
        errors = []
        if definition != UNAVAILABLE:
            errors.append(
                f'is "unknown", so its def must be exactly "{UNAVAILABLE}", not '
                f'{json.dumps(definition)}. Use "contextual" with a basis if you have evidence.'
            )
        if basis is not None:
            errors.append('is "unknown"; remove its "basis" (only contextual glosses take one).')
        return errors

    errors = []
    if definition == UNAVAILABLE:
        errors.append(f'is "contextual" but has no gloss. Use "unknown" for "{UNAVAILABLE}".')
    elif len(definition.split()) > MAX_CONTEXTUAL_WORDS:
        errors.append(
            f"has a contextual gloss of {len(definition.split())} words; the limit is "
            f'{MAX_CONTEXTUAL_WORDS}. Shorten it, or use "unknown".'
        )
    kinds = ", ".join(f'"{kind}"' for kind in BASIS_KINDS)
    if (
        not isinstance(basis, dict)
        or not isinstance(basis.get("note"), str)
        or not basis["note"].strip()
    ):
        errors.append(
            'is "contextual" but has no valid basis. Add "basis": {"kind": one of '
            f'{kinds}, "note": the evidence}}, or use "unknown". '
            f"{_evidence_hint(token['t'], lexicon)}"
        )
        return errors
    kind, note = basis.get("kind"), basis["note"].strip()
    if kind not in BASIS_KINDS:
        errors.append(
            f"has an unrecognized basis kind {json.dumps(kind)}; use one of {kinds}, "
            'or "unknown".'
        )
        return errors
    if len(note) > MAX_BASIS_NOTE:
        errors.append(f"has a basis note over {MAX_BASIS_NOTE} characters. Shorten it.")
    if kind in ("components", "transparent_compound"):
        parts = gap_evidence(token["t"], lexicon)
        if len(key) < 2 or not parts:
            errors.append(
                f'has basis "{kind}", but it has no dictionary components to build on. '
                'Use "sentence_context" if the text explains it, otherwise "unknown".'
            )
        elif not any(char in note for char in token["t"] + key):
            errors.append(
                f'has basis "{kind}", but the note names none of its characters. Name the '
                f"components the gloss comes from. {_evidence_hint(token['t'], lexicon)}"
            )
    elif not _quotes_context(note, token["t"], context_text):
        what = (
            "the identifying words (a role, title, work or event; two or more characters, "
            "other than the name itself), or give only the kind of name"
            if kind == "identified_referent"
            else "the words (two or more characters, other than the word itself) that "
            "establish the meaning"
        )
        errors.append(
            f'has basis "{kind}", but the note quotes nothing from the text. Quote {what}, '
            'or use "unknown".'
        )
    if not errors:
        token["basis"] = {"kind": kind, "note": note}
    return errors


def _quotes_context(note: str, word: str, text: str) -> bool:
    context = text.replace(word, "\n")
    for run in HAN_PATTERN.findall(note):
        if any(run[i : i + 2] in context for i in range(len(run) - 1)):
            return True
    return False


def _candidate_hint(lexicon: Lexicon, word: str, limit: int = 6, width: int = 160) -> str:
    candidates = lexicon.candidates(word) or []
    parts = []
    for candidate in candidates[:limit]:
        definition = candidate["def"]
        if len(definition) > width:
            definition = definition[: width - 1] + "…"
        parts.append(f"[{candidate['py']}] {definition}")
    return json.dumps(parts, ensure_ascii=False)


def _join_hint(expected: str, item: dict) -> str:
    tokens = item.get("tokens")
    if not isinstance(tokens, list):
        return ""
    joined = "".join(
        token["t"] for token in tokens if isinstance(token, dict) and isinstance(token.get("t"), str)
    )
    if joined == expected:
        return ""
    index = next(
        (i for i, (a, b) in enumerate(zip(joined, expected)) if a != b),
        min(len(joined), len(expected)),
    )
    return (
        f" Joined tokens differ from the source at character {index}: "
        f"expected {expected[index:index + 6]!r}, got {joined[index:index + 6]!r}."
    )


def _schema_errors(sentences: list[dict], lexicon: Lexicon) -> list[str]:
    counts: Counter[str] = Counter(
        token["k"] for sentence in sentences for token in sentence["tokens"] if not token.get("p")
    )
    words = {}
    for key, count in counts.items():
        entry = lexicon.word_entry(key)
        entry["countInText"] = count
        words[key] = entry
    document = {
        "schemaVersion": 1,
        "textId": "chunk",
        "title": "chunk",
        "script": "mixed",
        "createdAt": "1970-01-01",
        "stats": {
            "wordCount": sum(counts.values()),
            "uniqueWords": len(counts),
            "hskCoverage": {},
        },
        "paragraphs": [{"sentences": sentences}],
        "words": words,
    }
    return [f"Schema: {error}" for error in validate_text_file(document)]


def validate(work_dir: Path, number: int) -> dict:
    manifest = _load_manifest(work_dir)
    chunk = next((item for item in manifest["chunks"] if item["number"] == number), None)
    if chunk is None:
        raise SkillError(f"Chunk {number} does not exist.")
    if chunk["status"] != "pending":
        return {"chunk": number, "status": chunk["status"], "errors": [], "note": "already final"}

    output_path = _chunk_path(work_dir, number, "output")
    errors: list[str]
    if not output_path.is_file():
        raise SkillError(f"Write the chunk output to {output_path} before validating.")
    try:
        output = _read_json(output_path)
    except json.JSONDecodeError as error:
        accepted, errors = [], [f"Output is not valid JSON: {error}"]
    else:
        accepted, errors = check_chunk_output(
            _read_json(_chunk_path(work_dir, number, "input")),
            output,
            _lexicon(manifest),
            (work_dir / "source.txt").read_text(encoding="utf-8"),
            _glossary(manifest),
        )

    if not errors:
        _write_json(_chunk_path(work_dir, number, "result"), accepted)
        chunk["status"] = "done"
        chunk["errors"] = []
    else:
        chunk["attempts"] += 1
        chunk["errors"] = errors
        if chunk["attempts"] > manifest["retries"]:
            chunk["status"] = "fallback"
    _save_manifest(work_dir, manifest)
    return {
        "chunk": number,
        "status": chunk["status"] if chunk["status"] != "pending" else "retry",
        "attemptsUsed": chunk["attempts"],
        "retriesRemaining": max(0, manifest["retries"] - chunk["attempts"])
        if chunk["status"] == "pending"
        else 0,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# assemble


def assemble(work_dir: Path, *, overwrite: bool = False) -> dict:
    from .cli import write_outputs

    manifest = _load_manifest(work_dir)
    pending = [chunk["number"] for chunk in manifest["chunks"] if chunk["status"] == "pending"]
    if pending:
        raise SkillError(f"Chunks still pending: {pending}. Annotate and validate them first.")

    queue: list[tuple[dict, bool]] = []
    failures: list[str] = []
    fallback_chunks = []
    for chunk in manifest["chunks"]:
        number = chunk["number"]
        if chunk["status"] == "done":
            queue.extend((item, False) for item in _read_json(_chunk_path(work_dir, number, "result")))
        else:
            fallback_chunks.append(number)
            failures.extend(f"chunk {number}: {error}" for error in chunk["errors"])
            queue.extend((item, True) for item in _read_json(_chunk_path(work_dir, number, "local")))

    position = 0
    # key -> occurrences of Claude-written contextual glosses, for the words map and report
    contextual: dict[str, list[dict]] = {}
    glossed: dict[str, list[dict]] = {}
    occurrences: dict[str, list[str]] = {}

    def annotate(sentence: str) -> tuple[str, list[dict], dict]:
        nonlocal position
        if position >= len(queue):
            raise SkillError("Work files have fewer sentences than the source.")
        item, fallback = queue[position]
        position += 1
        if "".join(token["t"] for token in item["tokens"]) != sentence:
            raise SkillError(f"Stored annotation for {item['id']} does not match the source.")
        tokens = []
        for stored in item["tokens"]:
            # Provenance moves to the word entry; tokens keep the plain schema-v1 shape.
            token = {
                k: v for k, v in stored.items() if k not in ("defSource", "basis", "glossary")
            }
            if not token.get("p"):
                occurrences.setdefault(token["k"], []).append(item["id"])
                if stored.get("defSource") == "glossary" and stored.get("glossary"):
                    glossed.setdefault(token["k"], []).append(
                        {"t": token["t"], "def": token["def"], "glossary": stored["glossary"],
                         "sentenceId": item["id"]}
                    )
                if stored.get("defSource") == "contextual" and stored.get("basis"):
                    contextual.setdefault(token["k"], []).append(
                        {"t": token["t"], "def": token["def"], "basis": stored["basis"],
                         "sentenceId": item["id"]}
                    )
            tokens.append(token)
        return item["translation"], tokens, {"fallback": fallback}

    source = (work_dir / "source.txt").read_text(encoding="utf-8")
    lexicon = _lexicon(manifest)
    data, normalized, report = build_text_file(
        source=source,
        title=manifest["title"],
        script_option=manifest["scriptOption"],
        annotate=annotate,
        lexicon=lexicon,
    )
    if position != len(queue):
        raise SkillError("Work files have more sentences than the source.")
    if data["textId"] != manifest["textId"]:
        raise SkillError("Work directory source does not match its manifest.")

    review = _apply_provenance(data["words"], lexicon, contextual, occurrences, glossed)
    report.update(review)

    report.update(
        {
            "mode": "skill",
            "chunkSize": manifest["chunkSize"],
            "chunkCount": len(manifest["chunks"]),
            "chunksFallbackToLocal": fallback_chunks,
            "chunkRetryAttempts": sum(
                chunk["attempts"] for chunk in manifest["chunks"] if chunk["status"] == "done"
            ),
            "validationFailures": failures,
        }
    )
    errors = validate_text_file(data)
    if errors:
        raise SkillError("Generated JSON failed schema-v1 validation: " + " ".join(errors))
    json_path, source_path, report_path = write_outputs(
        Path(manifest["outputDir"]), data["textId"], data, normalized, report, overwrite
    )
    return {
        "json": str(json_path),
        "source": str(source_path),
        "report": str(report_path),
        "title": manifest["title"],
        "sentenceCount": report["sentencesProcessed"],
        "chunkCount": len(manifest["chunks"]),
        "chunksFallbackToLocal": fallback_chunks,
        "unmatchedDictionaryWords": report["unmatchedDictionaryWords"],
        "unmatchedHskWords": report["unmatchedHskWords"],
        "definitionSources": report["definitionSources"],
        "identifiedReferents": [
            {"word": item["word"], "def": item["def"]} for item in report["identifiedReferents"]
        ],
        "contextualGlosses": [
            {"word": item["word"], "def": item["def"], "basis": item["basis"]["kind"]}
            for item in report["contextualGlosses"]
        ],
        "glossaryTerms": [
            {"word": item["word"], "def": item["def"], "file": item["file"]}
            for item in report["glossaryTerms"]
        ],
        "unknownDefinitions": [item["word"] for item in report["unknownDefinitions"]],
    }


def _apply_provenance(
    words: dict[str, dict],
    lexicon: Lexicon,
    contextual: dict[str, list[dict]],
    occurrences: dict[str, list[str]],
    glossed: dict[str, list[dict]] | None = None,
) -> dict:
    """Add defSource/basis/glossary to each word entry and build the report's review lists.

    A word with a validated glossary gloss is "glossary" (this includes an override of a
    CC-CEDICT word). Otherwise a word in CC-CEDICT is "dictionary"; a missing word is
    "contextual" when any occurrence carries a validated contextual gloss, and "unknown"
    otherwise (including fallback chunks). The entry's def stays the dictionary field
    (the placeholder for glossary, contextual and unknown words), so a gloss written for
    this text is never shown as a dictionary fact.
    """
    glossed = glossed or {}
    counts: Counter[str] = Counter()
    glosses: list[dict] = []
    terms: list[dict] = []
    unknown: list[dict] = []
    for key in sorted(words):
        entry = words[key]
        if key in glossed:
            entry["defSource"] = "glossary"
            entry["def"] = UNAVAILABLE
            entry["glossary"] = dict(glossed[key][0]["glossary"])
            grouped_terms: dict[tuple, dict] = {}
            for record in glossed[key]:
                marker = record["glossary"]
                group = grouped_terms.setdefault(
                    (record["def"], marker["file"], marker["source"]),
                    {"word": key, "t": record["t"], "def": record["def"], **marker,
                     "sentenceIds": []},
                )
                if record["sentenceId"] not in group["sentenceIds"]:
                    group["sentenceIds"].append(record["sentenceId"])
            terms.extend(grouped_terms.values())
        elif key in lexicon:
            entry["defSource"] = "dictionary"
        elif key in contextual:
            entry["defSource"] = "contextual"
            entry["basis"] = dict(contextual[key][0]["basis"])
            grouped: dict[tuple, dict] = {}
            for record in contextual[key]:
                basis = record["basis"]
                group = grouped.setdefault(
                    (record["def"], basis["kind"], basis["note"]),
                    {"word": key, "t": record["t"], "def": record["def"],
                     "basis": dict(basis), "sentenceIds": []},
                )
                if record["sentenceId"] not in group["sentenceIds"]:
                    group["sentenceIds"].append(record["sentenceId"])
            glosses.extend(grouped.values())
        else:
            entry["defSource"] = "unknown"
            unknown.append(
                {"word": key, "sentenceIds": list(dict.fromkeys(occurrences.get(key, [])))}
            )
        counts[entry["defSource"]] += 1
    # Identifications of real people or organizations are the riskiest glosses: listed apart.
    referents = [item for item in glosses if item["basis"]["kind"] == "identified_referent"]
    return {
        "definitionSources": {source: counts[source] for source in DEF_SOURCES},
        "identifiedReferents": referents,
        "contextualGlosses": [item for item in glosses if item not in referents],
        "glossaryTerms": terms,
        "unknownDefinitions": unknown,
    }


# ---------------------------------------------------------------------------
# CLI


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zh-preprocessor skill",
        description="Chunked work-file pipeline used by the zh-reader-annotate Claude skill.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    prep = commands.add_parser("prepare", help="Segment a text and write chunk work files.")
    prep.add_argument("input", type=Path)
    prep.add_argument("--title")
    prep.add_argument("--script", choices=("auto", "simp", "trad"), default="auto")
    prep.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_DIR)
    prep.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    prep.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    prep.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    prep.add_argument("--data-dir", type=Path, default=DATA_DIR)
    prep.add_argument("--cedict", type=Path)
    prep.add_argument("--hsk", type=Path)
    prep.add_argument(
        "--glossary", type=Path, action="append", default=[], dest="glossaries",
        help="Domain glossary TSV (repeatable). Its term/override rows gloss tokens.",
    )

    nxt = commands.add_parser("next", help="Show the next pending chunk.")
    nxt.add_argument("work_dir", type=Path)

    val = commands.add_parser("validate", help="Validate Claude's output for one chunk.")
    val.add_argument("work_dir", type=Path)
    val.add_argument("chunk", type=int)

    asm = commands.add_parser("assemble", help="Merge chunks and write the three outputs.")
    asm.add_argument("work_dir", type=Path)
    asm.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    options = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if options.command == "prepare":
            result = prepare(
                options.input,
                title=options.title,
                script=options.script,
                output_dir=options.out,
                work_root=options.work_root,
                chunk_size=options.chunk_size,
                retries=options.retries,
                cedict=options.cedict,
                hsk=options.hsk,
                data_dir=options.data_dir,
                glossaries=options.glossaries,
            )
            exit_code = 0
        elif options.command == "next":
            result = next_chunk(options.work_dir.expanduser())
            exit_code = 0
        elif options.command == "validate":
            result = validate(options.work_dir.expanduser(), options.chunk)
            exit_code = 1 if result["status"] == "retry" else 0
        else:
            result = assemble(options.work_dir.expanduser(), overwrite=options.overwrite)
            exit_code = 0
    except (SkillError, OSError, UnicodeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code
