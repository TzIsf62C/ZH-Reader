export const SUPPORTED_SCHEMA_VERSION = 1

export type ScriptKind = 'simp' | 'trad' | 'mixed'
export type TokenSource = 'claude' | 'local'
export type HskLevel = string | null

export interface Token {
  /** Text exactly as it appears in the source. */
  t: string
  /** Canonical simplified key (word tokens only). */
  k?: string
  py?: string
  def?: string
  src?: TokenSource
  /** True for punctuation, numbers, Latin text and whitespace (non-word tokens). */
  p?: boolean
}

export interface Sentence {
  id: string
  translation: string
  tokens: Token[]
}

export interface Paragraph {
  sentences: Sentence[]
}

/**
 * Where a word's definition comes from (schema-v1 addendum, written by the annotation skill).
 * Absent in API-mode and older files.
 */
export type DefSource = 'dictionary' | 'contextual' | 'glossary' | 'unknown'
export const DEF_SOURCES: readonly DefSource[] = ['dictionary', 'contextual', 'glossary', 'unknown']

/** The evidence for a contextual gloss. Known kinds: components, transparent_compound,
 * sentence_context, identified_referent. */
export interface DefBasis {
  kind: string
  note: string
}

/** The glossary row a glossary gloss came from: the file name and the row's URL or "derived". */
export interface GlossaryRef {
  file: string
  source: string
}

/** Optional definition provenance, shared by word entries and saved vocab. */
export interface DefProvenance {
  defSource?: DefSource
  /** Contextual entries only. */
  basis?: DefBasis
  /** Glossary entries only. */
  glossary?: GlossaryRef
}

export interface WordEntry extends DefProvenance {
  simp: string
  trad: string
  py: string
  /** The dictionary definition; "[definition unavailable]" for contextual, glossary and unknown words. */
  def: string
  countInText: number
  hsk: HskLevel
}

export interface TextStats {
  wordCount: number
  uniqueWords: number
  hskCoverage: Record<string, number>
}

export interface TextFile {
  schemaVersion: number
  textId: string
  title: string
  script: ScriptKind
  createdAt: string
  stats: TextStats
  paragraphs: Paragraph[]
  words: Record<string, WordEntry>
}

export interface TextMeta {
  textId: string
  title: string
  script: ScriptKind
  importedAt: number
  /** Character count of the text. */
  length: number
  wordCount: number
  hskCoverage: Record<string, number>
  /** Index of the first token on the page the reader left off at. */
  position: number
  tokenCount: number
  finishedCount: number
}

export interface TextData {
  textId: string
  data: TextFile
}

export interface WordStat {
  key: string
  simp: string
  trad: string
  py: string
  def: string
  hsk: HskLevel
  timesRead: number
  firstRead: string
  lastRead: string
}

export interface VocabEntry extends DefProvenance {
  key: string
  simp: string
  trad: string
  py: string
  def: string
  hsk: HskLevel
  savedAt: number
  note: string
}

export interface ReadingSession {
  id?: number
  textId: string
  /** Local calendar date, YYYY-MM-DD. */
  completedDate: string
  wordCount: number
  undone: boolean
  /** Snapshot of the counts added, so an undo is exact even if the text is gone. */
  counts: Record<string, number>
}

export interface GoalEntry {
  weekStart: string
  goalDays: number
}

export type ThemeName = 'light' | 'dark' | 'sepia'
export type FontChoice = 'sans' | 'serif' | 'kai'

export interface Settings {
  id: 'main'
  learnedThreshold: number
  goalHistory: GoalEntry[]
  theme: ThemeName
  fontSize: number
  lineSpacing: number
  fontChoice: FontChoice
  scriptDisplay: 'simp' | 'trad'
  lastExportAt: number | null
  exportReminderDays: number
}
