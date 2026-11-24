# What you write: chunk output format

The reader imports schema-v1 JSON. The scripts build the document envelope (`textId`, `stats`, the `words` map with HSK levels and counts) and also fill in `k` and `src`. You write only the per-sentence part for each chunk.

## Chunk input (read-only)

```json
{"chunk": 1, "chunkCount": 4, "script": "trad",
 "sentences": [
   {"id": "p0s0", "text": "昨天我去了銀行。",
    "tokens": [
      {"t": "昨天", "py": "zuó tiān", "candidates": [{"py": "zuó tiān", "def": "yesterday"}]},
      {"t": "我", "py": "wǒ", "candidates": [{"py": "wǒ", "def": "I; me; my"}]},
      {"t": "去", "py": "qù", "candidates": [{"py": "qù", "def": "to go; to go to (a place); ..."}]},
      {"t": "了", "py": "le", "candidates": [{"py": "le", "def": "(completed action marker); ..."},
                                           {"py": "liǎo", "def": "to finish; to understand; ..."}]},
      {"t": "銀行", "py": "yín háng", "candidates": [{"py": "yín háng", "def": "bank"}]},
      {"t": "。"}]}]}
```

- `text` is the exact normalized sentence.
- `tokens` is the local jieba segmentation. It is a starting point, not a requirement.
- `candidates` lists every CC-CEDICT reading for the word. It is `null` when CC-CEDICT has no entry.
- A token holding only `t` is punctuation, numbers, Latin text or whitespace.
- `gaps` (chunk level) lists each word with `null` candidates once: up to three sentences it appears in (`sentences`), up to three sentences in other chunks that contain it (`elsewhere`), and the CC-CEDICT entries of its sub-words and characters (`components`). See [dictionary-gaps.md](dictionary-gaps.md).

## Chunk output (you write this)

A JSON array with one object per input sentence, in the same order and with the same `id`s:

```json
[
  {"id": "p0s0", "translation": "Yesterday I went to the bank.",
   "tokens": [
     {"t": "昨天", "py": "zuótiān", "def": "yesterday"},
     {"t": "我", "py": "wǒ", "def": "I"},
     {"t": "去", "py": "qù", "def": "to go"},
     {"t": "了", "py": "le", "def": "completed-action particle"},
     {"t": "銀行", "py": "yínháng", "def": "bank"},
     {"t": "。"}]}
]
```

A simplified sentence uses the same shape, for example `{"t": "银行", "py": "yínháng", "def": "bank"}`.

A word token may also carry `defSource` (`dictionary`, `contextual`, `glossary` or `unknown`) and, for `contextual` only, `basis: {"kind", "note"}`. Words missing from CC-CEDICT must carry `defSource`. `glossary` is only for terms in a glossary given to `prepare.py --glossary`:

```json
{"t": "滤水器", "py": "lǜshuǐqì", "def": "water filter", "defSource": "contextual",
 "basis": {"kind": "transparent_compound", "note": "滤 to filter + 水 water + 器 device"}}
```

## Assembled output (schema-v1 addendum)

The scripts move these fields from the tokens to the `words` map, so tokens keep the plain v1 shape. In skill output every word entry has an optional `defSource`. Contextual entries also have `basis`, and glossary entries have `glossary: {"file", "source"}` (the glossary's file name and the row's source URL or `derived`). The entry's `def` is the dictionary field: for `contextual`, `glossary` and `unknown` entries it stays `[definition unavailable]`, and the gloss is on the tokens. The reader shows `defSource` as the definition's source label, with the basis note or glossary source on demand. A reader that doesn't know these fields ignores them. API-mode output does not include them.

## Validation (`validate.py`)

1. The ids are exactly the input ids, in order.
2. `translation` is a non-empty string.
3. Joining every `t` gives `text` exactly, character for character. A changed, dropped, added or script-converted character fails this check. (`銀行` written as `银行` in a traditional text fails.)
4. A token is either all Chinese characters, with non-empty `py` and `def`, or contains no Chinese characters at all and has only `t`. A token that mixes the two, such as `"我。"`, fails.
5. A word in CC-CEDICT is `dictionary` (or has no `defSource`), has no `basis`, and has a `def` that shares a word or four-letter stem with one of its senses. The placeholder is rejected for it.
6. With a `--glossary` file, a word that is not in CC-CEDICT and matches a `term` row, or any word that matches an `override` row, is `glossary`. (An `override` may instead stay `dictionary` where the text uses the dictionary sense.) Its `def` shares a word or four-letter stem with the row's `english`, is 12 words or fewer, and has no `basis`. `glossary` is rejected for a word with no such row, and for a `term` that is in CC-CEDICT; such a `term` follows rule 5.
7. Any other word not in CC-CEDICT has a `defSource` of `unknown` or `contextual`:
   - `unknown`: `def` is exactly `[definition unavailable]`, with no `basis`.
   - `contextual`: `def` is 12 words or fewer and not the placeholder. `basis.kind` is `components`, `transparent_compound`, `sentence_context` or `identified_referent`, and `basis.note` is non-empty, 200 characters or fewer.
   - A component-based note names at least one of the word's characters, and the word must have dictionary components.
   - A `sentence_context` or `identified_referent` note quotes two or more characters from anywhere in the text, other than the word itself.
8. The assembled chunk passes the schema-v1 validator.
