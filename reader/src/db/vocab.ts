import type { VocabEntry, WordEntry, HskLevel } from '../core/types.ts'
import { db, type ReaderDB } from './db.ts'

export interface VocabSource {
  key: string
  entry: WordEntry
  /** Contextual pinyin/gloss from the tapped token, preferred when present. */
  py?: string
  def?: string
}

export async function addVocab(src: VocabSource, database: ReaderDB = db): Promise<void> {
  const existing = await database.vocab.get(src.key)
  if (existing) return
  const entry: VocabEntry = {
    key: src.key,
    simp: src.entry.simp,
    trad: src.entry.trad,
    py: src.py ?? src.entry.py,
    def: src.def ?? src.entry.def,
    hsk: src.entry.hsk as HskLevel,
    savedAt: Date.now(),
    note: '',
  }
  await database.vocab.put(entry)
}

/** Removing a vocab entry never touches read counts. */
export async function removeVocab(key: string, database: ReaderDB = db): Promise<void> {
  await database.vocab.delete(key)
}

export async function setVocabNote(key: string, note: string, database: ReaderDB = db): Promise<void> {
  await database.vocab.update(key, { note })
}

export type HskFilter = 'all' | 'none' | string
export type StatusFilter = 'all' | 'new' | 'learned'
export type VocabSort = 'date' | 'reads' | 'alpha'

export function hskSortValue(h: HskLevel): number {
  if (h === null) return 99
  return h === '7-9' ? 7 : Number(h)
}

export function filterAndSortVocab(
  entries: VocabEntry[],
  reads: Map<string, number>,
  opts: {
    hsk: HskFilter
    status: StatusFilter
    sort: VocabSort
    threshold: number
    script: 'simp' | 'trad'
  },
): VocabEntry[] {
  const out = entries.filter((e) => {
    if (opts.hsk === 'none' && e.hsk !== null) return false
    if (opts.hsk !== 'all' && opts.hsk !== 'none' && e.hsk !== opts.hsk) return false
    if (opts.status !== 'all') {
      const learned = (reads.get(e.key) ?? 0) >= opts.threshold
      if ((opts.status === 'learned') !== learned) return false
    }
    return true
  })
  const word = (e: VocabEntry) => (opts.script === 'trad' ? e.trad : e.simp)
  const sorters: Record<VocabSort, (a: VocabEntry, b: VocabEntry) => number> = {
    date: (a, b) => b.savedAt - a.savedAt,
    reads: (a, b) => (reads.get(b.key) ?? 0) - (reads.get(a.key) ?? 0),
    alpha: (a, b) => a.py.localeCompare(b.py, 'en') || word(a).localeCompare(word(b), 'zh'),
  }
  return out.sort(sorters[opts.sort])
}
