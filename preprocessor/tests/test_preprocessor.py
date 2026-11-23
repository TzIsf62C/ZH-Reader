from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from zh_preprocessor.claude import ClaudeAnnotator, CostLimitExceeded
from zh_preprocessor.cli import _write_outputs
from zh_preprocessor.lexicon import Lexicon
from zh_preprocessor.pipeline import build_text_file
from zh_preprocessor.schema import validate_text_file
from zh_preprocessor.text import detect_script, normalize_text, split_sentences


class Fixture:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cedict = self.root / "cedict.txt"
        self.cedict.write_text(
            "# CC-CEDICT fixture\n"
            "我 我 [wo3] /I; me/\n"
            "去 去 [qu4] /to go/\n"
            "银行 銀行 [yin2 hang2] /bank/\n"
            "今天 今天 [jin1 tian1] /today/\n",
            encoding="utf-8",
        )
        self.hsk = self.root / "hsk.json"
        self.hsk.write_text(
            json.dumps(
                [
                    {"simplified": "我", "level": ["new-1"]},
                    {"simplified": "去", "level": ["newest-1", "new-2"]},
                    {"simplified": "银行", "level": ["old-2", "new-3"]},
                ]
            ),
            encoding="utf-8",
        )
        self.lexicon = Lexicon(self.cedict, self.hsk)

    def close(self):
        self.temporary.cleanup()


class TextProcessingTests(unittest.TestCase):
    def test_normalizes_line_endings_and_preserves_paragraph_boundaries(self):
        self.assertEqual(
            normalize_text("  今天 \r\n 去银行。 \r\n\r\n\r\n再见  "),
            "今天\n去银行。\n\n再见",
        )

    def test_detects_simplified_traditional_and_mixed_text(self):
        self.assertEqual(detect_script("银行").script, "simp")
        self.assertEqual(detect_script("銀行").script, "trad")
        mixed = detect_script("银行和銀行")
        self.assertEqual(mixed.script, "mixed")
        self.assertGreater(mixed.ratio, 0)

    def test_sentence_split_keeps_closing_quotes(self):
        sentences = split_sentences("他说：「好。」然后走！", 0)
        self.assertEqual([sentence.text for sentence in sentences], ["他说：「好。」", "然后走！"])
        self.assertEqual([sentence.id for sentence in sentences], ["p0s0", "p0s1"])


class LocalPipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()

    def tearDown(self):
        self.fixture.close()

    def test_local_output_matches_schema_and_canonicalizes_both_scripts(self):
        source = "我去银行。 銀行"

        def annotate(sentence):
            return "", [
                self.fixture.lexicon.annotate(token)
                for token in self.fixture.lexicon.segment(sentence)
            ], {}

        data, normalized, report = build_text_file(
            source, "Sample", "auto", annotate, self.fixture.lexicon
        )
        self.assertEqual(normalized, source)
        self.assertEqual(data["script"], "mixed")
        self.assertEqual(data["words"]["银行"]["countInText"], 2)
        self.assertEqual(data["words"]["银行"]["hsk"], "3")
        self.assertEqual(data["stats"]["wordCount"], 4)
        self.assertEqual(report["sentencesProcessed"], 2)
        self.assertEqual(validate_text_file(data), [])
        reconstructed = "".join(
            token["t"]
            for sentence in data["paragraphs"][0]["sentences"]
            for token in sentence["tokens"]
        )
        self.assertEqual(reconstructed, source)

    def test_numbers_latin_whitespace_and_punctuation_are_nonwords(self):
        tokens = [
            self.fixture.lexicon.annotate(token)
            for token in self.fixture.lexicon.segment("我 9 AM。")
        ]
        self.assertEqual("".join(token["t"] for token in tokens), "我 9 AM。")
        self.assertEqual([token["t"] for token in tokens if token.get("p")], [" 9 AM。"])

    def test_rejects_empty_source(self):
        with self.assertRaisesRegex(ValueError, "no text"):
            build_text_file(
                "  \n \n",
                "Empty",
                "auto",
                lambda sentence: ("", [], {}),
                self.fixture.lexicon,
            )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for reader schema integration.")
    def test_generated_json_is_accepted_by_reader_validator(self):
        def annotate(sentence):
            return "", [
                self.fixture.lexicon.annotate(token)
                for token in self.fixture.lexicon.segment(sentence)
            ], {}

        data, _, _ = build_text_file(
            "我去银行。", "Schema integration", "auto", annotate, self.fixture.lexicon
        )
        repository = Path(__file__).resolve().parents[2]
        schema_url = (repository / "reader/src/core/schema.ts").as_uri()
        script = (
            f"import {{ validateTextFile }} from {json.dumps(schema_url)};"
            "import { readFileSync } from 'node:fs';"
            "console.log(JSON.stringify(validateTextFile(JSON.parse(readFileSync(0, 'utf8')))));"
        )
        result = subprocess.run(
            [
                shutil.which("node"),
                "--experimental-strip-types",
                "--input-type=module",
                "-e",
                script,
            ],
            input=json.dumps(data, ensure_ascii=False),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertTrue(json.loads(result.stdout)["ok"], result.stdout)

    def test_output_collision_requires_explicit_overwrite(self):
        output = self.fixture.root / "out"
        data = {"textId": "id"}
        paths = _write_outputs(output, "id", data, "source", {"ok": True})
        self.assertEqual(paths[1].read_text(encoding="utf-8"), "source")
        with self.assertRaises(FileExistsError):
            _write_outputs(output, "id", data, "changed", {"ok": False})
        self.assertEqual(paths[1].read_text(encoding="utf-8"), "source")
        _write_outputs(output, "id", data, "changed", {"ok": False}, overwrite=True)
        self.assertEqual(paths[1].read_text(encoding="utf-8"), "changed")

    def test_local_cli_runs_without_api_key_and_writes_three_outputs(self):
        source = self.fixture.root / "input.txt"
        source.write_text("我去银行。", encoding="utf-8")
        output = self.fixture.root / "out"
        environment = os.environ.copy()
        environment.pop("ANTHROPIC_API_KEY", None)
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        command = [
            sys.executable,
            "-m",
            "zh_preprocessor",
            str(source),
            "--mode",
            "local",
            "--cedict",
            str(self.fixture.cedict),
            "--hsk",
            str(self.fixture.hsk),
            "--out",
            str(output),
        ]
        result = subprocess.run(command, env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report_file = next(output.glob("*.report.json"))
        text_id = report_file.name.removesuffix(".report.json")
        json_file = output / f"{text_id}.json"
        generated = json.loads(json_file.read_text(encoding="utf-8"))
        self.assertEqual(generated["schemaVersion"], 1)
        self.assertEqual(validate_text_file(generated), [])
        self.assertEqual(json_file.stat().st_mode & 0o777, 0o600)
        self.assertTrue((output / f'{generated["textId"]}.source.txt').is_file())
        self.assertTrue((output / f'{generated["textId"]}.report.json').is_file())
        repeated = subprocess.run(command, env=environment, capture_output=True, text=True)
        self.assertEqual(repeated.returncode, 2)
        self.assertIn("Output already exists", repeated.stderr)


class FakeMessages:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


def _claude_response(tokens, translation="I went to the bank."):
    payload = json.dumps({"translation": translation, "tokens": tokens})
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=payload)],
        usage=SimpleNamespace(input_tokens=20, output_tokens=10),
    )


