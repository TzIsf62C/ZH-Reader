import { readFileSync } from 'node:fs'
import { beforeEach, describe, expect, it } from 'vitest'
import { validateTextFile } from '../core/schema.ts'
import type { TextFile } from '../core/types.ts'
import { backupReminderDue, buildBackup, checkBackup, restoreBackup } from './backup.ts'
import { createDb, getSettings, updateSettings, type ReaderDB } from './db.ts'
import { finishText, getLastSession, undoLastFinish } from './finish.ts'
import { importTexts, storeText } from './importer.ts'
import { addVocab, filterAndSortVocab, removeVocab } from './vocab.ts'
import { computeTotals, wordStatus } from '../core/stats.ts'

const raw = (name: string) => readFileSync(new URL(`../../../samples/${name}`, import.meta.url), 'utf8')
const sample = (name: string): TextFile => {
  const res = validateTextFile(JSON.parse(raw(name)))
  if (!res.ok) throw new Error(res.error)
  return res.data
}

let n = 0
let db: ReaderDB
beforeEach(async () => {
  db = createDb(`test-${++n}`)
  await db.open()
})

describe('import', () => {
  it('adds a valid file and prompts on duplicates', async () => {
    const file = { name: 'a.json', text: raw('sample-simplified.json') }
    const first = await importTexts([file], async () => 'skip', db)
    expect(first[0].status).toBe('imported')
    expect(await db.texts.count()).toBe(1)

    let asked = 0
    const second = await importTexts([file], async () => (asked++, 'skip'), db)
    expect(asked).toBe(1)
    expect(second[0].status).toBe('skipped')

    const third = await importTexts([file], async () => 'replace', db)
    expect(third[0].status).toBe('replaced')
    expect(await db.texts.count()).toBe(1)
  })

  it('rejects unsupported versions and bad JSON without storing anything', async () => {
    const bad = JSON.stringify({ ...JSON.parse(raw('sample-simplified.json')), schemaVersion: 9 })
    const out = await importTexts(
      [
        { name: 'v9.json', text: bad },
        { name: 'x.json', text: '{nope' },
      ],
      async () => 'skip',
      db,
    )
    expect(out.map((o) => o.status)).toEqual(['error', 'error'])
    expect(out[0].message).toMatch(/schemaVersion/)
    expect(await db.texts.count()).toBe(0)
  })

  it('replace keeps position and finish history', async () => {
    const data = sample('sample-simplified.json')
    await storeText(data, db)
    await db.texts.update(data.textId, { position: 12 })
    await finishText(data.textId, { today: '2026-11-16' }, db)
    await storeText(data, db)
    const meta = await db.texts.get(data.textId)
    expect(meta?.position).toBe(12)
    expect(meta?.finishedCount).toBe(1)
  })
})

