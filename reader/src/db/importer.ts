import { validateTextFile } from '../core/schema.ts'
import { textLength, flatten } from '../core/tokens.ts'
import type { TextFile, TextMeta } from '../core/types.ts'
import { db, type ReaderDB } from './db.ts'

export type ImportStatus = 'imported' | 'replaced' | 'skipped' | 'error'

export interface ImportOutcome {
  fileName: string
  status: ImportStatus
  title?: string
  message?: string
}

export type DuplicateDecision = 'replace' | 'skip'

export function buildMeta(data: TextFile, previous?: TextMeta): TextMeta {
  return {
    textId: data.textId,
    title: data.title,
    script: data.script,
    importedAt: previous?.importedAt ?? Date.now(),
    length: textLength(data),
    wordCount: data.stats?.wordCount ?? 0,
    hskCoverage: data.stats?.hskCoverage ?? {},
    position: previous?.position ?? 0,
    tokenCount: flatten(data).tokens.length,
    finishedCount: previous?.finishedCount ?? 0,
  }
}

/** Stores a validated text. Replacing keeps reading position and finish history. */
export async function storeText(
  data: TextFile,
  database: ReaderDB = db,
): Promise<'imported' | 'replaced'> {
  return database.transaction('rw', database.texts, database.textData, async () => {
    const previous = await database.texts.get(data.textId)
    await database.texts.put(buildMeta(data, previous))
    await database.textData.put({ textId: data.textId, data })
    return previous ? 'replaced' : 'imported'
  })
}

export async function importTexts(
  files: { name: string; text: string }[],
  decide: (existing: TextMeta, incoming: TextFile) => Promise<DuplicateDecision>,
  database: ReaderDB = db,
): Promise<ImportOutcome[]> {
  const outcomes: ImportOutcome[] = []
  for (const file of files) {
    let raw: unknown
    try {
      raw = JSON.parse(file.text)
    } catch {
      outcomes.push({ fileName: file.name, status: 'error', message: 'Not valid JSON.' })
      continue
    }
    const result = validateTextFile(raw)
    if (!result.ok) {
      outcomes.push({ fileName: file.name, status: 'error', message: result.error })
      continue
    }
    const data = result.data
    const existing = await database.texts.get(data.textId)
    if (existing && (await decide(existing, data)) === 'skip') {
      outcomes.push({ fileName: file.name, status: 'skipped', title: data.title })
      continue
    }
    const status = await storeText(data, database)
    outcomes.push({ fileName: file.name, status, title: data.title })
  }
  return outcomes
}

export async function deleteText(textId: string, database: ReaderDB = db): Promise<void> {
  await database.transaction('rw', database.texts, database.textData, async () => {
    await database.texts.delete(textId)
    await database.textData.delete(textId)
  })
}
