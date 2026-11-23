import type { Token } from './types.ts'

const CLOSERS = /^[，。！？；：、）」』”’》】〕…—,.!?;:)\]}%]+$/u
const OPENERS = /^[（「『“‘《【〔([{]+$/u

/**
 * Groups the tokens of a sentence so that a word is never split across lines
 * and closing punctuation never starts a line (it stays with the word before
 * it; opening punctuation stays with the word after it).
 * Returns arrays of local token indices.
 */
export function groupTokens(tokens: Token[]): number[][] {
  const groups: number[][] = []
  let pendingOpeners: number[] = []
  tokens.forEach((tok, i) => {
    if (!tok.p) {
      groups.push([...pendingOpeners, i])
      pendingOpeners = []
    } else if (OPENERS.test(tok.t)) {
      pendingOpeners.push(i)
    } else if (CLOSERS.test(tok.t) && groups.length > 0 && pendingOpeners.length === 0) {
      groups[groups.length - 1].push(i)
    } else {
      if (pendingOpeners.length) {
        groups.push(pendingOpeners)
        pendingOpeners = []
      }
      groups.push([i])
    }
  })
  if (pendingOpeners.length) groups.push(pendingOpeners)
  return groups
}

export function linesPerPage(availableHeight: number, lineHeight: number): number {
  return Math.max(1, Math.floor(availableHeight / lineHeight))
}

/** Pages each step moves: content width plus the gap between columns. */
export function pageStep(contentWidth: number, gap: number): number {
  return contentWidth + gap
}

export function pageCountFromScrollWidth(scrollWidth: number, contentWidth: number, gap: number): number {
  return Math.max(1, Math.round((scrollWidth + gap) / pageStep(contentWidth, gap)))
}

/** Page index of every token, from the left offset of its first line box. */
export function tokenPagesFromLefts(lefts: number[], step: number, pageCount: number): number[] {
  return lefts.map((l) => Math.min(pageCount - 1, Math.max(0, Math.floor((l + 1) / step))))
}

export function pageOfToken(tokenPage: number[], tokenIndex: number): number {
  if (tokenPage.length === 0) return 0
  return tokenPage[Math.min(Math.max(tokenIndex, 0), tokenPage.length - 1)]
}

/** First token index on a page (the reading-position anchor). */
export function firstTokenOnPage(tokenPage: number[], page: number): number {
  let lo = 0
  let hi = tokenPage.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (tokenPage[mid] < page) lo = mid + 1
    else hi = mid
  }
  return Math.min(lo, Math.max(0, tokenPage.length - 1))
}

/**
 * Sentence selected when a page opens: the sentence of the first token on it.
 * If the page starts mid-sentence this is the continuing sentence.
 */
export function sentenceOnPageTurn(tokenSentence: number[], tokenPage: number[], page: number): number {
  if (tokenSentence.length === 0) return -1
  return tokenSentence[firstTokenOnPage(tokenPage, page)]
}
