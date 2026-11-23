# ZH Reader Preprocessor

Python CLI that turns UTF-8 Chinese text into schema-v1 JSON accepted by the reader. It supports
offline local annotation and optional Claude translation/annotation.

## Install

Python 3.10 or later:

```bash
cd preprocessor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Install the local dictionary and HSK data once (network required for this setup command; subsequent
`--mode local` runs work offline):

```bash
zh-preprocessor setup-data
```

The datasets are kept outside the repository in `~/.cache/zh-reader-preprocessor` by default.
CC-CEDICT is distributed under CC BY-SA 4.0; its source and attribution are documented at
[MDBG's CC-CEDICT download page](https://www.mdbg.net/chinese/dictionary?page=cc-cedict). The HSK
vocabulary comes from [drkameleon/complete-hsk-vocabulary](https://github.com/drkameleon/complete-hsk-vocabulary),
licensed under MIT. Verify the terms and data sources before redistributing generated files.

## Usage

```bash
zh-preprocessor book.txt --mode local
zh-preprocessor book.txt --mode local --script trad --title "Book title" --out ./out
```

Local mode segments with jieba, looks up CC-CEDICT definitions and the lowest HSK 3.0 level, and
leaves sentence translations empty. Unknown dictionary definitions are visibly marked as
`[definition unavailable]`; the run report lists words missing from CC-CEDICT and HSK.

For Claude mode, install the optional client and export the API key:

```bash
python -m pip install -e '.[claude]'
export ANTHROPIC_API_KEY="..."
zh-preprocessor book.txt --mode claude --model claude-sonnet-4-5-20250929 --max-cost 1.00
```

The API key is read only from the environment. The model receives no tools; the system prompt treats
the source sentence as untrusted text data and directs the model not to follow embedded instructions.
Claude results are cached in a local SQLite database by sentence, model, and prompt version. A warm
cache causes no API calls. Before any Claude request, the CLI estimates total uncached run cost.
Token-cost preflight uses the configurable `--input-price-per-million` and
`--output-price-per-million` rates (defaults: $3/$15 per million); confirm rates for the model you
select. If the estimate exceeds `--max-cost`, the run stops before making any Claude requests.
This is a preflight estimate rather than a provider-side hard billing limit; actual usage can differ.
Validation failures and API errors are retried twice by default, then that sentence falls back to
local annotation.

Outputs are named from the SHA-1 of normalized text: `<hash>.json`, `<hash>.source.txt`, and
`<hash>.report.json`. The source sidecar stores normalized input but is not part of schema JSON.
Existing output files are never silently overwritten; pass `--overwrite` to explicitly replace them.
The JSON retains the reader's schema-v1 fields and counts occurrences in code.

To use alternate local data locations, pass `--data-dir`, or override one data file with `--cedict`
and/or `--hsk`. CC-CEDICT may be supplied as plain text, `.gz`, or a `.zip` containing one text file.

Run the preprocessor tests from this directory:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Skill mode (Claude Code, no API key)

The repository includes a Claude Code skill at `.claude/skills/zh-reader-annotate/`. With it, Claude writes the
translations and contextual glosses inside your Claude Code session, which counts against your plan instead
of API billing. The output is the same three files as the CLI.

Setup is the same as above: install the package dependencies (a venv or your system `python3`) and run
`zh-preprocessor setup-data` once. Then start Claude Code in the repository and ask, for example:

> Annotate ~/Downloads/story.txt for the reader, title "Story", traditional script.

The skill runs `zh-preprocessor skill prepare|next|validate|assemble` through its bundled scripts:

1. `prepare` normalizes and segments the text and looks up CC-CEDICT candidates, then writes chunks of about 20
   sentences to `~/.cache/zh-reader-preprocessor/work/<hash>/`.
2. Claude annotates one chunk at a time.
3. `validate` checks each chunk:
   - the tokens reconstruct the exact source, in its original script
   - pinyin and gloss are present
   - `[definition unavailable]` is used for non-CC-CEDICT words
   - the chunk passes schema v1

   A chunk that still fails after 2 retries keeps its local annotation.
4. `assemble` writes `<hash>.json`, `.source.txt` and `.report.json` to `~/Documents/zh-reader-out` by default.
   It never overwrites existing files unless you ask. Output and work directories inside the repository are refused.

Runs are resumable: if a session is interrupted, ask again with the same file, and completed chunks are kept.

| Area | API mode | Skill mode |
|---|---|---|
| Billing | Per token, with `--max-cost` preflight | Counts against plan limits |
| Cost control | Estimate before the first request | None; large books use a lot of allowance |
| Cache | SQLite by sentence, model, and prompt version | Work directory per text only |
| Isolation | Model has no tools | Model has tool access |
| Retries | Automatic in code | Driven by Claude following the skill |

**Prompt-injection risk:** in skill mode Claude has tool access, so instructions hidden in a source text are a
risk. The skill tells Claude to treat the text as data and to run only its bundled scripts. That reduces the risk
but does not remove it. Process only texts you trust.
