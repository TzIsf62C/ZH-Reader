import Dexie, { type EntityTable } from 'dexie'
import type {
  ReadingSession,
  Settings,
  TextData,
  TextMeta,
  VocabEntry,
  WordStat,
} from '../core/types.ts'

export type ReaderDB = Dexie & {
  texts: EntityTable<TextMeta, 'textId'>
  textData: EntityTable<TextData, 'textId'>
  wordStats: EntityTable<WordStat, 'key'>
  vocab: EntityTable<VocabEntry, 'key'>
  readingSessions: EntityTable<ReadingSession, 'id'>
  settings: EntityTable<Settings, 'id'>
}

export function createDb(name = 'zh-reader'): ReaderDB {
  const db = new Dexie(name) as ReaderDB
  db.version(1).stores({
    texts: 'textId, importedAt',
    textData: 'textId',
    wordStats: 'key, timesRead',
    vocab: 'key, savedAt',
    readingSessions: '++id, textId, completedDate',
    settings: 'id',
  })
  return db
}

export function defaultSettings(): Settings {
  return {
    id: 'main',
    learnedThreshold: 10,
    // The initial goal covers every week up to and including the current one.
    goalHistory: [{ weekStart: '1970-01-05', goalDays: 3 }],
    theme: 'light',
    fontSize: 24,
    lineSpacing: 1.8,
    fontChoice: 'sans',
    scriptDisplay: 'simp',
    lastExportAt: null,
    exportReminderDays: 14,
  }
}

export const db: ReaderDB = createDb()

export const TEXT_TABLES = ['texts', 'textData', 'wordStats', 'vocab', 'readingSessions', 'settings'] as const

/** Read-only (safe inside live queries); missing values fall back to defaults. */
export async function getSettings(database: ReaderDB = db): Promise<Settings> {
  const existing = await database.settings.get('main')
  return { ...defaultSettings(), ...existing }
}

export async function updateSettings(patch: Partial<Settings>, database: ReaderDB = db): Promise<Settings> {
  const next = { ...(await getSettings(database)), ...patch, id: 'main' as const }
  await database.settings.put(next)
  return next
}
