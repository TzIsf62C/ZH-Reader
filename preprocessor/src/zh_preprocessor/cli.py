from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from .claude import DEFAULT_MODEL, ClaudeAnnotator
from .data import DATA_DIR, install_data
from .lexicon import Lexicon
from .pipeline import build_text_file, output_paths
from .schema import validate_text_file
from .text import normalize_text, split_paragraphs, split_sentences


def _arguments(args: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="zh-preprocessor",
        description="Annotate Chinese UTF-8 text for import into ZH Reader.",
    )
    parser.add_argument("input", type=Path, help="Path to a UTF-8 text file.")
    parser.add_argument("--title", help="Display title (defaults to the input filename).")
    parser.add_argument("--script", choices=("auto", "simp", "trad"), default="auto")
    parser.add_argument("--mode", choices=("claude", "local"), default="claude")
    parser.add_argument("--model", default=os.environ.get("ZH_READER_CLAUDE_MODEL", DEFAULT_MODEL))
    parser.add_argument("--out", type=Path, default=Path("./out"), help="Output directory.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--cedict", type=Path, help="CC-CEDICT text, gzip, or zip file.")
    parser.add_argument("--hsk", type=Path, help="complete-hsk-vocabulary JSON file.")
    parser.add_argument("--cache", type=Path, help="Claude SQLite cache file.")
    parser.add_argument("--max-cost", type=float, help="Maximum estimated Claude spend in USD.")
    parser.add_argument("--retries", type=int, default=2, help="Claude retry limit (default: 2).")
    parser.add_argument(
        "--input-price-per-million",
        type=float,
        default=3.0,
        help="Claude input price in USD per million tokens (default: 3.00).",
    )
    parser.add_argument(
        "--output-price-per-million",
        type=float,
        default=15.0,
        help="Claude output price in USD per million tokens (default: 15.00).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Explicitly replace output files with the same content hash.",
    )
    return parser.parse_args(args)


def _install_data(args: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="zh-preprocessor setup-data",
        description="Download local CC-CEDICT and HSK data for offline annotation.",
    )
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    options = parser.parse_args(args)
    cedict, hsk = install_data(options.data_dir)
    print(f"Installed CC-CEDICT: {cedict}")
    print(f"Installed HSK vocabulary: {hsk}")
    return 0


def write_outputs(
    output_dir: Path,
    text_id: str,
    data: dict,
    source: str,
    report: dict,
    overwrite: bool = False,
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path, source_path, report_path = output_paths(output_dir, text_id)
    contents = (
        (json_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n"),
        (source_path, source),
        (report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n"),
    )
    if not overwrite:
        conflicts = [str(path) for path, _ in contents if path.exists()]
        if conflicts:
            raise FileExistsError(
                "Output already exists; choose a different --out directory or pass --overwrite: "
                + ", ".join(conflicts)
            )
        created: list[Path] = []
        try:
            for path, content in contents:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                created.append(path)
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
                    file.write(content)
        except OSError:
            for path in created:
                path.unlink(missing_ok=True)
            raise
        return json_path, source_path, report_path

    temporary_paths: list[Path] = []
    try:
        staged: list[tuple[Path, Path]] = []
        for path, content in contents:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=output_dir, delete=False
            ) as file:
                file.write(content)
                temporary = Path(file.name)
            temporary_paths.append(temporary)
            staged.append((temporary, path))
        for temporary, path in staged:
            os.replace(temporary, path)
            temporary_paths.remove(temporary)
    finally:
        for temporary in temporary_paths:
            temporary.unlink(missing_ok=True)
    return json_path, source_path, report_path


_write_outputs = write_outputs


def _run(args: list[str]) -> int:
    options = _arguments(args)
    if options.retries < 0:
        raise ValueError("--retries must be zero or greater.")
    if options.max_cost is not None and options.max_cost < 0:
        raise ValueError("--max-cost must be zero or greater.")
    if options.input_price_per_million < 0 or options.output_price_per_million < 0:
        raise ValueError("Claude token prices must be zero or greater.")
    if options.mode == "local" and options.max_cost is not None:
        raise ValueError("--max-cost is only applicable to --mode claude.")

    source = options.input.read_text(encoding="utf-8")
    title = options.title if options.title is not None else options.input.stem
    lexicon = Lexicon(
        options.cedict or options.data_dir / "cedict_ts.u8",
        options.hsk or options.data_dir / "complete-hsk-vocabulary.json",
    )

    if options.mode == "local":
        def annotate_local(sentence: str) -> tuple[str, list[dict], dict]:
            return "", [lexicon.annotate(token) for token in lexicon.segment(sentence)], {}

        annotate = annotate_local
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for --mode claude.")
        try:
            from anthropic import APIError, Anthropic
        except ImportError as error:
            raise RuntimeError(
                "Claude mode requires the optional dependency. Install with "
                "`python -m pip install -e '.[claude]'`."
            ) from error
        annotator = ClaudeAnnotator(
            client=Anthropic(api_key=api_key, max_retries=0),
            lexicon=lexicon,
            model=options.model,
            cache_path=options.cache or options.data_dir / "claude-cache.sqlite3",
            max_cost=options.max_cost,
            retries=options.retries,
            input_price=options.input_price_per_million,
            output_price=options.output_price_per_million,
            api_error_types=(APIError,),
        )
        normalized = normalize_text(source)
        sentence_texts = [
            sentence.text
            for paragraph_index, paragraph in enumerate(split_paragraphs(normalized))
            for sentence in split_sentences(paragraph, paragraph_index)
        ]
        estimate = annotator.preflight(sentence_texts)
        print(f"Estimated uncached Claude spend: ${estimate:.4f}")
        annotate = annotator.annotate

    data, normalized, report = build_text_file(
        source=source,
        title=title,
        script_option=options.script,
        annotate=annotate,
        lexicon=lexicon,
    )
    if options.mode == "claude":
        report["preflightEstimatedCostUsd"] = round(annotator.preflight_estimate, 6)
        report["model"] = options.model
    report["mode"] = options.mode
    errors = validate_text_file(data)
    if errors:
        raise ValueError("Generated JSON failed schema-v1 validation: " + " ".join(errors))
    json_path, source_path, report_path = write_outputs(
        options.out, data["textId"], data, normalized, report, options.overwrite
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"JSON: {json_path}")
    print(f"Source: {source_path}")
    print(f"Report: {report_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "skill":
        from .skill import main as skill_main

        return skill_main(args[1:])
    if args and args[0] == "setup-data":
        try:
            return _install_data(args[1:])
        except (OSError, ValueError) as error:
            print(f"Error: {error}", file=sys.stderr)
            return 2
    try:
        return _run(args)
    except (OSError, UnicodeError, ValueError, RuntimeError, sqlite3.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
