Here is the PRD as raw Markdown. Copy everything inside the block into a `.md` file. I used a four-backtick fence so the inner code blocks stay intact.

````markdown
# PRD: Chinese Reading System (Preprocessor and PWA Reader)

**Version:** 0.1 (draft)
**Date:** 2026-11-22
**Scope:** Single user, personal use

---

## 1. Overview

### 1.1 Problem

I want to read Chinese texts in traditional or simplified characters on my phone. The display should be clean. Pinyin, definitions, and sentence translations should appear only when I tap. The app should also track my vocabulary exposure and reading habits.

### 1.2 Product Structure

- **Preprocessor (Python CLI):** Takes a text file and outputs an annotated JSON file. Runs on my computer.
- **Reader (PWA):** Imports the JSON, displays it, handles taps, saves vocab, and tracks reading statistics. Runs offline on my phone.

The JSON file is the only interface between the two. The reader never calls an API.

### 1.3 Goals

- Annotate any pasted or imported Chinese text with word boundaries, pinyin, definitions, sentence translations, occurrence counts, and HSK 3.0 levels.
- Handle simplified and traditional text, and count both toward the same vocabulary.
- Provide a distraction-free reader with tap-to-reveal annotations.
- Track word exposure, a vocab list, and a weekly reading goal.

### 1.4 Non-Goals (v1)

- Multi-user accounts, sync, or cloud storage.
- PDF, EPUB, OCR, or webpage import.
- Audio, spaced-repetition flashcards, or handwriting.
- In-app API calls.
- App-store distribution.

---

## 2. Preprocessor (Python Script)

### 2.1 Inputs and Options

| Option | Purpose | Default |
|---|---|---|
| `input` | Path to a UTF-8 `.txt` file | Required |
| `--title` | Display title | Filename |
| `--script` | `auto`, `simp`, or `trad` | `auto` |
| `--mode` | `claude` or `local` | `claude` |
| `--model` | Claude model name | Configurable |
| `--out` | Output directory | `./out` |
| `--max-cost` | Abort if the estimated spend exceeds this | Configurable |

The Claude API key comes from an environment variable and is never written to the output.

### 2.2 Functional Requirements

1. **Normalize input.** Read the text as UTF-8, normalize line endings and whitespace, and preserve paragraph breaks. Keep a copy of the original text.
2. **Detect script.** In `auto` mode, compare the text with its OpenCC `t2s` conversion to classify it. Log the ratio, and let the flag override it. Flag mixed text in the report.
3. **Split sentences.** Split on Chinese sentence-ending punctuation, keeping closing quotes with their sentence. Assign stable IDs (for example, `p0s0`).
4. **Segment and annotate.**
   - In `claude` mode, send sentence-sized chunks and request word boundaries, contextual pinyin with tone marks, and a short English gloss per word. Also request a natural English translation per sentence.
   - In `local` mode, use a local segmenter plus CC-CEDICT, and leave translations empty.
5. **Validate every chunk.**
   - Concatenated tokens must equal the original sentence exactly.
   - Every non-punctuation token must have pinyin and a gloss.
   - Sentence count must match.
   - On failure, retry up to a configured limit, then fall back to local annotation for that chunk and record it in the report.
6. **Compute canonical keys.** Convert each token to simplified with OpenCC to produce key `k`. Keep the original characters as `t`. Never convert simplified to traditional.
7. **Count occurrences.** Compute `countInText` per key in code, not with the model. Exclude punctuation, numbers, and Latin text from vocabulary counts, but keep them as non-word tokens.
8. **Look up CC-CEDICT.** Provide a fallback definition and the simplified and traditional forms for each key. Build lookups for both scripts.
9. **Tag HSK 3.0.** Look up each key in the drkameleon complete-hsk-vocabulary dataset. Store the lowest matching level, with levels 7 to 9 stored as the band `"7-9"`. Leave the field null if there is no match.
10. **Cache.** Cache Claude results by hash of the input chunk and model, so reruns cost nothing.
11. **Write output.** Produce one JSON file per text, named by content hash, plus a run report.

