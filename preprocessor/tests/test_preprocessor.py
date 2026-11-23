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
        self.assertIn("defSource", " ".join(result["errors"]))
        tokens[2].update({"def": skill.UNAVAILABLE, "defSource": "unknown"})
        self._write(work_dir, 1, [{"id": "p0s0", "translation": "I go to Beijing.", "tokens": tokens}])
        self.assertEqual(skill.validate(work_dir, 1)["status"], "done")

    def test_validate_rejects_unavailable_for_dictionary_word_and_offers_candidates(self):
        # A merge Claude could not look up (e.g. jieba split 银 + 行) must not keep the
        # placeholder when the merged word is in CC-CEDICT.
        work_dir, _ = self._prepare(text="我去银行。")
        tokens = [{"t": "我", "py": "wǒ", "def": "I"}, {"t": "去", "py": "qù", "def": "to go"},
                  {"t": "银行", "py": "yínháng", "def": skill.UNAVAILABLE}, {"t": "。"}]
        self._write(work_dir, 1, [{"id": "p0s0", "translation": "I go to the bank.", "tokens": tokens}])
        result = skill.validate(work_dir, 1)
        self.assertEqual(result["status"], "retry")
        message = " ".join(result["errors"])
        self.assertIn("银行", message)
        self.assertIn("bank", message)
        tokens[2]["def"] = "bank"
        self._write(work_dir, 1, [{"id": "p0s0", "translation": "I go to the bank.", "tokens": tokens}])
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


FIXTURES = Path(__file__).resolve().parent / "fixtures"
GAP_SENTENCE = "她发现水槽下面的滤水器坏了。"
GAP_GLOSSES = {
    "今天": "today", "早上": "morning", "在": "at", "厨房": "kitchen", "里": "inside",
    "喝": "to drink", "了": "completed action marker", "一": "one", "杯": "cup", "水": "water",
    "她": "she", "发现": "to discover", "水槽": "sink", "下面": "under", "的": "possessive particle",
    "坏": "broken", "给": "to give", "朋友": "friend", "打电话": "to make a telephone call",
    "笑": "to laugh", "着": "action in progress", "说": "to say", "最近": "recently",
    "很": "very", "买": "to buy", "一个": "a", "新": "new", "现在": "now", "干净": "clean",
    "亚马逊": "Amazon", "创始人": "founder", "年轻": "young", "时": "when", "纽约": "New York",
    "工作": "to work", "我": "I", "邻居": "neighbor", "先生": "Mr.", "也": "also",
    "喜欢": "to like", "唱歌": "to sing", "后来": "later", "搬": "to move", "到": "to reach",
    "西雅图": "Seattle",
}
FILTER_BASIS = {"kind": "transparent_compound", "note": "滤 to filter + 水 water + 器 device"}
NAME_BASIS = {"kind": "components", "note": "莉: used in the transliteration of female names"}
SURNAME_BASIS = {"kind": "sentence_context", "note": "followed by 先生 (Mr.)"}
REFERENT_BASIS = {"kind": "identified_referent",
                  "note": "introduced as 亚马逊的创始人 (founder of Amazon) in p2s0"}
# How the fixture's gap words should be annotated under the name ladder.
GAP_ANNOTATIONS = {
    "滤水器": ("water filter", "contextual", FILTER_BASIS),
    "雪莉": ("female given name", "contextual", NAME_BASIS),
    "斯威夫特": ("surname", "contextual", SURNAME_BASIS),
    "贝索斯": ("Jeff Bezos, founder of Amazon", "contextual", REFERENT_BASIS),
    "叽萌": ("[definition unavailable]", "unknown", None),
}


def gap_token(t, definition, **fields):
    return {"t": t, "py": "x", "def": definition, **fields}


def known(t):
    return gap_token(t, GAP_GLOSSES[t])


