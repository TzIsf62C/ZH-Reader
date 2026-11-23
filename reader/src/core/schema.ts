import { SUPPORTED_SCHEMA_VERSION, type TextFile } from './types.ts'

export type ValidationResult = { ok: true; data: TextFile } | { ok: false; error: string }

const isObj = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)

export function validateTextFile(raw: unknown): ValidationResult {
  if (!isObj(raw)) return { ok: false, error: 'The file is not a text export (expected a JSON object).' }

  if (raw.schemaVersion !== SUPPORTED_SCHEMA_VERSION) {
    const found = raw.schemaVersion === undefined ? 'missing' : String(raw.schemaVersion)
    return {
      ok: false,
      error: `Unsupported schemaVersion (${found}). This reader supports version ${SUPPORTED_SCHEMA_VERSION}.`,
    }
  }
  if (typeof raw.textId !== 'string' || !raw.textId) return { ok: false, error: 'Missing textId.' }
  if (typeof raw.title !== 'string') return { ok: false, error: 'Missing title.' }
  if (raw.script !== 'simp' && raw.script !== 'trad' && raw.script !== 'mixed') {
    return { ok: false, error: 'Invalid script (expected simp, trad or mixed).' }
  }
  if (!isObj(raw.stats)) return { ok: false, error: 'Missing stats.' }
  if (!isObj(raw.words)) return { ok: false, error: 'Missing words.' }
  if (!Array.isArray(raw.paragraphs) || raw.paragraphs.length === 0) {
    return { ok: false, error: 'The text has no paragraphs.' }
  }

  const words = raw.words
  const seenIds = new Set<string>()
  for (const [pi, para] of raw.paragraphs.entries()) {
    if (!isObj(para) || !Array.isArray(para.sentences)) {
      return { ok: false, error: `Paragraph ${pi} is malformed.` }
    }
    for (const sent of para.sentences) {
      if (!isObj(sent) || typeof sent.id !== 'string' || !Array.isArray(sent.tokens)) {
        return { ok: false, error: `A sentence in paragraph ${pi} is malformed.` }
      }
      if (seenIds.has(sent.id)) return { ok: false, error: `Duplicate sentence id ${sent.id}.` }
      seenIds.add(sent.id)
      if (sent.translation !== undefined && typeof sent.translation !== 'string') {
        return { ok: false, error: `Sentence ${sent.id} has an invalid translation.` }
      }
      for (const tok of sent.tokens) {
        if (!isObj(tok) || typeof tok.t !== 'string' || tok.t === '') {
          return { ok: false, error: `Sentence ${sent.id} has a malformed token.` }
        }
        if (tok.p !== true) {
          if (typeof tok.k !== 'string' || !isObj(words[tok.k])) {
            return { ok: false, error: `Token "${tok.t}" in ${sent.id} has no entry in words.` }
          }
        }
      }
    }
  }
  for (const [key, w] of Object.entries(words)) {
    if (!isObj(w) || typeof w.simp !== 'string' || typeof w.countInText !== 'number') {
      return { ok: false, error: `Word entry "${key}" is malformed.` }
    }
  }

  const data = raw as unknown as TextFile
  for (const para of data.paragraphs) {
    for (const s of para.sentences) s.translation ??= ''
  }
  return { ok: true, data }
}

export function reconstructSentence(tokens: { t: string }[]): string {
  return tokens.map((t) => t.t).join('')
}
