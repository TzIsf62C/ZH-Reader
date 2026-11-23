from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Callable

from .lexicon import Lexicon
from .text import detect_script, normalize_text, split_paragraphs, split_sentences

AnnotateSentence = Callable[[str], tuple[str, list[dict[str, str | bool]], dict[str, int | float | bool]]]


def build_text_file(
    source: str,
    title: str,
    script_option: str,
    annotate: AnnotateSentence,
    lexicon: Lexicon,
) -> tuple[dict, str, dict]:
    normalized = normalize_text(source)
    if not normalized:
        raise ValueError("The input contains no text after whitespace normalization.")

    detection = detect_script(normalized)
    script = detection.script if script_option == "auto" else script_option
    paragraphs = split_paragraphs(normalized)
    output_paragraphs = []
    word_counts: Counter[str] = Counter()
    sentence_count = 0
    sentences_retried = 0
    retry_attempts = 0
    fallback_count = 0
    validation_failures: list[str] = []
    input_tokens = 0
    output_tokens = 0
    api_calls = 0
    cache_hits = 0
    estimated_cost = 0.0
    actual_cost = 0.0

    for paragraph_index, paragraph in enumerate(paragraphs):
        sentences = []
        for sentence in split_sentences(paragraph, paragraph_index):
            translation, tokens, usage = annotate(sentence.text)
            sentence_count += 1
            retries = int(usage.get("retries", 0))
            sentences_retried += int(retries > 0)
            retry_attempts += retries
            fallback_count += int(bool(usage.get("fallback", False)))
            validation_failures.extend(usage.get("validation_failures", []))  # type: ignore[arg-type]
            input_tokens += int(usage.get("input_tokens", 0))
            output_tokens += int(usage.get("output_tokens", 0))
            api_calls += int(usage.get("api_calls", 0))
            cache_hits += int(usage.get("cache_hits", 0))
            estimated_cost += float(usage.get("estimated_cost", 0.0))
            actual_cost += float(usage.get("actual_cost", 0.0))
            if "".join(str(token["t"]) for token in tokens) != sentence.text:
                raise ValueError(f"Annotation changed source text for sentence {sentence.id}.")
            for token in tokens:
                if token.get("p") is True:
                    continue
                key = str(token["k"])
                word_counts[key] += 1
            sentences.append(
                {"id": sentence.id, "translation": translation, "tokens": tokens}
            )
        output_paragraphs.append({"sentences": sentences})

    words = {}
    for key, count in word_counts.items():
        entry = lexicon.word_entry(key)
        entry["countInText"] = count
        words[key] = entry

    total_words = sum(word_counts.values())
    source_token_count = sum(
        len(sentence["tokens"])
        for paragraph in output_paragraphs
        for sentence in paragraph["sentences"]
    )
    level_counts: Counter[str] = Counter(
        str(words[key]["hsk"]) if words[key]["hsk"] is not None else "none"
        for key, count in word_counts.items()
        for _ in range(count)
    )
    hsk_coverage = (
        {level: round(count / total_words, 4) for level, count in sorted(level_counts.items())}
        if total_words
        else {}
    )
    text_id = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
    data = {
        "schemaVersion": 1,
        "textId": text_id,
        "title": title,
        "script": script,
        "createdAt": date.today().isoformat(),
        "stats": {
            "wordCount": total_words,
            "uniqueWords": len(words),
            "hskCoverage": hsk_coverage,
        },
        "paragraphs": output_paragraphs,
        "words": words,
    }

    unmatched_cedict = sorted(key for key in word_counts if key not in lexicon)
    unmatched_hsk = lexicon.unmatched_hsk(word_counts)
    report = {
        "textId": text_id,
        "title": title,
        "scriptDetected": detection.script,
        "scriptDetectionRatio": detection.ratio,
        "scriptOverride": script_option if script_option != "auto" else None,
        "sentencesProcessed": sentence_count,
        "sentencesRetried": sentences_retried,
        "retryAttempts": retry_attempts,
        "sentencesFallbackToLocal": fallback_count,
        "tokenCount": source_token_count,
        "wordTokenCount": total_words,
        "uniqueWords": len(word_counts),
        "hskMatches": len(word_counts) - len(unmatched_hsk),
        "unmatchedHskWords": unmatched_hsk,
        "unmatchedDictionaryWords": unmatched_cedict,
        "validationFailures": validation_failures,
        "apiCalls": api_calls,
        "cacheHits": cache_hits,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "estimatedCostUsd": round(estimated_cost, 6),
        "actualCostUsd": round(actual_cost, 6),
        "preflightEstimatedCostUsd": None,
        "sourceSidecar": f"{text_id}.source.txt",
    }
    return data, normalized, report


def output_paths(output_dir: Path, text_id: str) -> tuple[Path, Path, Path]:
    return (
        output_dir / f"{text_id}.json",
        output_dir / f"{text_id}.source.txt",
        output_dir / f"{text_id}.report.json",
    )
