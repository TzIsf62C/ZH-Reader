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


def reader_validation(data: dict) -> dict:
    """Run the reader's TypeScript validateTextFile on data (requires Node.js)."""
    repository = Path(__file__).resolve().parents[2]
    schema_url = (repository / "reader/src/core/schema.ts").as_uri()
    script = (
        f"import {{ validateTextFile }} from {json.dumps(schema_url)};"
        "import { readFileSync } from 'node:fs';"
        "console.log(JSON.stringify(validateTextFile(JSON.parse(readFileSync(0, 'utf8')))));"
    )
    result = subprocess.run(
        [shutil.which("node"), "--experimental-strip-types", "--input-type=module", "-e", script],
        input=json.dumps(data, ensure_ascii=False),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


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
        result = reader_validation(data)
        self.assertTrue(result["ok"], result)

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


from zh_preprocessor import skill  # noqa: E402

GOOD_TOKENS = {
    "我去银行。": [
        {"t": "我", "py": "wǒ", "def": "I"},
        {"t": "去", "py": "qù", "def": "to go"},
        {"t": "银行", "py": "yínháng", "def": "bank"},
        {"t": "。"},
    ],
    "今天我去。": [
        {"t": "今天", "py": "jīntiān", "def": "today"},
        {"t": "我", "py": "wǒ", "def": "I"},
        {"t": "去", "py": "qù", "def": "to go"},
        {"t": "。"},
    ],
}


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.root = self.fixture.root
        self.output = self.root / "out"

    def tearDown(self):
        self.fixture.close()

    def _prepare(self, text="我去银行。今天我去。\n\n我去银行。", **kwargs):
        source = self.root / "input.txt"
        source.write_text(text, encoding="utf-8")
        options = {
            "title": "Skill test",
            "output_dir": self.output,
            "work_root": self.root / "work",
            "chunk_size": 2,
            "cedict": self.fixture.cedict,
            "hsk": self.fixture.hsk,
        }
        options.update(kwargs)
        result = skill.prepare(source, **options)
        return Path(result["workDir"]), result

    def _write(self, work_dir, number, sentences):
        path = skill._chunk_path(work_dir, number, "output")
        path.write_text(json.dumps(sentences, ensure_ascii=False), encoding="utf-8")

    def _good_output(self, work_dir, number):
        chunk = json.loads(skill._chunk_path(work_dir, number, "input").read_text("utf-8"))
        return [
            {
                "id": sentence["id"],
                "translation": "A translation.",
                "tokens": GOOD_TOKENS[sentence["text"]],
            }
            for sentence in chunk["sentences"]
        ]

    def _complete(self, work_dir):
        while not (step := skill.next_chunk(work_dir))["done"]:
            self._write(work_dir, step["chunk"], self._good_output(work_dir, step["chunk"]))
            self.assertEqual(skill.validate(work_dir, step["chunk"])["status"], "done")

    def test_prepare_writes_chunks_with_dictionary_candidates(self):
        work_dir, result = self._prepare()
        self.assertEqual(result["sentenceCount"], 3)
        self.assertEqual(result["chunkCount"], 2)
        self.assertEqual(result["chunksPending"], [1, 2])
        chunk = json.loads(skill._chunk_path(work_dir, 1, "input").read_text("utf-8"))
        self.assertEqual([s["id"] for s in chunk["sentences"]], ["p0s0", "p0s1"])
        bank = chunk["sentences"][0]["tokens"][2]
        self.assertEqual(bank["t"], "银行")
        self.assertEqual(bank["candidates"], [{"py": "yín háng", "def": "bank"}])
        self.assertEqual(chunk["sentences"][0]["tokens"][3], {"t": "。"})

    def test_prepare_resumes_existing_work_without_resetting(self):
        work_dir, _ = self._prepare()
        self._write(work_dir, 1, self._good_output(work_dir, 1))
        skill.validate(work_dir, 1)
        _, again = self._prepare()
        self.assertTrue(again["resumed"])
        self.assertEqual(again["chunksDone"], 1)
        self.assertEqual(again["chunksPending"], [2])
        self.assertEqual(skill.next_chunk(work_dir)["chunk"], 2)

    def test_prepare_refuses_output_inside_repository(self):
        repository = Path(__file__).resolve().parents[2]
        with self.assertRaises(skill.SkillError):
            self._prepare(output_dir=repository / "out")

    def test_validate_rejects_altered_text_dropped_token_and_missing_sentence(self):
        work_dir, _ = self._prepare()
        good = self._good_output(work_dir, 1)
        cases = {
            "altered": [{**good[0], "tokens": [{"t": "我", "py": "wǒ", "def": "I"},
                                               {"t": "来", "py": "lái", "def": "come"},
                                               {"t": "银行", "py": "yínháng", "def": "bank"},
                                               {"t": "。"}]}, good[1]],
            "dropped": [{**good[0], "tokens": good[0]["tokens"][:3]}, good[1]],
            "missing": [good[1]],
        }
        lexicon = self.fixture.lexicon
        chunk = json.loads(skill._chunk_path(work_dir, 1, "input").read_text("utf-8"))
        for name, output in cases.items():
            with self.subTest(name):
                _, errors = skill.check_chunk_output(chunk, output, lexicon)
                self.assertTrue(errors)
        self.assertEqual(skill.check_chunk_output(chunk, good, lexicon)[1], [])

    def test_validate_rejects_simplified_swap_in_traditional_text(self):
        work_dir, _ = self._prepare(text="我去銀行。", script="trad")
        swapped = [{"id": "p0s0", "translation": "I go to the bank.", "tokens": GOOD_TOKENS["我去银行。"]}]
        self._write(work_dir, 1, swapped)
        result = skill.validate(work_dir, 1)
        self.assertEqual(result["status"], "retry")
        self.assertIn("reconstruct", " ".join(result["errors"]))
        kept = [{**swapped[0], "tokens": [*swapped[0]["tokens"][:2],
                                          {"t": "銀行", "py": "yínháng", "def": "bank"},
                                          {"t": "。"}]}]
        self._write(work_dir, 1, kept)
        self.assertEqual(skill.validate(work_dir, 1)["status"], "done")
        result = json.loads(skill._chunk_path(work_dir, 1, "result").read_text("utf-8"))
        self.assertEqual(result[0]["tokens"][2]["t"], "銀行")
        self.assertEqual(result[0]["tokens"][2]["k"], "银行")

    def test_validate_rejects_invented_definition_for_unknown_word(self):
        work_dir, _ = self._prepare(text="我去北京。")
        tokens = [{"t": "我", "py": "wǒ", "def": "I"}, {"t": "去", "py": "qù", "def": "to go"},
                  {"t": "北京", "py": "Běijīng", "def": "Beijing"}, {"t": "。"}]
        self._write(work_dir, 1, [{"id": "p0s0", "translation": "I go to Beijing.", "tokens": tokens}])
        result = skill.validate(work_dir, 1)
        self.assertIn(skill.UNAVAILABLE, " ".join(result["errors"]))
        tokens[2]["def"] = skill.UNAVAILABLE
        self._write(work_dir, 1, [{"id": "p0s0", "translation": "I go to Beijing.", "tokens": tokens}])
        self.assertEqual(skill.validate(work_dir, 1)["status"], "done")

    def test_three_failures_fall_back_to_local_annotation(self):
        work_dir, _ = self._prepare()
        for attempt in range(3):
            self._write(work_dir, 1, [])
            result = skill.validate(work_dir, 1)
        self.assertEqual(result["status"], "fallback")
        self._write(work_dir, 2, self._good_output(work_dir, 2))
        skill.validate(work_dir, 2)
        assembled = skill.assemble(work_dir)
        self.assertEqual(assembled["chunksFallbackToLocal"], [1])
        data = json.loads(Path(assembled["json"]).read_text("utf-8"))
        first = data["paragraphs"][0]["sentences"][0]
        self.assertEqual(first["translation"], "")
        self.assertEqual({t.get("src") for t in first["tokens"] if not t.get("p")}, {"local"})
        report = json.loads(Path(assembled["report"]).read_text("utf-8"))
        self.assertEqual(report["sentencesFallbackToLocal"], 2)
        self.assertEqual(report["mode"], "skill")

    def test_assemble_refuses_pending_chunks(self):
        work_dir, _ = self._prepare()
        with self.assertRaises(skill.SkillError):
            skill.assemble(work_dir)

    def test_assemble_matches_local_cli_shape_and_passes_validators(self):
        text = "我去银行。今天我去。\n\n我去银行。"
        work_dir, _ = self._prepare(text=text)
        self._complete(work_dir)
        assembled = skill.assemble(work_dir)
        data = json.loads(Path(assembled["json"]).read_text("utf-8"))
        self.assertEqual(validate_text_file(data), [])
        if shutil.which("node"):
            self.assertTrue(reader_validation(data)["ok"])
        lexicon = self.fixture.lexicon
        local, _, _ = build_text_file(
            text, "Skill test", "auto",
            lambda s: ("", [lexicon.annotate(t) for t in lexicon.segment(s)], {}), lexicon,
        )
        self.assertEqual(data["textId"], local["textId"])
        self.assertEqual(Path(assembled["json"]).name, f'{local["textId"]}.json')
        ids = lambda d: [s["id"] for p in d["paragraphs"] for s in p["sentences"]]
        self.assertEqual(ids(data), ids(local))
        self.assertEqual(data["paragraphs"][0]["sentences"][0]["translation"], "A translation.")
        self.assertTrue(Path(assembled["source"]).is_file())
        self.assertTrue(Path(assembled["report"]).is_file())

    def test_assemble_without_overwrite_leaves_existing_files_untouched(self):
        work_dir, _ = self._prepare()
        self._complete(work_dir)
        assembled = skill.assemble(work_dir)
        paths = [Path(assembled[key]) for key in ("json", "source", "report")]
        before = [path.read_bytes() for path in paths]
        exit_code = skill.main(["assemble", str(work_dir)])
        self.assertEqual(exit_code, 2)
        self.assertEqual([path.read_bytes() for path in paths], before)
        self.assertEqual(skill.main(["assemble", str(work_dir), "--overwrite"]), 0)
