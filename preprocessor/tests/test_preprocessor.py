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

    def test_line_break_ends_a_sentence_without_punctuation(self):
        paragraph = normalize_text(
            "哪些情節挑戰會升級普爾亞平板？（5 個）\n"
            "#\t情節挑戰\t起點\t委託人\t獎勵\n"
            "1\t哈特諾村的研究所\t監視堡壘\t洛貝利\t神廟感應器\n"
            "3\t重大發明！傳送標記器！！\t哈特諾古代研究所\t洛貝利\t傳送標記器"
        )
        texts = [sentence.text for sentence in split_sentences(paragraph, 0)]
        self.assertEqual(texts, [
            "哪些情節挑戰會升級普爾亞平板？",
            "（5 個）",
            "\n# 情節挑戰 起點 委託人 獎勵",
            "\n1 哈特諾村的研究所 監視堡壘 洛貝利 神廟感應器",
            "\n3 重大發明！",
            "傳送標記器！！",
            " 哈特諾古代研究所 洛貝利 傳送標記器",
        ])
        self.assertEqual("".join(texts), paragraph)

    def test_line_break_after_punctuation_adds_no_empty_sentence(self):
        texts = [s.text for s in split_sentences("你好。\n「再見！」\n好", 0)]
        self.assertEqual(texts, ["你好。", "\n「再見！」", "\n好"])


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
                         {"dictionary": len(words) - 5, "contextual": 4, "glossary": 0,
                          "unknown": 1})
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


from zh_preprocessor.glossary import Glossary, GlossaryError, load_rows  # noqa: E402

GLOSSARY_TSV = (
    "term\tkind\tpinyin\tenglish\tnote\tsource\n"
    "# A test domain glossary.\n"
    "嘰萌\tterm\tjīméng\tJimeng Charm\titem\thttps://example.org/wiki/Jimeng\n"
    "水槽\toverride\tshuǐcáo\tSink Station\tplace; CC-CEDICT has 水槽 = sink\tderived\n"
    "滤水器\tname\tlǜshuǐqì\tWater Filter\tname rows never gloss tokens\thttps://example.org/f\n"
    "水槽\tterm\tshuǐcáo\tSink\tdemoted below\thttps://example.org/s\n"
)


