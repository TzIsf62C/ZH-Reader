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