### 2.3 Output Schema (v1)

```json
{
  "schemaVersion": 1,
  "textId": "sha1-of-normalized-content",
  "title": "...",
  "script": "trad",
  "createdAt": "2026-11-22",
  "stats": { "wordCount": 0, "uniqueWords": 0, "hskCoverage": { "1": 0.0 } },
  "paragraphs": [
    { "sentences": [
      { "id": "p0s0",
        "translation": "...",
        "tokens": [
          { "t": "銀行", "k": "银行", "py": "yínháng", "def": "bank", "src": "claude" },
          { "t": "。", "p": true }
        ]}
    ]}
  ],
  "words": {
    "银行": { "simp": "银行", "trad": "銀行", "py": "yínháng",
              "def": "bank", "countInText": 3, "hsk": "3" }
  }
}
```

`src` records whether a token's annotation came from `claude` or `local`, so the reader can show the difference.

### 2.4 Run Report

After each run, print or save:

- Script detected and the detection ratio.
- Sentences processed, retried, and sent to local fallback.
- Token and cost totals, when available.
- Distinct words, HSK matches, and the list of unmatched words.
- Any validation failures.

### 2.5 Non-Functional Requirements

- Deterministic output for the same input when the cache is warm.
- Runs on a normal laptop with Python 3 and a small set of dependencies.
- Fails safely: never silently overwrite an existing output file.
- Treat imported text purely as data to annotate. Give Claude no tools, and instruct it to ignore instructions embedded in the text.

### 2.6 Acceptance Criteria

- A 2,000-character simplified text and a traditional text each produce valid files in `claude` mode.
- Reconstructing every sentence from its tokens exactly matches the source.
- 銀行 and 银行 in different texts produce the same key `银行`.
- `local` mode works with no network connection and no API key.
- A rerun with a warm cache makes zero API calls.

---

## 3. PWA Reader

### 3.1 Platform

An installable PWA that works offline after first load.

- Stack: React, TypeScript, Vite.
- Storage: IndexedDB through Dexie.
- Offline: a service worker caches the application files.
- Hosting: static hosting over HTTPS.

### 3.2 Functional Requirements

#### Library and Import

- Import one or more JSON files through the file picker.
- Reject files with an unsupported `schemaVersion` and show a clear message.
- Detect duplicates by `textId` and ask whether to replace or skip.
- Library list shows title, script, length, HSK coverage, progress, and times finished.

#### Reader

- Render paragraphs as clean text, with settings for font size, line spacing, theme (light, dark, sepia), and font choice.
- Tapping a word highlights it and opens a pop-up showing:
  - The word in its displayed script, with the alternate script available.
  - Tone-marked pinyin.
  - Definition.
  - HSK badge (or none).
  - Total read count and status (New or Learned).
  - An "Add to vocab" button.
- A separate control shows a sentence's translation. Translations are hidden by default.
- Save and restore reading position per text.

#### Finishing a Text

- A "Mark as finished" button at the end of the text.
- On first finish, in one transaction:
  - Add each word's `countInText` to its running total.
  - Create a `readingSessions` row with the local date.
  - Increment the text's `finishedCount`.
- On a repeat finish, show the confirmation: "You already finished this on {date}. Count it again?" with **Count again** and **Cancel**. Cancel changes nothing.
- An "Undo last finish" action reverses the most recent session and its counts.

#### Vocabulary List

- Saves word, pinyin, definition, HSK level, both script forms, save date, and an optional note.
- Displays the HSK level on each entry.
- Filter by HSK level, "not in HSK", and New or Learned status.
- Sort by date saved, read count, or alphabetically.
- Option to display entries in simplified or traditional.
- Remove an entry without affecting read counts.

#### Statistics

