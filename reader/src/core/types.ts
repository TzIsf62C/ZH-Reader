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

export interface WordEntry {
  simp: string
  trad: string
  py: string
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

export interface VocabEntry {
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