class GlossaryTests(unittest.TestCase):
    """Domain glossaries: the `glossary` defSource (PRD section 6, G1 and G4-G6)."""

    def setUp(self):
        self.fixture = Fixture()
        self.lexicon = Lexicon(FIXTURES / "dictionary-gaps-cedict.txt", self.fixture.hsk)
        self.path = self.fixture.root / "test-domain.tsv"
        # The last row would duplicate 水槽; tests that need a valid file drop it.
        self.path.write_text(GLOSSARY_TSV.rsplit("水槽\tterm", 1)[0], encoding="utf-8")
        self.glossary = Glossary([self.path])

    def tearDown(self):
        self.fixture.close()

    def write(self, text, name="other.tsv"):
        path = self.fixture.root / name
        path.write_text(text, encoding="utf-8")
        return path

    # --- loading --------------------------------------------------------------------------

    def test_loader_indexes_term_and_override_rows_by_simplified_key(self):
        self.assertEqual(set(self.glossary.rows), {"叽萌", "水槽"})  # 嘰萌 indexed as 叽萌
        row = self.glossary.get("叽萌")
        self.assertEqual((row.kind, row.english, row.line), ("term", "Jimeng Charm", 3))
        self.assertEqual(row.marker, {"file": "test-domain.tsv",
                                      "source": "https://example.org/wiki/Jimeng"})
        self.assertIsNone(self.glossary.get("滤水器"))  # name rows are translation-only

    def test_loader_rejects_malformed_rows(self):
        cases = {
            "duplicate": GLOSSARY_TSV,
            "no source": "雪莉\tterm\tx\tShirley\tnote\t\n",
            "bad kind": "雪莉\tnickname\tx\tShirley\tnote\tderived\n",
            "columns": "雪莉\tterm\tShirley\n",
            "not chinese": "Shirley\tterm\tx\tShirley\tnote\tderived\n",
            "punctuated term": "雪莉！\tterm\tx\tShirley\tnote\tderived\n",
            "no english": "雪莉\tterm\tx\t\tnote\tderived\n",
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(GlossaryError):
                load_rows(self.write(text))
        with self.assertRaises(GlossaryError):
            Glossary([self.fixture.root / "missing.tsv"])
        # A name row is translation-only, so a full title may contain punctuation.
        self.assertEqual(len(load_rows(self.write("重大發明！\tname\tx\tBig Invention!\tq\tderived\n"))), 1)

    def test_term_in_two_glossaries_is_an_error(self):
        other = self.write("叽萌\tterm\tx\tJimeng\tnote\tderived\n")
        with self.assertRaises(GlossaryError):
            Glossary([self.path, other])

    # --- validation (G4) ------------------------------------------------------------------

    def check(self, word, fields, glossary="default"):
        tokens = [known("她"), known("发现"), {"t": word, "py": "x", **fields}]
        chunk = {"sentences": [{"id": "p0s0", "text": "她发现" + word}]}
        output = [{"id": "p0s0", "translation": "x", "tokens": tokens}]
        accepted, errors = skill.check_chunk_output(
            chunk, output, self.lexicon, None, self.glossary if glossary == "default" else glossary)
        return (accepted[0]["tokens"][2] if accepted else None), errors

    def test_glossary_term_gloss_is_accepted_and_records_its_source(self):
        token, errors = self.check("叽萌", {"def": "Jimeng Charm (item)", "defSource": "glossary"})
        self.assertEqual(errors, [])
        self.assertEqual(token["defSource"], "glossary")
        self.assertEqual(token["glossary"], self.glossary.get("叽萌").marker)

    def test_glossary_gloss_must_share_wording_and_take_no_basis(self):
        _, unrelated = self.check("叽萌", {"def": "lucky token", "defSource": "glossary"})
        self.assertIn("shares no word with its glossary english", " ".join(unrelated))
        _, placeholder = self.check("叽萌", {"def": skill.UNAVAILABLE, "defSource": "glossary"})
        self.assertIn("shares no word", " ".join(placeholder))
        _, with_basis = self.check("叽萌", {"def": "Jimeng", "defSource": "glossary",
                                            "basis": SURNAME_BASIS})
        self.assertIn('remove its "basis"', " ".join(with_basis))

    def test_glossary_term_cannot_be_tagged_contextual_or_unknown(self):
        cases = {
            "contextual": {"def": "Jimeng Charm", "defSource": "contextual",
                           "basis": {"kind": "sentence_context", "note": "glossary; 她发现"}},
            "unknown": {"def": skill.UNAVAILABLE, "defSource": "unknown"},
            "missing": {"def": "Jimeng Charm"},
        }
        for name, fields in cases.items():
            with self.subTest(name):
                _, errors = self.check("叽萌", fields)
                self.assertEqual(len(errors), 1, errors)
                self.assertIn('"defSource": "glossary"', errors[0])

    def test_glossary_source_needs_a_glossary_row(self):
        _, no_row = self.check("雪莉", {"def": "Shirley", "defSource": "glossary"})
        self.assertIn("not a term or override row", " ".join(no_row))
        _, no_glossary = self.check("叽萌", {"def": "Jimeng", "defSource": "glossary"}, None)
        self.assertIn("not a term or override row", " ".join(no_glossary))

    def test_term_in_cedict_stays_dictionary_but_override_wins(self):
        term = self.write("水槽\tterm\tx\tSink Station\tnote\tderived\n", "term.tsv")
        _, errors = self.check("水槽", {"def": "Sink Station", "defSource": "glossary"},
                               Glossary([term]))
        self.assertIn("CC-CEDICT wins", " ".join(errors))
        token, errors = self.check("水槽", {"def": "Sink Station", "defSource": "glossary"})
        self.assertEqual(errors, [])
        self.assertEqual(token["defSource"], "glossary")
        # An override may still be used in its CC-CEDICT sense, but not as contextual.
        self.assertEqual(self.check("水槽", {"def": "sink"})[1], [])
        _, contextual = self.check("水槽", {"def": "Sink Station", "defSource": "contextual",
                                            "basis": {"kind": "sentence_context",
                                                      "note": "她发现"}})
        self.assertIn('"defSource": "glossary"', " ".join(contextual))

    # --- prepare (G1), assemble and report (G5, G6) ---------------------------------------

    def prepare(self, **kwargs):
        options = {"output_dir": self.fixture.root / "out", "work_root": self.fixture.root / "work",
                   "cedict": FIXTURES / "dictionary-gaps-cedict.txt", "hsk": self.fixture.hsk}
        return skill.prepare(FIXTURES / "dictionary-gaps.txt", **(options | kwargs))

    def test_prepare_stores_glossaries_and_refuses_to_resume_with_others(self):
        work_dir = Path(self.prepare(glossaries=[self.path])["workDir"])
        manifest = json.loads((work_dir / skill.MANIFEST).read_text("utf-8"))
        self.assertEqual(manifest["glossaries"], [str(self.path.resolve())])
        self.assertTrue(self.prepare(glossaries=[self.path])["resumed"])
        with self.assertRaises(skill.SkillError):
            self.prepare()
        with self.assertRaises(skill.SkillError):
            self.prepare(glossaries=[self.write(GLOSSARY_TSV, "bad.tsv")],
                         work_root=self.fixture.root / "work2")

    def test_assemble_writes_glossary_provenance_and_report(self):
        work_dir = Path(self.prepare(glossaries=[self.path])["workDir"])
        chunk = json.loads(skill._chunk_path(work_dir, 1, "input").read_text("utf-8"))
        glossed = {"叽萌": {"def": "Jimeng Charm", "defSource": "glossary"},
                   "水槽": {"def": "Sink Station", "defSource": "glossary"}}
        output = []
        for sentence in chunk["sentences"]:
            tokens = []
            for token in sentence["tokens"]:
                t = token["t"]
                if "py" not in token:
                    tokens.append({"t": t})
                elif t == "一杯":
                    tokens += [known("一"), known("杯")]
                elif t in glossed:
                    tokens.append({"t": t, "py": "x", **glossed[t]})
                elif t in GAP_ANNOTATIONS:
                    tokens.append({"t": t, "py": "x", **DictionaryGapTests.annotated(t)})
                else:
                    tokens.append(known(t))
            output.append({"id": sentence["id"], "translation": "T.", "tokens": tokens})
        skill._chunk_path(work_dir, 1, "output").write_text(
            json.dumps(output, ensure_ascii=False), encoding="utf-8")
        validation = skill.validate(work_dir, 1)
        self.assertEqual(validation["status"], "done", validation["errors"])

        assembled = skill.assemble(work_dir)
        self.assertEqual(assembled["definitionSources"]["glossary"], 2)
        self.assertEqual({item["word"]: item["file"] for item in assembled["glossaryTerms"]},
                         {"叽萌": "test-domain.tsv", "水槽": "test-domain.tsv"})
        data = json.loads(Path(assembled["json"]).read_text("utf-8"))
        self.assertEqual(validate_text_file(data), [])
        if shutil.which("node"):
            self.assertTrue(reader_validation(data)["ok"])
        for key, source in (("叽萌", "https://example.org/wiki/Jimeng"), ("水槽", "derived")):
            entry = data["words"][key]
            self.assertEqual(entry["defSource"], "glossary")
            self.assertEqual(entry["def"], skill.UNAVAILABLE)
            self.assertEqual(entry["glossary"], {"file": "test-domain.tsv", "source": source})
            self.assertNotIn("basis", entry)
        tokens = [t for p in data["paragraphs"] for s in p["sentences"] for t in s["tokens"]]
        self.assertTrue(all("glossary" not in t and "defSource" not in t for t in tokens))
        self.assertEqual({t["def"] for t in tokens if t.get("k") == "叽萌"}, {"Jimeng Charm"})
        report = json.loads(Path(assembled["report"]).read_text("utf-8"))
        terms = {item["word"]: item for item in report["glossaryTerms"]}
        self.assertEqual(terms["叽萌"]["sentenceIds"], ["p1s0"])
        self.assertEqual(terms["叽萌"]["source"], "https://example.org/wiki/Jimeng")

    def test_schema_addendum_checks_glossary_entries(self):
        base = {"simp": "叽萌", "trad": "嘰萌", "py": "x", "countInText": 1, "hsk": None}
        marker = {"file": "test-domain.tsv", "source": "derived"}
        cases = {
            "valid": ({**base, "def": skill.UNAVAILABLE, "defSource": "glossary",
                       "glossary": marker}, False),
            "no marker": ({**base, "def": skill.UNAVAILABLE, "defSource": "glossary"}, True),
            "empty source": ({**base, "def": skill.UNAVAILABLE, "defSource": "glossary",
                              "glossary": {"file": "a.tsv", "source": " "}}, True),
            "def in entry": ({**base, "def": "Jimeng", "defSource": "glossary",
                              "glossary": marker}, True),
            "marker on contextual": ({**base, "def": skill.UNAVAILABLE, "defSource": "contextual",
                                      "basis": SURNAME_BASIS, "glossary": marker}, True),
            "marker without source": ({**base, "def": skill.UNAVAILABLE, "glossary": marker}, True),
        }
        for name, (entry, invalid) in cases.items():
            with self.subTest(name):
                data = {
                    "schemaVersion": 1, "textId": "t", "title": "t", "script": "simp",
                    "createdAt": "2026-01-01",
                    "stats": {"wordCount": 1, "uniqueWords": 1, "hskCoverage": {}},
                    "paragraphs": [{"sentences": [{"id": "p0s0", "translation": "", "tokens": [
                        {"t": "叽萌", "k": "叽萌", "py": "x", "def": "Jimeng", "src": "claude"}]}]}],
                    "words": {"叽萌": entry},
                }
                self.assertEqual(bool(validate_text_file(data)), invalid, validate_text_file(data))


TRADITIONAL_KEY_CEDICT = (
    "# OpenCC keeps 著 and 砲 as they are; CC-CEDICT files these words under 着 and 炮.\n"
    "他 他 [ta1] /he/\n"
    "笑 笑 [xiao4] /to laugh/\n"
    "着 着 [zhao1] /variant of 著|着[zhao1]/\n"
    "著 着 [zhao2] /to touch/\n"
    "著 着 [zhe5] /aspect particle indicating action in progress/\n"
    "著 着 [zhuo2] /to wear (clothes)/\n"
    "著 著 [zhu4] /to make known/to write/book/\n"
    "接著 接着 [jie1 zhe5] /to continue/then/\n"
    "著作 著作 [zhu4 zuo4] /to write/literary work/\n"
    "砲彈 炮弹 [pao4 dan4] /artillery shell/\n"
    "張 张 [Zhang1] /surname Zhang/\n"
    "張 张 [zhang1] /sheet of paper/classifier for flat objects/\n"
    "沈 沈 [Shen3] /surname Shen/\n"
    "沈 沉 [chen2] /variant of 沉[chen2]/\n"
)


class TraditionalKeyTests(unittest.TestCase):
    """Traditional words reach their CC-CEDICT entry even when OpenCC converts them differently."""

    def setUp(self):
        self.fixture = Fixture()
        cedict = self.fixture.root / "traditional-keys.txt"
        cedict.write_text(TRADITIONAL_KEY_CEDICT, encoding="utf-8")
        self.lexicon = Lexicon(cedict, self.fixture.hsk)

    def tearDown(self):
        self.fixture.close()

    def test_traditional_headword_finds_the_entry_opencc_misses(self):
        self.assertEqual(self.lexicon.key("接著"), "接着")
        self.assertEqual(self.lexicon.key("砲彈"), "炮弹")
        self.assertEqual(self.lexicon.candidates("接著"), [{"py": "jiē zhe", "def": "to continue; then"}])
        annotated = self.lexicon.annotate("接著")
        self.assertEqual((annotated["k"], annotated["py"]), ("接着", "jiē zhe"))

    def test_words_opencc_already_converts_keep_their_key(self):
        self.assertEqual(self.lexicon.key("著作"), "著作")
        self.assertEqual(self.lexicon.key("他"), "他")
        self.assertEqual(self.lexicon.key("不在字典"), "不在字典")
        self.assertIsNone(self.lexicon.candidates("不在字典"))

    def test_ambiguous_character_offers_every_reading_and_pinyin_picks_the_key(self):
        readings = [candidate["py"] for candidate in self.lexicon.candidates("著")]
        self.assertEqual(readings, ["zhù", "zháo", "zhe", "zhuó"])
        self.assertEqual(self.lexicon.key("著"), "著")  # without pinyin, OpenCC's key stays
        self.assertEqual(self.lexicon.key("著", "zhe"), "着")
        self.assertEqual(self.lexicon.key("著", "zhù"), "著")
        self.assertEqual(self.lexicon.key("著", "zhe5"), "着")  # toneless fallback
        self.assertEqual(self.lexicon.key("沈"), "沈")
        self.assertEqual(self.lexicon.key("沈", "chén"), "沉")

    def test_skill_validation_keys_traditional_words_by_their_entry(self):
        chunk = {"sentences": [{"id": "p0s0", "text": "他接著笑著。"},
                               {"id": "p0s1", "text": "著作"}]}
        output = [
            {"id": "p0s0", "translation": "Then he kept laughing.", "tokens": [
                {"t": "他", "py": "tā", "def": "he"},
                {"t": "接著", "py": "jiēzhe", "def": "then"},
                {"t": "笑", "py": "xiào", "def": "to laugh"},
                {"t": "著", "py": "zhe", "def": "aspect particle"},
                {"t": "。"}]},
            {"id": "p0s1", "translation": "Literary work", "tokens": [
                {"t": "著作", "py": "zhùzuò", "def": "literary work"}]},
        ]
        accepted, errors = skill.check_chunk_output(chunk, output, self.lexicon)
        self.assertEqual(errors, [])
        keys = [token.get("k") for sentence in accepted for token in sentence["tokens"]]
        self.assertEqual(keys, ["他", "接着", "笑", "着", None, "著作"])

    def test_skill_rejects_a_gloss_from_the_other_reading_and_lists_all_readings(self):
        chunk = {"sentences": [{"id": "p0s0", "text": "笑著"}]}
        output = [{"id": "p0s0", "translation": "laughing", "tokens": [
            {"t": "笑", "py": "xiào", "def": "to laugh"},
            {"t": "著", "py": "zhe", "def": "book"}]}]
        errors = skill.check_chunk_output(chunk, output, self.lexicon)[1]
        self.assertEqual(len(errors), 1)
        self.assertIn("matches no CC-CEDICT sense", errors[0])
        self.assertIn("[zhe] aspect particle", errors[0])
        self.assertIn("[zhù]", errors[0])

    def test_gap_evidence_shows_the_particle_reading_of_著(self):
        parts = {part["part"]: part["candidates"] for part in skill.gap_evidence("笑著他", self.lexicon)}
        self.assertIn("著", parts)
        self.assertIn("zhe", [candidate["py"] for candidate in parts["著"]])

    def test_word_entry_uses_the_texts_reading_and_skips_cross_references(self):
        default = self.lexicon.word_entry("着")
        self.assertEqual((default["py"], default["def"], default["trad"]), ("zháo", "to touch", "著"))
        particle = self.lexicon.word_entry("着", "zhe")
        self.assertEqual(particle["py"], "zhe")
        self.assertEqual(particle["def"], "aspect particle indicating action in progress")
        self.assertEqual(particle["trad"], "著")

    def test_text_words_map_takes_the_most_used_reading(self):
        def annotate(sentence):
            return "", [
                {"t": "他", "k": "他", "py": "tā", "def": "he", "src": "claude"},
                {"t": "笑", "k": "笑", "py": "xiào", "def": "to laugh", "src": "claude"},
                {"t": "著", "k": "着", "py": "zhe", "def": "aspect particle", "src": "claude"},
            ], {}

        data, _, _ = build_text_file("他笑著", "Sample", "trad", annotate, self.lexicon)
        self.assertEqual(data["words"]["着"]["py"], "zhe")
        self.assertIn("aspect particle", data["words"]["着"]["def"])
        self.assertEqual(validate_text_file(data), [])

    def test_word_entry_prefers_a_common_word_unless_the_text_reads_a_name(self):
        self.assertEqual(self.lexicon.word_entry("张")["py"], "zhāng")
        self.assertEqual(self.lexicon.word_entry("张", "Zhāng")["def"], "surname Zhang")
