# ZH Reader (PWA)

Offline-first reader for annotated Chinese texts (PRD §3). It imports the JSON files produced by the
preprocessor (schema v1) and never makes network calls beyond loading its own files.

## Develop

```bash
npm install
npm run dev          # http://localhost:5173
npm test             # Vitest (core logic + Dexie via fake-indexeddb)
npm run test:e2e     # Playwright smoke + perf (iPhone WebKit, Pixel Chromium); builds and serves a preview
npm run lint
npm run build        # type-check + production build into dist/
npm run samples      # regenerate ../samples/*.json
```

Sample files live in [`../samples`](../samples): a simplified text, a traditional text (both contain
銀行/银行 → key `银行`) and a ~5,000-character text for pagination and performance checks. Their
annotations are hand-written illustrative data, not real HSK data.

## How reading works

- **Pages, not scrolling.** The text is laid out in CSS columns whose height is a whole number of lines
  (font size × line spacing). Each column is one page, so changing text size, spacing, font or rotating
  the device re-paginates automatically. Words (and the punctuation attached to them) never split.
  The reading position is a token index, so it survives re-pagination. Swipe left/right to turn pages
  (arrow keys also work). The last "page" is an end card with **Mark as finished**.
- **Tap a word** → its sentence gets a subtle highlight (dark gray in dark mode).
- **Press and hold a word** → word details appear in the reserved panel at the top, so the finger never
  covers them. They stay after release so **Add to vocab** can be tapped.
- **Tap the top panel** → show/hide the selected sentence's translation (hidden by default).
- On a page turn the first sentence on the new page is selected (the continuing sentence if the page
  starts mid-sentence).
- Latin text, numbers and punctuation are not tappable.

## Data model

Dexie tables: `texts` (metadata, position, finishedCount), `textData` (the full JSON, kept separate so
the library list stays light), `wordStats`, `vocab`, `readingSessions` (with a snapshot of the counts it
added so undo is exact) and `settings`. Status, weekly results and totals are derived, never stored.
Dates are local `YYYY-MM-DD`; weeks run Monday–Sunday.

## Backup

Settings → *Export everything* writes one JSON file with all tables (including texts). *Restore from
backup* replaces all data after an explicit warning. iOS can evict storage of unused web apps, so install
to the home screen and export regularly (a reminder appears after the configured interval).

## Hosting

Any static HTTPS host works (`dist/` after `npm run build`). The app uses a hash router and relative
URLs, so it can live under a sub-path. A restrictive Content-Security-Policy (`connect-src 'self'`) is set
in `index.html`. On iOS: open in Safari → Share → *Add to Home Screen*.

Check the licences of the vocabulary and dictionary data before sharing the app or its output.