class DictionaryGapTests(unittest.TestCase):
    """The skill's dictionary-gap contract: dictionary, contextual (with basis) or unknown."""

    def setUp(self):
        self.fixture = Fixture()
        self.lexicon = Lexicon(FIXTURES / "dictionary-gaps-cedict.txt", self.fixture.hsk)

    def tearDown(self):
        self.fixture.close()

    def check(self, filter_token):
        """Validate GAP_SENTENCE with the 滤水器 token replaced; return the error list."""
        tokens = [known(t) for t in ("她", "发现", "水槽", "下面", "的")]
        tokens += [filter_token, known("坏"), known("了"), {"t": "。"}]
        chunk = {"sentences": [{"id": "p0s0", "text": GAP_SENTENCE}]}
        output = [{"id": "p0s0", "translation": "She found the water filter was broken.",
                   "tokens": tokens}]
        return skill.check_chunk_output(chunk, output, self.lexicon)[1]

    def filter_token(self, definition, **fields):
        return gap_token("滤水器", definition, **fields)

    def test_fixture_text_has_known_invented_name_and_compound_words(self):
        text = (FIXTURES / "dictionary-gaps.txt").read_text("utf-8")
        for word in ("厨房", "水槽", "朋友"):
            self.assertIn(word, self.lexicon)
        for word in GAP_ANNOTATIONS:
            self.assertIn(word, text)
            self.assertNotIn(word, self.lexicon)

    def test_prepare_lists_gap_evidence_and_sentences(self):
        result = skill.prepare(
            FIXTURES / "dictionary-gaps.txt", output_dir=self.fixture.root / "out",
            work_root=self.fixture.root / "work", cedict=FIXTURES / "dictionary-gaps-cedict.txt",
            hsk=self.fixture.hsk,
        )
        chunk = json.loads(skill._chunk_path(Path(result["workDir"]), 1, "input").read_text("utf-8"))
        gaps = {gap["word"]: gap for gap in chunk["gaps"]}
        self.assertTrue({"雪莉", "滤水器", "叽萌"} <= set(gaps))
        self.assertNotIn("厨房", gaps)
        parts = {part["part"]: part["candidates"] for part in gaps["滤水器"]["components"]}
        self.assertEqual(set(parts), {"滤", "水", "器"})
        self.assertIn("to filter", parts["滤"][0]["def"])
        self.assertEqual([s["id"] for s in gaps["滤水器"]["sentences"]], ["p0s1", "p1s1"])
        self.assertEqual(gaps["滤水器"]["sentences"][0]["text"].strip(), GAP_SENTENCE)
        self.assertEqual({p["part"] for p in gaps["雪莉"]["components"]}, {"雪", "莉"})

    def test_prepare_shows_where_a_name_appears_in_other_chunks(self):
        result = skill.prepare(
            FIXTURES / "dictionary-gaps.txt", output_dir=self.fixture.root / "out",
            work_root=self.fixture.root / "work", cedict=FIXTURES / "dictionary-gaps-cedict.txt",
            hsk=self.fixture.hsk, chunk_size=3,
        )
        work_dir = Path(result["workDir"])
        last = json.loads(skill._chunk_path(work_dir, 3, "input").read_text("utf-8"))
        bezos = next(gap for gap in last["gaps"] if gap["word"] == "贝索斯")
        self.assertEqual([s["id"] for s in bezos["sentences"]], ["p3s0"])
        self.assertEqual(bezos["elsewhere"],
                         [{"id": "p2s0", "text": "亚马逊的创始人贝索斯年轻时在纽约工作。"}])

    # --- dictionary words ---------------------------------------------------------------

    def test_unknown_with_placeholder_is_accepted_beside_dictionary_glosses(self):
        self.assertEqual(self.check(self.filter_token(skill.UNAVAILABLE, defSource="unknown")), [])

    def test_dictionary_word_with_changed_definition_is_rejected(self):
        tokens = [known(t) for t in ("她", "发现")] + [gap_token("水槽", "garage")]
        chunk = {"sentences": [{"id": "p0s0", "text": "她发现水槽"}]}
        errors = skill.check_chunk_output(
            chunk, [{"id": "p0s0", "translation": "x", "tokens": tokens}], self.lexicon)[1]
        self.assertEqual(len(errors), 1)
        self.assertIn("matches no CC-CEDICT sense", errors[0])
        self.assertIn("sink", errors[0])  # the fix hint lists the real sense

    def test_dictionary_word_cannot_be_marked_contextual_unknown_or_given_a_basis(self):
        cases = {
            "contextual": {"def": "sink", "defSource": "contextual", "basis": FILTER_BASIS},
            "unknown": {"def": skill.UNAVAILABLE, "defSource": "unknown"},
            "basis": {"def": "sink", "basis": FILTER_BASIS},
            "placeholder": {"def": skill.UNAVAILABLE},
        }
        for name, fields in cases.items():
            with self.subTest(name):
                tokens = [known("她"), known("发现"), {"t": "水槽", "py": "x", **fields}]
                chunk = {"sentences": [{"id": "p0s0", "text": "她发现水槽"}]}
                errors = skill.check_chunk_output(
                    chunk, [{"id": "p0s0", "translation": "x", "tokens": tokens}], self.lexicon)[1]
                self.assertEqual(len(errors), 1, errors)
                self.assertIn("水槽", errors[0])

    # --- words missing from CC-CEDICT -------------------------------------------------

    def test_missing_word_without_defsource_is_rejected_with_evidence(self):
        errors = self.check(self.filter_token(skill.UNAVAILABLE))
        self.assertEqual(len(errors), 1)
        self.assertIn("needs a defSource", errors[0])
        self.assertIn("to filter", errors[0])  # component evidence is offered

    def test_missing_word_cannot_claim_dictionary_or_an_unknown_source(self):
        for source in ("dictionary", "guess"):
            with self.subTest(source):
                errors = self.check(self.filter_token("water filter", defSource=source))
                self.assertEqual(len(errors), 1)
                self.assertIn("defSource", errors[0])

    def test_unknown_must_keep_placeholder_and_take_no_basis(self):
        invented = self.check(self.filter_token("water filter", defSource="unknown"))
        self.assertIn('def must be exactly "[definition unavailable]"', " ".join(invented))
        with_basis = self.check(self.filter_token(skill.UNAVAILABLE, defSource="unknown",
                                                  basis=FILTER_BASIS))
        self.assertIn('remove its "basis"', " ".join(with_basis))

    def test_contextual_with_valid_component_basis_is_accepted(self):
        token = self.filter_token("water filter", defSource="contextual", basis=FILTER_BASIS)
        self.assertEqual(self.check(token), [])

    def test_contextual_without_or_with_invalid_basis_is_rejected(self):
        cases = {
            "missing": None,
            "no note": {"kind": "components", "note": " "},
            "bad kind": {"kind": "intuition", "note": "滤 filter"},
            "not a dict": "components",
        }
        for name, basis in cases.items():
            with self.subTest(name):
                fields = {"defSource": "contextual", "def": "water filter"}
                if basis is not None:
                    fields["basis"] = basis
                errors = self.check({"t": "滤水器", "py": "x", **fields})
                self.assertEqual(len(errors), 1, errors)
                self.assertIn("basis", errors[0])

    def test_contextual_gloss_over_twelve_words_is_rejected(self):
        long = "a device that removes impurities from the water supplied to a kitchen sink"
        errors = self.check({"t": "滤水器", "py": "x", "defSource": "contextual", "def": long,
                             "basis": FILTER_BASIS})
        self.assertEqual(len(errors), 1)
        self.assertIn("limit is 12", errors[0])

    def test_contextual_placeholder_is_rejected(self):
        errors = self.check({"t": "滤水器", "py": "x", "defSource": "contextual",
                             "def": skill.UNAVAILABLE, "basis": FILTER_BASIS})
        self.assertIn('Use "unknown"', " ".join(errors))

    def test_component_basis_must_name_components_that_exist(self):
        vague = self.check({"t": "滤水器", "py": "x", "defSource": "contextual",
                            "def": "water filter",
                            "basis": {"kind": "components", "note": "looks like a filter"}})
        self.assertIn("names none of its characters", " ".join(vague))
        # A word whose characters have no dictionary entries has no components to cite.
        lone = skill.check_chunk_output(
            {"sentences": [{"id": "p0s0", "text": "鿏"}]},
            [{"id": "p0s0", "translation": "x", "tokens": [
                {"t": "鿏", "py": "x", "def": "element", "defSource": "contextual",
                 "basis": {"kind": "components", "note": "鿏 radical"}}]}],
            self.lexicon)[1]
        self.assertIn("no dictionary components", " ".join(lone))

    def test_sentence_context_basis_must_quote_the_text(self):
        unquoted = self.check({"t": "滤水器", "py": "x", "defSource": "contextual",
                               "def": "water filter",
                               "basis": {"kind": "sentence_context", "note": "obvious from context"}})
        self.assertIn("quotes nothing from the text", " ".join(unquoted))
        self_quote = self.check({"t": "滤水器", "py": "x", "defSource": "contextual",
                                 "def": "water filter",
                                 "basis": {"kind": "sentence_context", "note": "滤水器"}})
        self.assertIn("quotes nothing from the text", " ".join(self_quote))
        quoted = self.check({"t": "滤水器", "py": "x", "defSource": "contextual",
                             "def": "water filter",
                             "basis": {"kind": "sentence_context",
                                       "note": "located 水槽下面 (under the sink) and 坏了 (broke)"}})
        self.assertEqual(quoted, [])

    # --- names ----------------------------------------------------------------------------

    def check_words(self, words, overrides, source_text=None):
        """Validate one sentence made of `words`; gap words take fields from `overrides`."""
        tokens = []
        for word in words:
            if word in overrides:
                tokens.append({"t": word, "py": "x", **overrides[word]})
            elif word in "，。":
                tokens.append({"t": word})
            else:
                tokens.append(known(word))
        chunk = {"sentences": [{"id": "p0s0", "text": "".join(words)}]}
        output = [{"id": "p0s0", "translation": "x", "tokens": tokens}]
        return skill.check_chunk_output(chunk, output, self.lexicon, source_text)[1]

    @staticmethod
    def annotated(word, definition=None, basis=None):
        default_def, source, default_basis = GAP_ANNOTATIONS[word]
        fields = {"def": definition or default_def, "defSource": source}
        if (basis or default_basis) is not None:
            fields["basis"] = basis or default_basis
        return fields

    def test_names_take_a_category_gloss_with_evidence(self):
        shirley = ["今天", "早上", "，", "雪莉", "在", "厨房", "里", "喝", "了", "一", "杯", "水", "。"]
        self.assertEqual(self.check_words(shirley, {"雪莉": self.annotated("雪莉")}), [])
        swift = ["我", "的", "邻居", "斯威夫特", "先生", "也", "很", "喜欢", "唱歌", "。"]
        self.assertEqual(self.check_words(swift, {"斯威夫特": self.annotated("斯威夫特")}), [])
        # A name still needs evidence: a sentence-context note must quote the text.
        bare = self.check_words(swift, {"斯威夫特": self.annotated(
            "斯威夫特", basis={"kind": "sentence_context", "note": "sounds like a surname"})})
        self.assertIn("quotes nothing from the text", " ".join(bare))

    def test_identified_referent_quotes_identifying_text_from_any_chunk(self):
        later = ["后来", "，", "贝索斯", "搬", "到", "了", "西雅图", "。"]
        source = (FIXTURES / "dictionary-gaps.txt").read_text("utf-8")
        bezos = {"贝索斯": self.annotated("贝索斯")}
        self.assertEqual(self.check_words(later, bezos, source), [])
        # The introduction is in another chunk, so the chunk alone cannot support it.
        self.assertIn("quotes nothing from the text", " ".join(self.check_words(later, bezos)))

    def test_identified_referent_without_identifying_quote_is_rejected(self):
        later = ["后来", "，", "贝索斯", "搬", "到", "了", "西雅图", "。"]
        source = (FIXTURES / "dictionary-gaps.txt").read_text("utf-8")
        for note in ("a famous billionaire", "贝索斯 is well known"):
            with self.subTest(note):
                errors = self.check_words(later, {"贝索斯": self.annotated(
                    "贝索斯", basis={"kind": "identified_referent", "note": note})}, source)
                self.assertEqual(len(errors), 1, errors)
                self.assertIn("identifying words", errors[0])

    def test_identified_referent_quote_relevance_is_not_checked(self):
        # Documents a limit: the validator checks that a quote exists, not that it identifies
        # the person. "喜欢唱歌" is quoted text, so a Taylor Swift guess passes mechanically;
        # only the instructions and the report review catch it.
        swift = ["我", "的", "邻居", "斯威夫特", "先生", "也", "很", "喜欢", "唱歌", "。"]
        guess = {"def": "Taylor Swift, singer", "defSource": "contextual",
                 "basis": {"kind": "identified_referent", "note": "喜欢唱歌 (likes singing)"}}
        self.assertEqual(self.check_words(swift, {"斯威夫特": guess}), [])

    # --- assembly and report ------------------------------------------------------------

    def test_assemble_writes_provenance_and_review_lists(self):
        out = self.fixture.root / "out"
        result = skill.prepare(
            FIXTURES / "dictionary-gaps.txt", output_dir=out, work_root=self.fixture.root / "work",
            cedict=FIXTURES / "dictionary-gaps-cedict.txt", hsk=self.fixture.hsk,
        )
        work_dir = Path(result["workDir"])
        chunk = json.loads(skill._chunk_path(work_dir, 1, "input").read_text("utf-8"))
        output = []
        for sentence in chunk["sentences"]:
            tokens = []
            for token in sentence["tokens"]:
                t = token["t"]
                if "py" not in token:
                    tokens.append({"t": t})
                elif t == "一杯":
                    tokens += [known("一"), known("杯")]
                elif t in GAP_ANNOTATIONS:
                    tokens.append({"t": t, "py": "x", **self.annotated(t)})
                else:
                    tokens.append(known(t))
            output.append({"id": sentence["id"], "translation": "T.", "tokens": tokens})
        skill._chunk_path(work_dir, 1, "output").write_text(
            json.dumps(output, ensure_ascii=False), encoding="utf-8")
        validation = skill.validate(work_dir, 1)
        self.assertEqual(validation["status"], "done", validation["errors"])

        assembled = skill.assemble(work_dir)
        self.assertEqual(assembled["definitionSources"]["contextual"], 4)
        self.assertEqual(assembled["definitionSources"]["unknown"], 1)
        self.assertEqual(assembled["unknownDefinitions"], ["叽萌"])
        self.assertEqual(assembled["identifiedReferents"],
                         [{"word": "贝索斯", "def": "Jeff Bezos, founder of Amazon"}])
        self.assertEqual(
            {item["word"]: (item["def"], item["basis"]) for item in assembled["contextualGlosses"]},
            {"滤水器": ("water filter", "transparent_compound"),
             "雪莉": ("female given name", "components"),
             "斯威夫特": ("surname", "sentence_context")})

        data = json.loads(Path(assembled["json"]).read_text("utf-8"))
        self.assertEqual(validate_text_file(data), [])
        if shutil.which("node"):
            self.assertTrue(reader_validation(data)["ok"])
        words = data["words"]
        self.assertEqual(words["厨房"]["defSource"], "dictionary")
        self.assertEqual(words["叽萌"], words["叽萌"] | {"defSource": "unknown",
                                                     "def": skill.UNAVAILABLE})
        self.assertNotIn("basis", words["叽萌"])
        self.assertEqual(words["贝索斯"]["basis"], REFERENT_BASIS)
        self.assertEqual(words["贝索斯"]["def"], skill.UNAVAILABLE)
        # The entry def stays the dictionary field; the contextual gloss lives on tokens only.
        self.assertEqual(words["滤水器"]["def"], skill.UNAVAILABLE)
        self.assertEqual(words["滤水器"]["defSource"], "contextual")
        self.assertEqual(words["滤水器"]["basis"], FILTER_BASIS)
        tokens = [t for p in data["paragraphs"] for s in p["sentences"] for t in s["tokens"]]
        self.assertTrue(all("defSource" not in t and "basis" not in t for t in tokens))
        self.assertEqual({t["def"] for t in tokens if t.get("k") == "滤水器"}, {"water filter"})

        report = json.loads(Path(assembled["report"]).read_text("utf-8"))
        self.assertEqual(report["definitionSources"],
                         {"dictionary": len(words) - 5, "contextual": 4, "unknown": 1})
        glosses = {item["word"]: item for item in report["contextualGlosses"]}
        self.assertEqual(set(glosses), {"滤水器", "雪莉", "斯威夫特"})
        self.assertEqual((glosses["滤水器"]["basis"], glosses["滤水器"]["sentenceIds"]),
                         (FILTER_BASIS, ["p0s1", "p1s1"]))
        referent = report["identifiedReferents"][0]
        self.assertEqual((referent["word"], referent["basis"], referent["sentenceIds"]),
                         ("贝索斯", REFERENT_BASIS, ["p2s0", "p3s0"]))
        unknown = {item["word"]: item["sentenceIds"] for item in report["unknownDefinitions"]}
        self.assertEqual(unknown, {"叽萌": ["p1s0"]})

    def test_schema_addendum_rejects_inconsistent_provenance(self):
        base = {"simp": "滤水器", "trad": "濾水器", "py": "x", "countInText": 1, "hsk": None}
        cases = {
            "bad source": {**base, "def": skill.UNAVAILABLE, "defSource": "guess"},
            "unknown with def": {**base, "def": "water filter", "defSource": "unknown"},
            "contextual no basis": {**base, "def": skill.UNAVAILABLE, "defSource": "contextual"},
            "contextual def in entry": {**base, "def": "water filter", "defSource": "contextual",
                                        "basis": FILTER_BASIS},
            "basis on dictionary": {**base, "def": "x", "defSource": "dictionary",
                                    "basis": FILTER_BASIS},
        }
        for name, entry in cases.items():
            with self.subTest(name):
                data = {
                    "schemaVersion": 1, "textId": "t", "title": "t", "script": "simp",
                    "createdAt": "2026-01-01",
                    "stats": {"wordCount": 1, "uniqueWords": 1, "hskCoverage": {}},
                    "paragraphs": [{"sentences": [{"id": "p0s0", "translation": "", "tokens": [
                        {"t": "滤水器", "k": "滤水器", "py": "x", "def": "water filter",
                         "src": "claude"}]}]}],
                    "words": {"滤水器": entry},
                }
                self.assertTrue(validate_text_file(data))
