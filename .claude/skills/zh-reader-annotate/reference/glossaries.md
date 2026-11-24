# Domain glossaries

A domain glossary maps the fixed names of one domain (a game, a series, a field) to their official English forms, with a source for every row. It is the default whenever the user asks for a glossary to be created, or a glossary already exists for the text's domain. The full design is section 6 of `docs/PRD-annotation-skill.md` in the ZH Reader repository.

Glossaries live in `~/Documents/zh-reader-glossaries/`, one file per domain: `<domain>.tsv` (for example `totk.tsv`), plus `<domain>.draft.tsv` while rows are under review.

## File format

UTF-8 TSV. Lines starting with `#` are comments. Columns: `term`, `kind`, `pinyin`, `english`, `note`, `source`.

| `kind` | Meaning | Tokens | Translation |
|---|---|---|---|
| `term` | A bare name that is not in CC-CEDICT (哈特諾, 沛恩) | One token, glossed from the row | Use `english` |
| `name` | An official English name built from dictionary words or terms (哈特諾村, 怪物精華, quest titles) | Segment its words normally; never merge it into one token | Use `english` |
| `override` | A name whose string is also an unrelated CC-CEDICT entry (畢茲 is "QNB", 雙子 is "Gemini") | One token, glossed from the row instead of CC-CEDICT | Use `english` |

- `term` rows hold **bare names only**: 哈特諾 is a `term`, 哈特諾村 is a `name`. A suffix such as 村, 遺跡, 峽谷 or 隊 is a dictionary word and stays its own token.
- `source` is a URL, or `derived` when the row is a substring of a sourced row; the `note` then names that row and its source.
- A name whose English is just the translation of its characters needs no row (塔邦撻大橋驛站 is "Tabantha Bridge Stable": 大 + 橋 are glossed as dictionary words). Add a row only when the English is not evident from the Chinese (雙子 → Dueling Peaks).

## Workflow

### 1. Find or start the glossary
In step 1 of the main workflow, ask which domain the text belongs to if it is not obvious, and look for `~/Documents/zh-reader-glossaries/<domain>.tsv`. If it exists, use it. If it does not and the user asked for a glossary, start one. Otherwise annotate without a glossary.

### 2. Harvest the gaps
Run `prepare.py`, then read the `gaps` lists in the chunk inputs (each entry has the word and its sentences). Drop words the glossary already covers. jieba splits unknown names into fragments (哈特 + 諾村), so read the gap sentences and the text to recover the real names. Propose candidate rows with a `kind` and pinyin; never fill `english` from your own recognition.

### 3. Look up official names
Fill `english` and `source` only from a source that shows the exact Chinese string (in the text's script) next to the English. Prefer bulk sources that list many names at once; use term-by-term searches only for leftovers. If you can start a subagent, give it the candidate list and the output path, so the fetched pages stay out of the main context. Leave a row's `english` blank when no source shows it; a belief without a source goes in the `note`, marked unsourced.

For Tears of the Kingdom, these sources worked:
- Zelda Wiki `api.php?action=cargoquery&tables=Nomenclature&fields=game,subject,subjectType,lang,translation,note&where=game='TotK' AND lang='zhT'&limit=500&offset=N&format=json`
- zelda.kiranico.com `/zh-Hant/totk/data/<category>` paired with `/totk/data/<category>` by shared ID; `/totk/map` pages paired by coordinates for place names

Write the proposed rows to `<domain>.draft.tsv`, never directly to `<domain>.tsv`.

### 4. Review with the user
Show a short summary and ask about the uncertain groups separately, for example:
- rows sourced to a different work in the same series (same Chinese string)
- rows derived from a longer sourced name
- rows with no source

Write only the accepted rows to `<domain>.tsv` (append them if it exists), keeping the header comments. Never change `<domain>.tsv` without the user's approval.

### 5. Annotate with the glossary
Until `prepare.py` supports `--glossary`, apply the glossary by hand while writing each chunk:
- **Segmentation**: in each token or run of tokens, find the **longest** `term` or `override` that matches, make it one token, and split what remains into CC-CEDICT words (依蓋隊基地 → 依蓋 + 隊 + 基地). If a remainder is not in CC-CEDICT, it follows the dictionary-gap rules.
- **`term` and `override` tokens**: `"defSource": "contextual"` with `"basis": {"kind": "sentence_context", "note": "<domain> glossary: <english>, source <source>; in text: 「<quote>」"}`. The quote must include at least two characters from the sentence other than the word. A `term` that is in CC-CEDICT is glossed from the dictionary instead; an `override` always uses the glossary.
- **`name` rows**: use `english` in the sentence translation; the tokens stay ordinary dictionary words.
- A name in the text with no row (a bare name that appears only inside a `name` row) can be glossed `contextual` from that row, citing it.

### 6. Report
Add to the final report: the glossary path, how many rows were added, and any gaps that are still `unknown`, so the user can decide whether to add them.
