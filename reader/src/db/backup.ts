import { validateTextFile } from '../core/schema.ts'
import { db, defaultSettings, type ReaderDB } from './db.ts'

export const BACKUP_FORMAT = 'zh-reader-backup'
export const BACKUP_VERSION = 1

export interface Backup {
  format: typeof BACKUP_FORMAT
  backupVersion: number
  exportedAt: number
  tables: Record<string, unknown[]>
}

/** Builds a full backup; the settings snapshot carries the export time. */
export async function buildBackup(now: number = Date.now(), database: ReaderDB = db): Promise<Backup> {
  const [texts, textData, wordStats, vocab, readingSessions, settings] = await Promise.all([
    database.texts.toArray(),
    database.textData.toArray(),
    database.wordStats.toArray(),
    database.vocab.toArray(),
    database.readingSessions.toArray(),
    database.settings.toArray(),
  ])
  const main = settings.find((s) => s.id === 'main') ?? defaultSettings()
  return {
    format: BACKUP_FORMAT,
    backupVersion: BACKUP_VERSION,
    exportedAt: now,
    tables: {
      texts,
      textData,
      wordStats,
      vocab,
      readingSessions,
      settings: [{ ...main, lastExportAt: now }],
    },
  }
}

export async function markExported(now: number, database: ReaderDB = db): Promise<void> {
  const current = (await database.settings.get('main')) ?? defaultSettings()
  await database.settings.put({ ...current, lastExportAt: now })
}

export type BackupCheck = { ok: true; backup: Backup } | { ok: false; error: string }

export function checkBackup(raw: unknown): BackupCheck {
  const b = raw as Partial<Backup> | null
  if (!b || typeof b !== 'object' || b.format !== BACKUP_FORMAT) {
    return { ok: false, error: 'This file is not a ZH Reader backup.' }
  }
  if (b.backupVersion !== BACKUP_VERSION) {
    return { ok: false, error: `Unsupported backup version (${String(b.backupVersion)}).` }
  }
  const t = b.tables
  const required = ['texts', 'textData', 'wordStats', 'vocab', 'readingSessions', 'settings']
  if (!t || required.some((name) => !Array.isArray(t[name]))) {
    return { ok: false, error: 'The backup is missing data tables.' }
  }
  for (const row of t.textData as { data?: unknown }[]) {
    const res = validateTextFile(row?.data)
    if (!res.ok) return { ok: false, error: `The backup contains an invalid text: ${res.error}` }
  }
  return { ok: true, backup: b as Backup }
}

/** Replaces every table with the backup contents in a single transaction. */
export async function restoreBackup(backup: Backup, database: ReaderDB = db): Promise<void> {
  const t = backup.tables
  await database.transaction(
    'rw',
    [
      database.texts,
      database.textData,
      database.wordStats,
      database.vocab,
      database.readingSessions,
      database.settings,
    ],
    async () => {
      await Promise.all([
        database.texts.clear(),
        database.textData.clear(),
        database.wordStats.clear(),
        database.vocab.clear(),
        database.readingSessions.clear(),
        database.settings.clear(),
      ])
      await database.texts.bulkPut(t.texts as never[])
      await database.textData.bulkPut(t.textData as never[])
      await database.wordStats.bulkPut(t.wordStats as never[])
      await database.vocab.bulkPut(t.vocab as never[])
      await database.readingSessions.bulkPut(t.readingSessions as never[])
      const settings = (t.settings as { id?: string }[])[0] ?? {}
      await database.settings.put({ ...defaultSettings(), ...settings, id: 'main' })
    },
  )
}

export function backupReminderDue(
  lastExportAt: number | null,
  reminderDays: number,
  now: number = Date.now(),
): boolean {
  if (reminderDays <= 0) return false
  if (lastExportAt === null) return true
  return now - lastExportAt >= reminderDays * 86_400_000
}
