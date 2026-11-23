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

## Validation (`validate.py`)

1. The ids are exactly the input ids, in order.
2. `translation` is a non-empty string.
3. Joining every `t` gives `text` exactly, character for character. A changed, dropped, added or script-converted character fails this check. (`銀行` written as `银行` in a traditional text fails.)
4. A token is either all Chinese characters, with non-empty `py` and `def`, or contains no Chinese characters at all and has only `t`. A token that mixes the two, such as `"我。"`, fails.
5. A Chinese token whose word is not in CC-CEDICT has `def` equal to exactly `[definition unavailable]`.
6. The assembled chunk passes the schema-v1 validator.
