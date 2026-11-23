import type { Sentence, TextFile, Token } from './types.ts'

export interface FlatSentence {
  id: string
  translation: string
  paragraph: number
  /** Global index of the first and last token of the sentence. */
  first: number
  last: number
}

export interface FlatText {
  tokens: Token[]
  /** Sentence index (into `sentences`) for every global token index. */
  tokenSentence: number[]
  sentences: FlatSentence[]
}

export function flatten(data: TextFile): FlatText {
  const tokens: Token[] = []
  const tokenSentence: number[] = []
  const sentences: FlatSentence[] = []
  data.paragraphs.forEach((para, pi) => {
    for (const s of para.sentences) {
      if (s.tokens.length === 0) continue
      const first = tokens.length
      for (const t of s.tokens) {
        tokens.push(t)
        tokenSentence.push(sentences.length)
      }
      sentences.push({ id: s.id, translation: s.translation, paragraph: pi, first, last: tokens.length - 1 })
    }
  })
  return { tokens, tokenSentence, sentences }
}

export function textLength(data: TextFile): number {
  let n = 0
  for (const p of data.paragraphs) for (const s of p.sentences) for (const t of s.tokens) n += t.t.length
  return n
}

export function sentenceText(s: Sentence): string {
  return s.tokens.map((t) => t.t).join('')
}