describe('finish flow', () => {
  it('adds each word count exactly once and records a session', async () => {
    const data = sample('sample-simplified.json')
    await storeText(data, db)
    const res = await finishText(data.textId, { today: '2026-11-16' }, db)
    expect(res.status).toBe('finished')
    const bank = await db.wordStats.get('银行')
    expect(bank?.timesRead).toBe(data.words['银行'].countInText)
    expect(bank?.firstRead).toBe('2026-11-16')
    expect(await db.readingSessions.count()).toBe(1)
    expect((await db.texts.get(data.textId))?.finishedCount).toBe(1)
    expect((await db.readingSessions.toArray())[0].wordCount).toBe(data.stats.wordCount)
  })

  it('requires confirmation on a repeat finish and Cancel changes nothing', async () => {
    const data = sample('sample-simplified.json')
    await storeText(data, db)
    await finishText(data.textId, { today: '2026-11-16' }, db)
    const before = await db.wordStats.toArray()
    const res = await finishText(data.textId, { today: '2026-11-17' }, db)
    expect(res).toEqual({ status: 'needs-confirmation', lastDate: '2026-11-16' })
    expect(await db.wordStats.toArray()).toEqual(before)
    expect(await db.readingSessions.count()).toBe(1)

    const again = await finishText(data.textId, { today: '2026-11-17', confirmRepeat: true }, db)
    expect(again.status).toBe('finished')
    expect((await db.wordStats.get('银行'))?.timesRead).toBe(data.words['银行'].countInText * 2)
    expect((await db.texts.get(data.textId))?.finishedCount).toBe(2)
  })

  it('shows a word with 9 reads as New and the next finish makes it Learned', async () => {
    const data = sample('sample-simplified.json')
    await storeText(data, db)
    await db.wordStats.put({
      key: '护照',
      simp: '护照',
      trad: '護照',
      py: 'hùzhào',
      def: 'passport',
      hsk: null,
      timesRead: 9,
      firstRead: '2026-11-01',
      lastRead: '2026-11-01',
    })
    const settings = await getSettings(db)
    expect(wordStatus(9, settings.learnedThreshold)).toBe('new')
    await finishText(data.textId, { today: '2026-11-16' }, db)
    const stat = await db.wordStats.get('护照')
    expect(stat?.timesRead).toBe(10)
    expect(wordStatus(stat!.timesRead, settings.learnedThreshold)).toBe('learned')
  })

  it('undo reverses the most recent session, its counts and finishedCount', async () => {
    const a = sample('sample-simplified.json')
    const b = sample('sample-traditional.json')
    await storeText(a, db)
    await storeText(b, db)
    await finishText(a.textId, { today: '2026-11-16' }, db)
    const afterA = await db.wordStats.toArray()
    await finishText(b.textId, { today: '2026-11-18' }, db)
    expect((await db.wordStats.get('银行'))?.lastRead).toBe('2026-11-18')

    const res = await undoLastFinish(db)
    expect(res.status).toBe('undone')
    expect(await db.wordStats.toArray()).toEqual(afterA)
    expect((await db.texts.get(b.textId))?.finishedCount).toBe(0)
    expect((await getLastSession(db))?.textId).toBe(a.textId)

    // a word only in the undone text disappears; undoing everything empties stats
    await undoLastFinish(db)
    expect(await db.wordStats.count()).toBe(0)
    expect((await undoLastFinish(db)).status).toBe('nothing')
  })

  it('counts simplified and traditional readings toward the same word', async () => {
    const a = sample('sample-simplified.json')
    const b = sample('sample-traditional.json')
    await storeText(a, db)
    await storeText(b, db)
    await finishText(a.textId, { today: '2026-11-16' }, db)
    await finishText(b.textId, { today: '2026-11-17' }, db)
    expect((await db.wordStats.get('银行'))?.timesRead).toBe(
      a.words['银行'].countInText + b.words['银行'].countInText,
    )
    const totals = computeTotals(
      await db.readingSessions.toArray(),
      await db.wordStats.toArray(),
      10,
    )
    expect(totals.textsFinished).toBe(2)
  })
})

