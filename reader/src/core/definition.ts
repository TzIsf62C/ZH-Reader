import type { DefProvenance, DefSource, Token, WordEntry } from './types.ts'

/** The placeholder the annotators write when a word has no definition. */
export const UNAVAILABLE = '[definition unavailable]'

export const SOURCE_LABELS: Record<DefSource, string> = {
  dictionary: 'Dictionary',
  contextual: 'Contextual',
  glossary: 'Glossary',
  unknown: 'Unknown',
}

const BASIS_LABELS: Record<string, string> = {
  components: 'From its characters',
  transparent_compound: 'From its parts (transparent compound)',
  sentence_context: 'From the surrounding text',
  identified_referent: 'Identified from the text',
}

export type SourceDetail =
  | { kind: 'basis'; label: string; note: string }
  | { kind: 'glossary'; file: string; source: string; url: string | null }

export interface DefinitionView {
  /** The definition source, or null for files without provenance (API mode, older files). */
  source: DefSource | null
  /** Badge text: the source label, or Claude/Local for files without provenance. */
  label: string | null
  /** The definition to show, or null when there is none. */
  def: string | null
  /** The dictionary definition, when it differs from the one shown. */
  dictionaryDef: string | null
  /** Evidence for a contextual gloss, or the source of a glossary gloss, shown on demand. */
  detail: SourceDetail | null
}

const isUsable = (def: string | undefined): def is string => !!def && def !== UNAVAILABLE

/** Only http(s) sources become links; anything else (such as "derived") is shown as text. */
export function sourceUrl(source: string): string | null {
  try {
    const url = new URL(source)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}

export function sourceDetail(p: DefProvenance): SourceDetail | null {
  if (p.defSource === 'contextual' && p.basis) {
    return { kind: 'basis', label: BASIS_LABELS[p.basis.kind] ?? p.basis.kind, note: p.basis.note }
  }
  if (p.defSource === 'glossary' && p.glossary) {
    return { kind: 'glossary', ...p.glossary, url: sourceUrl(p.glossary.source) }
  }
  return null
}

/**
 * How to display the definition of one tapped token.
 *
 * The token holds the gloss written for this occurrence; the word entry holds the dictionary
 * definition (the placeholder for contextual, glossary and unknown words) and, in annotated
 * files, the definition source.
 */
export function describeDefinition(token: Token, entry: WordEntry): DefinitionView {
  const shown = token.def ?? entry.def
  const def = isUsable(shown) ? shown : null
  const dictionaryDef =
    isUsable(entry.def) && token.def && token.def !== entry.def ? entry.def : null

  if (!entry.defSource) {
    const label = token.src === 'claude' ? 'Claude' : token.src === 'local' ? 'Local' : null
    return { source: null, label, def, dictionaryDef, detail: null }
  }
  // A contextual or glossary word whose occurrence has no gloss (a chunk that fell back to
  // local annotation) is unknown here, not labeled with a source it doesn't show.
  const source: DefSource = def === null ? 'unknown' : entry.defSource
  return {
    source,
    label: SOURCE_LABELS[source],
    def: source === 'unknown' ? null : def,
    dictionaryDef: source === 'dictionary' ? dictionaryDef : null,
    detail: source === entry.defSource ? sourceDetail(entry) : null,
  }
}

/** The provenance to save with a vocab entry, matching what the panel showed. */
export function vocabProvenance(view: DefinitionView, entry: WordEntry): DefProvenance {
  if (!view.source) return {}
  const out: DefProvenance = { defSource: view.source }
  if (view.source === 'contextual' && entry.basis) out.basis = { ...entry.basis }
  if (view.source === 'glossary' && entry.glossary) out.glossary = { ...entry.glossary }
  return out
}
