# Filling dictionary gaps

Every word token has a definition source, `defSource`:

| `defSource` | When | `def` | `basis` |
|---|---|---|---|
| `dictionary` | The word is in CC-CEDICT (`candidates` is not `null`). Default, may be omitted. | A gloss taken from one of its senses | not allowed |
| `contextual` | Not in CC-CEDICT, and you can name the evidence for a meaning | 12 words or fewer | required |
| `glossary` | A `term` or `override` row in a glossary given to `prepare.py --glossary` (see [glossaries.md](glossaries.md)) | Keeps a word of the row's `english` | not allowed |
| `unknown` | Not in CC-CEDICT, and the evidence does not clearly support a meaning | exactly `[definition unavailable]` | not allowed |

"Unknown" is a correct answer. A confident guess is the worst outcome: when you are unsure between `contextual` and `unknown`, choose `unknown`.

## Evidence the chunk gives you

Each chunk input has a `gaps` list with one entry per word that is missing from CC-CEDICT:

```json
{"word": "贝索斯",
 "sentences": [{"id": "p3s0", "text": "后来，贝索斯搬到了西雅图。"}],
 "elsewhere": [{"id": "p2s0", "text": "亚马逊的创始人贝索斯年轻时在纽约工作。"}],
 "components": [{"part": "贝", "candidates": [{"py": "bèi", "def": "cowry; ..."}]}, ...]}
```

- `sentences`: where the word appears in this chunk.
- `elsewhere`: up to three sentences in other chunks that contain it, such as an earlier introduction.
- `components`: the CC-CEDICT entries of its sub-words (longest first) and characters.

If you merge tokens into a word that has no `gaps` entry, validation shows its component evidence in the error.

## Basis kinds

`basis` is an object: `{"kind": <kind>, "note": <the evidence, 200 characters or fewer>}`.

- `transparent_compound`: a modern compound whose meaning is the plain sum of its dictionary parts, often with a productive suffix such as 器 (device), 机 (machine), 化 (-ize), 性 (-ity), 者 (-er). The note names the parts: `滤 to filter + 水 water + 器 device`. Segmentation rules in [annotation-rules.md](annotation-rules.md) say to split such a word into its dictionary parts, so this applies only to a fixed proper term kept as one token, or to a word whose parts are not all in CC-CEDICT.
- `components`: the meaning follows from the dictionary meanings of the parts, but less literally than a transparent compound. The note names the parts and how they combine.
- `sentence_context`: the text itself establishes the meaning, for example by explaining it or by an unambiguous apposition. The note quotes the supporting words.
- `identified_referent`: the text identifies a specific real person, organization or place that is not in CC-CEDICT. The note quotes the identifying words: a role, title, work, event or an earlier full introduction. Use it only for names; see below.

Validation checks these mechanically: a component basis must name at least one of the word's characters, and the word must have dictionary components. A `sentence_context` or `identified_referent` note must quote two or more characters from anywhere in the text, other than the word itself. Validation cannot judge whether the evidence is good enough or whether the quote really identifies someone; that judgment is yours.

## Names

Never translate the characters of a name ("snow jasmine" for 雪莉), and never put a specific English form ("Shirley", "Shelly", "Sherry") in the gloss unless the text itself writes it, as in 雪莉（Shirley）. The sentence translation renders an unidentified transliterated name as pinyin with the best guess in parentheses, `Xuělì (Shirley)`, so the guess is visible as a guess. See "Translation" in [annotation-rules.md](annotation-rules.md). Use the first step that the evidence supports:

1. **In CC-CEDICT** (many public figures are, such as 特朗普, 马斯克, 姚明): `dictionary`.
2. **The text identifies the person**: `contextual` with `identified_referent`. Gloss who they are and why they are known: "Jeff Bezos, founder of Amazon". The text must pin the person down beyond the name: a role or title (美国总统), a work, era, place or event tied to them, or an earlier full introduction (see `elsewhere`). A later bare surname can carry the identification forward from that introduction. Your own recognition of the name is not evidence, and neither is a detail that merely fits a famous person: 斯威夫特 who likes singing is still just a neighbor named Swift.
3. **Only the kind of name is supported**: `contextual` with the category, not a meaning: "female given name", "surname", "place name", "company name". Cite a component entry such as 莉 "used in the transliteration of female names" (`components`), or quote the text, such as 先生 after a surname or 她 referring back to the name (`sentence_context`).
4. **Nothing supports even a category**: `unknown`.

## Otherwise `unknown` unless a basis clearly supports a gloss

- Idioms and set phrases (chengyu, xiehouyu): their meaning is rarely the sum of the characters.
- Dialect and regional words, internet slang, coinages and anything that looks invented.
- Classical or literary phrases.
- Rare or technical terms where the parts could combine in more than one way.

Component evidence alone does not make a gloss safe. 叽萌 has dictionary parts (叽 grumble, 萌 cute), but nothing shows what the word means, so it is `unknown`.

## Never

- Change or replace a dictionary word's definition. Validation rejects a dictionary gloss that shares no word (or four-letter stem) with its CC-CEDICT senses, and lists the senses to choose from.
- Mark a dictionary word `contextual` or `unknown`, or a missing word `dictionary`.
- Present a guess as a dictionary fact. Contextual glosses are kept separate: the assembled `words` entry keeps `def: "[definition unavailable]"`, `defSource: "contextual"` and the `basis`. The report lists identified referents first, then other contextual glosses, for review.

## Examples

Dictionary (`水槽` has `candidates`):
```json
{"t": "水槽", "py": "shuǐcáo", "def": "sink"}
```

Contextual, compound and name category:
```json
{"t": "滤水器", "py": "lǜshuǐqì", "def": "water filter", "defSource": "contextual",
 "basis": {"kind": "transparent_compound", "note": "滤 to filter + 水 water + 器 device"}}
{"t": "雪莉", "py": "Xuělì", "def": "female given name", "defSource": "contextual",
 "basis": {"kind": "components", "note": "莉: used in the transliteration of female names"}}
```

Contextual, identified referent (later mention; the introduction is in another chunk):
```json
{"t": "贝索斯", "py": "Bèisuǒsī", "def": "Jeff Bezos, founder of Amazon", "defSource": "contextual",
 "basis": {"kind": "identified_referent", "note": "introduced as 亚马逊的创始人 (founder of Amazon) in p2s0"}}
```

Unknown (`叽萌` is unexplained slang):
```json
{"t": "叽萌", "py": "jīméng", "def": "[definition unavailable]", "defSource": "unknown"}
```
