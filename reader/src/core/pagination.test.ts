import { describe, expect, it } from 'vitest'
import {
  firstTokenOnPage,
  groupTokens,
  linesPerPage,
  pageCountFromScrollWidth,
  pageOfToken,
  sentenceOnPageTurn,
  tokenPagesFromLefts,
} from './pagination.ts'
import { FONT_STACKS, THEMES, contrastRatio } from './themes.ts'
import type { Token } from './types.ts'

describe('pagination helpers', () => {
  it('fits a whole number of lines per page', () => {
    expect(linesPerPage(700, 43.2)).toBe(16)
    expect(linesPerPage(700, 24 * 1.8 * 2)).toBe(8)
    expect(linesPerPage(10, 50)).toBe(1)
  })

  it('computes page count from the multi-column scroll width', () => {
    const cw = 340
    const gap = 40
    const step = cw + gap
    expect(pageCountFromScrollWidth(cw, cw, gap)).toBe(1)
    expect(pageCountFromScrollWidth(3 * cw + 2 * gap, cw, gap)).toBe(3)
    expect(step).toBe(380)
  })

  it('assigns tokens to pages by left offset', () => {
    const pages = tokenPagesFromLefts([0, 120, 300, 380, 500, 760, 1000], 380, 3)
    expect(pages).toEqual([0, 0, 0, 1, 1, 2, 2])
    expect(pageOfToken(pages, 4)).toBe(1)
    expect(firstTokenOnPage(pages, 1)).toBe(3)
    expect(firstTokenOnPage(pages, 2)).toBe(5)
  })

  it('keeps the same anchor token when the page layout changes', () => {
    const anchor = 5
    const before = tokenPagesFromLefts([0, 0, 0, 380, 380, 760, 760, 760], 380, 3) // 3 pages
    const after = tokenPagesFromLefts([0, 0, 380, 380, 760, 760, 1140, 1140], 380, 4) // larger font
    expect(pageOfToken(before, anchor)).toBe(2)
    expect(pageOfToken(after, anchor)).toBe(2)
    expect(firstTokenOnPage(after, pageOfToken(after, anchor))).toBe(4)
  })

  it('selects the first sentence on a page, or the continuing one', () => {
    // sentences: [0,0,0] [1,1,1,1] [2,2]; pages: s1 spans pages 0-1
    const tokenSentence = [0, 0, 0, 1, 1, 1, 1, 2, 2]
    const tokenPage = [0, 0, 0, 0, 0, 1, 1, 1, 1]
    expect(sentenceOnPageTurn(tokenSentence, tokenPage, 0)).toBe(0)
    expect(sentenceOnPageTurn(tokenSentence, tokenPage, 1)).toBe(1) // continuation
  })
})

describe('token grouping', () => {
  const tok = (t: string, p = false): Token => (p ? { t, p } : { t, k: t })
  it('keeps closing punctuation with the previous word and openers with the next', () => {
    const tokens = [tok('说'), tok('：', true), tok('“', true), tok('明天'), tok('。', true), tok('”', true)]
    expect(groupTokens(tokens)).toEqual([[0, 1], [2, 3, 4, 5]])
  })

  it('leaves standalone non-word tokens in their own group', () => {
    const tokens = [tok('9', true), tok('点'), tok('ATM', true)]
    expect(groupTokens(tokens)).toEqual([[0], [1], [2]])
  })
})

describe('themes', () => {
  it('uses a dark gray sentence highlight distinct from black with legible text', () => {
    const d = THEMES.dark
    expect(d.sentenceHighlight).toBe('#3a3a3c')
    expect(contrastRatio(d.sentenceHighlight, d.bg)).toBeGreaterThan(1.5)
    expect(contrastRatio(d.text, d.sentenceHighlight)).toBeGreaterThanOrEqual(7)
  })

  it.each(Object.keys(THEMES) as (keyof typeof THEMES)[])('%s theme has accessible contrast', (name) => {
    const c = THEMES[name]
    expect(contrastRatio(c.text, c.bg)).toBeGreaterThanOrEqual(7)
    expect(contrastRatio(c.text, c.sentenceHighlight)).toBeGreaterThanOrEqual(7)
    expect(contrastRatio(c.text, c.wordHold)).toBeGreaterThanOrEqual(4.5)
    expect(contrastRatio(c.muted, c.panel)).toBeGreaterThanOrEqual(4.5)
    expect(contrastRatio(c.accentText, c.accent)).toBeGreaterThanOrEqual(4.5)
    expect(contrastRatio(c.accent, c.bg)).toBeGreaterThanOrEqual(4.5)
  })

  it('has CJK font stacks', () => {
    expect(Object.keys(FONT_STACKS)).toEqual(['sans', 'serif', 'kai'])
  })
})