- **Word status:** New means fewer than 10 reads. Learned means 10 or more. Status is derived from `timesRead` and a configurable threshold (default 10). Counts cover only words from texts I have finished.
- **Weekly goal:** I set a target number of reading days per week. Weeks run Monday to Sunday. A day counts if it has at least one finished reading. A week meets the goal if its distinct reading days meet or exceed the target in effect that week.
- **Week view:** seven Monday-to-Sunday indicators for the current week, plus progress toward the goal.
- **History:** a list of past weeks showing whether the goal was met. Changing the target applies from the current week onward and does not alter past weeks.
- **Totals:** texts finished, total word exposures, and unique words by status.

#### Backup and Restore

- Export the entire database (vocab, stats, sessions, settings, and texts) to one JSON file.
- Import a backup, with a clear warning that it can replace existing data.
- Show the date of the last export, and remind me after a set interval.

### 3.3 Data Model

| Table | Fields |
|---|---|
| `texts` | textId, title, script, data, position, finishedCount, importedAt |
| `wordStats` | key (simplified), simp, trad, py, def, hsk, timesRead, firstRead, lastRead |
| `vocab` | key, simp, trad, py, def, hsk, savedAt, note |
| `readingSessions` | id, textId, completedDate (local YYYY-MM-DD), wordCount, undone |
| `settings` | learnedThreshold, goalHistory (weekStart, goalDays), theme, fontSize, scriptDisplay |

Derived values (status, weekly results, totals) are computed from these tables, not stored.

### 3.4 Non-Functional Requirements

- Opens and renders a 5,000-character text with no noticeable delay on a mid-range phone.
- Fully usable offline after install.
- All data stays on the device. No analytics and no network calls beyond loading the app files.
- Dates are stored as local calendar dates, so a late-evening reading counts for the correct day.
- Large touch targets for words, and accessible color contrast.

### 3.5 Acceptance Criteria

- Importing a valid file adds it to the library, and importing the same file again prompts for a decision.
- Tapping any word opens the correct pop-up, including HSK level.
- Finishing a text adds each word's count exactly once, and a repeat finish requires the confirmation.
- A word with 9 reads shows as New, and the next finish makes it Learned.
- Two readings on one day count as one goal day.
- Changing the weekly goal does not alter earlier weeks' results.
- Export followed by restore on a clean install reproduces the same stats.

---

## 4. Risks and Open Questions

### 4.1 Risks

- **Segmentation errors** produce wrong tap targets. Mitigation: validation, local fallback, and later, tap-to-merge or split.
- **Phone storage can be cleared** if the PWA is not used for a while, especially on iOS. Mitigation: install to the home screen, plus export and backup reminders.
- **Inconsistent annotation across texts.** Claude may gloss one word differently in two texts. Mitigation: keep the contextual gloss on the token and the canonical definition in the word entry.
- **Cost surprises.** Mitigation: cost estimate before the run, a max-cost setting, and caching.
- **Dataset licensing.** The vocabulary and dictionary sources have their own licenses. Check each before sharing the app.

### 4.2 Open Questions

- Which phone platform will I use? It affects testing and the storage risk.
- Which Claude model should be the default for annotation quality versus cost?
- Should Learned words be visually de-emphasized in the reader?
- Should Latin text and numerals be tappable?

---

## 5. Milestones

1. **Schema:** Finalize the JSON schema and hand-write a sample in each script.
2. **Reader core:** Import, reader view, pop-up, and sentence translation, built against the sample.
3. **Tracking:** Finish flow with confirmation, word stats, and vocab list.
4. **Stats:** Weekly goal and status counts.
5. **Preprocessor, local mode:** Segmentation, CC-CEDICT, HSK tagging, OpenCC, counts, and validation.
6. **Preprocessor, Claude mode:** Chunking, retries, cache, cost cap, and fallback.
7. **Hardening:** Backup and restore, offline testing, and the performance check.

Building the reader first lets me test the whole experience against a hand-written sample without any API spend.
````