def _claude_tokens():
    return [
        {"t": "我", "py": "wǒ", "def": "I"},
        {"t": "去", "py": "qù", "def": "to go"},
        {"t": "银行", "py": "yínháng", "def": "bank"},
        {"t": "。"},
    ]


class ClaudeAnnotationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()

    def tearDown(self):
        self.fixture.close()

    def _annotator(self, responses, **kwargs):
        messages = FakeMessages(responses)
        return (
            ClaudeAnnotator(
                client=SimpleNamespace(messages=messages),
                lexicon=self.fixture.lexicon,
                model="test-model",
                cache_path=self.fixture.root / "cache.sqlite3",
                sleep=lambda _delay: None,
                **kwargs,
            ),
            messages,
        )

    def test_valid_response_is_cached_by_sentence_and_model(self):
        annotator, messages = self._annotator([_claude_response(_claude_tokens())])
        first = annotator.annotate("我去银行。")
        cached_annotator, cached_messages = self._annotator([])
        second = cached_annotator.annotate("我去银行。")
        self.assertEqual(messages.calls, 1)
        self.assertEqual(cached_messages.calls, 0)
        self.assertEqual(first[0], "I went to the bank.")
        self.assertEqual(first[1][2]["k"], "银行")
        self.assertEqual(first[1][2]["src"], "claude")
        self.assertEqual(second[2], {"cache_hits": 1})

    def test_invalid_segmentation_retries_then_falls_back_to_local(self):
        wrong = _claude_response([{"t": "我", "py": "wǒ", "def": "I"}])
        valid = _claude_response(_claude_tokens())
        annotator, messages = self._annotator([wrong, valid], retries=1)
        translation, tokens, usage = annotator.annotate("我去银行。")
        self.assertEqual(messages.calls, 2)
        self.assertEqual(usage["retries"], 1)
        self.assertEqual(translation, "I went to the bank.")

        (self.fixture.root / "cache.sqlite3").unlink()
        self.assertEqual("".join(token["t"] for token in tokens), "我去银行。")

        fallback, fallback_messages = self._annotator([wrong, wrong], retries=1)
        translation, tokens, usage = fallback.annotate("我去银行。")
        self.assertEqual(fallback_messages.calls, 2)
        self.assertEqual(translation, "")
        self.assertTrue(usage["fallback"])
        self.assertTrue(all(token.get("src") == "local" for token in tokens if "k" in token))

    def test_max_cost_stops_before_api_call(self):
        annotator, messages = self._annotator(
            [_claude_response(_claude_tokens())], max_cost=0
        )
        with self.assertRaisesRegex(CostLimitExceeded, "no Claude requests were made"):
            annotator.preflight(["我去银行。"])
        self.assertEqual(messages.calls, 0)

    def test_preflight_sums_unique_uncached_sentences_and_skips_cache_hits(self):
        annotator, messages = self._annotator([_claude_response(_claude_tokens())])
        first_sentence_cost = annotator.preflight(["我去银行。"])
        self.assertGreater(first_sentence_cost, 0)
        annotator.max_cost = first_sentence_cost * 1.05
        annotator.annotate("我去银行。")
        self.assertEqual(annotator.preflight(["我去银行。", "我去银行。"]), 0)
        with self.assertRaises(CostLimitExceeded):
            annotator.preflight(["我去银行。", "银行我去。"])
        self.assertGreater(annotator.preflight_estimate, 0)
        self.assertEqual(messages.calls, 1)


if __name__ == "__main__":
    unittest.main()
