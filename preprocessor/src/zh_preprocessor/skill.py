"""Work-file pipeline for the zh-reader-annotate Claude Code skill.

Claude writes annotations into chunk files inside a session; these commands prepare the
chunks, validate what Claude wrote, and assemble the reader's schema-v1 output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from .claude import AnnotationError, make_tokens
from .data import DATA_DIR
from .lexicon import Lexicon
from .pipeline import build_text_file, output_paths
from .schema import validate_text_file
from .text import canonical_simplified, detect_script, is_han_word, normalize_text
from .text import split_paragraphs, split_sentences

DEFAULT_CHUNK_SIZE = 20
DEFAULT_RETRIES = 2
DEFAULT_OUTPUT_DIR = Path.home() / "Documents" / "zh-reader-out"
DEFAULT_WORK_ROOT = DATA_DIR / "work"
UNAVAILABLE = "[definition unavailable]"
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

    if (work_dir / MANIFEST).is_file():
        manifest = _load_manifest(work_dir)
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
    chunk_input: dict, output: Any, lexicon: Lexicon
) -> tuple[list[dict], list[str]]:
    """Return (accepted sentences, errors). Sentences are only usable when errors is empty."""
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
    accepted = []
    for sentence in expected:
        item = by_id.get(sentence["id"])
        if item is None:
            continue
        try:
            translation, tokens = make_tokens(
                sentence["text"], item.get("translation"), item.get("tokens")
            )
        except AnnotationError as error:
            errors.append(f"{sentence['id']}: {error}{_join_hint(sentence['text'], item)}")
            continue
        for token in tokens:
            if token.get("p") is True:
                continue
            if token["k"] not in lexicon and token["def"] != UNAVAILABLE:
                errors.append(
                    f'{sentence["id"]}: "{token["t"]}" is not in CC-CEDICT; its def must be '
                    f'exactly "{UNAVAILABLE}" (or re-segment into dictionary words).'
                )
        accepted.append({"id": sentence["id"], "translation": translation, "tokens": tokens})

    if not errors:
        errors.extend(_schema_errors(accepted, lexicon))
    return accepted, errors


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
            _read_json(_chunk_path(work_dir, number, "input")), output, _lexicon(manifest)
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

    def annotate(sentence: str) -> tuple[str, list[dict], dict]:
        nonlocal position
        if position >= len(queue):
            raise SkillError("Work files have fewer sentences than the source.")
        item, fallback = queue[position]
        position += 1
        if "".join(token["t"] for token in item["tokens"]) != sentence:
            raise SkillError(f"Stored annotation for {item['id']} does not match the source.")
        return item["translation"], item["tokens"], {"fallback": fallback}

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
