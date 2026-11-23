import { localDateString } from '../core/dates.ts'
import type { ReadingSession, WordStat } from '../core/types.ts'
import { db, type ReaderDB } from './db.ts'

export type FinishResult =
  | { status: 'finished'; session: ReadingSession }
  | { status: 'needs-confirmation'; lastDate: string }
  | { status: 'missing' }

export async function lastFinishDate(textId: string, database: ReaderDB = db): Promise<string | null> {
  const sessions = await database.readingSessions.where('textId').equals(textId).toArray()
  const active = sessions.filter((s) => !s.undone).sort((a, b) => (a.id ?? 0) - (b.id ?? 0))
  return active.length ? active[active.length - 1].completedDate : null
}

/**
 * Marks a text finished. The first finish counts straight away; a repeat finish
 * needs `confirmRepeat` (the UI asks "Count it again?").
 */
export async function finishText(
  textId: string,
  opts: { confirmRepeat?: boolean; today?: string } = {},
  database: ReaderDB = db,
): Promise<FinishResult> {
  const today = opts.today ?? localDateString()
  return database.transaction(
    'rw',
    [database.texts, database.textData, database.wordStats, database.readingSessions],
    async (): Promise<FinishResult> => {
      const meta = await database.texts.get(textId)
      const stored = await database.textData.get(textId)
      if (!meta || !stored) return { status: 'missing' }

      if (meta.finishedCount > 0 && !opts.confirmRepeat) {
        const last = await lastFinishDate(textId, database)
        return { status: 'needs-confirmation', lastDate: last ?? today }
      }

      const counts: Record<string, number> = {}
      for (const [key, w] of Object.entries(stored.data.words)) {
        if (w.countInText > 0) counts[key] = w.countInText
      }

      for (const [key, count] of Object.entries(counts)) {
        const w = stored.data.words[key]
        const prev = await database.wordStats.get(key)
        const next: WordStat = {
          key,
          simp: w.simp,
          trad: w.trad,
          py: w.py,
          def: w.def,
          hsk: w.hsk,
          timesRead: (prev?.timesRead ?? 0) + count,
          firstRead: prev?.firstRead ?? today,
          lastRead: today > (prev?.lastRead ?? '') ? today : prev!.lastRead,
        }
        await database.wordStats.put(next)
      }

      const session: ReadingSession = {
        textId,
        completedDate: today,
        wordCount: Object.values(counts).reduce((a, b) => a + b, 0),
        undone: false,
        counts,
      }
      session.id = await database.readingSessions.add(session)
      await database.texts.update(textId, { finishedCount: meta.finishedCount + 1 })
      return { status: 'finished', session }
    },
  )
}

export async function getLastSession(database: ReaderDB = db): Promise<ReadingSession | undefined> {
  const all = await database.readingSessions.toArray()
  return all.filter((s) => !s.undone).sort((a, b) => (b.id ?? 0) - (a.id ?? 0))[0]
}

/** Reverses the most recent session and the counts it added. */
export async function undoLastFinish(
  database: ReaderDB = db,
): Promise<{ status: 'undone'; session: ReadingSession } | { status: 'nothing' }> {
  return database.transaction(
    'rw',
    [database.texts, database.wordStats, database.readingSessions],
    async () => {
      const session = await getLastSession(database)
      if (!session || session.id === undefined) return { status: 'nothing' as const }
      await database.readingSessions.update(session.id, { undone: true })

      const remaining = (await database.readingSessions.toArray()).filter((s) => !s.undone)
      for (const [key, count] of Object.entries(session.counts)) {
        const stat = await database.wordStats.get(key)
        if (!stat) continue
        const timesRead = stat.timesRead - count
        if (timesRead <= 0) {
          await database.wordStats.delete(key)
          continue
        }
        const dates = remaining.filter((s) => s.counts[key]).map((s) => s.completedDate).sort()
        await database.wordStats.update(key, {
          timesRead,
          firstRead: dates[0] ?? stat.firstRead,
          lastRead: dates[dates.length - 1] ?? stat.lastRead,
        })
      }
      const meta = await database.texts.get(session.textId)
      if (meta) {
        await database.texts.update(session.textId, { finishedCount: Math.max(0, meta.finishedCount - 1) })
      }
      return { status: 'undone' as const, session }
    },
  )
}