describe('vocab', () => {
  it('removing an entry never affects read counts', async () => {
    const data = sample('sample-simplified.json')
    await storeText(data, db)
    await finishText(data.textId, { today: '2026-11-16' }, db)
    await addVocab({ key: '银行', entry: data.words['银行'] }, db)
    const reads = (await db.wordStats.get('银行'))!.timesRead
    await removeVocab('银行', db)
    expect(await db.vocab.count()).toBe(0)
    expect((await db.wordStats.get('银行'))!.timesRead).toBe(reads)
  })

  it('records where the saved definition came from', async () => {
    const res = validateTextFile(JSON.parse(readFileSync(new URL('../../e2e/fixtures/def-sources.json', import.meta.url), 'utf8')))
    if (!res.ok) throw new Error(res.error)
    const words = res.data.words
    await addVocab({
      key: '白诘报社',
      entry: words['白诘报社'],
      def: 'Lucky Clover Gazette (organization)',
      provenance: { defSource: 'glossary', glossary: words['白诘报社'].glossary },
    }, db)
    expect(await db.vocab.get('白诘报社')).toMatchObject({
      def: 'Lucky Clover Gazette (organization)',
      defSource: 'glossary',
      glossary: { file: 'totk.tsv', source: 'https://zelda.kiranico.com/zh-Hant/totk/map' },
    })
    // Entries saved without provenance (older entries, API-mode texts) still work.
    await addVocab({ key: '我', entry: words['我'] }, db)
    expect(await db.vocab.get('我')).not.toHaveProperty('defSource')
  })

  it('filters by HSK level, not-in-HSK and status, and sorts', async () => {
    const data = sample('sample-simplified.json')
    for (const k of ['银行', '护照', '失望']) await addVocab({ key: k, entry: data.words[k] }, db)
    const entries = await db.vocab.toArray()
    const reads = new Map([['银行', 12], ['失望', 1]])
    const base = { threshold: 10, script: 'simp' as const, sort: 'alpha' as const }
    const keys = (o: Partial<Parameters<typeof filterAndSortVocab>[2]>) =>
      filterAndSortVocab(entries, reads, { hsk: 'all', status: 'all', ...base, ...o }).map((e) => e.key)
    expect(keys({ hsk: 'none' })).toEqual(['护照'])
    expect(keys({ hsk: '3' })).toEqual(['银行'])
    expect(keys({ status: 'learned' })).toEqual(['银行'])
    expect(keys({ status: 'new' }).sort()).toEqual(['失望', '护照'].sort())
    expect(keys({ sort: 'reads' })[0]).toBe('银行')
  })
})

describe('backup and restore', () => {
  it('export then restore on a clean install reproduces the same stats', async () => {
    const a = sample('sample-simplified.json')
    const b = sample('sample-traditional.json')
    await storeText(a, db)
    await storeText(b, db)
    await finishText(a.textId, { today: '2026-11-16' }, db)
    await finishText(b.textId, { today: '2026-11-18' }, db)
    await addVocab({ key: '银行', entry: a.words['银行'] }, db)
    await updateSettings({ learnedThreshold: 7, theme: 'dark' }, db)

    const backup = await buildBackup(1_700_000_000_000, db)
    const json = JSON.parse(JSON.stringify(backup))
    const check = checkBackup(json)
    expect(check.ok).toBe(true)

    const clean = createDb(`clean-${++n}`)
    await clean.open()
    if (check.ok) await restoreBackup(check.backup, clean)

    const snapshot = async (d: ReaderDB) => ({
      stats: await d.wordStats.orderBy('key').toArray(),
      sessions: await d.readingSessions.toArray(),
      vocab: await d.vocab.toArray(),
      texts: await d.texts.toArray(),
      data: await d.textData.toArray(),
      totals: computeTotals(await d.readingSessions.toArray(), await d.wordStats.toArray(), 7),
    })
    const restored = await snapshot(clean)
    const original = await snapshot(db)
    expect(restored).toEqual(original)
    const s = await getSettings(clean)
    expect(s.learnedThreshold).toBe(7)
    expect(s.theme).toBe('dark')
    expect(s.lastExportAt).toBe(1_700_000_000_000)
  })

  it('rejects files that are not backups', () => {
    expect(checkBackup({ foo: 1 }).ok).toBe(false)
    expect(checkBackup({ format: 'zh-reader-backup', backupVersion: 1, tables: {} }).ok).toBe(false)
  })

  it('reminds after the configured interval', () => {
    const day = 86_400_000
    expect(backupReminderDue(null, 14)).toBe(true)
    expect(backupReminderDue(1000, 14, 1000 + 13 * day)).toBe(false)
    expect(backupReminderDue(1000, 14, 1000 + 14 * day)).toBe(true)
    expect(backupReminderDue(null, 0)).toBe(false)
  })
})
