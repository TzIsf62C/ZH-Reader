import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { describeDefinition, sourceUrl, UNAVAILABLE, vocabProvenance } from './definition.ts'
import { validateTextFile } from './schema.ts'
import type { TextFile, Token, WordEntry } from './types.ts'

const fixture = (): TextFile => {
  const raw = readFileSync(new URL('../../e2e/fixtures/def-sources.json', import.meta.url), 'utf8')
  const res = validateTextFile(JSON.parse(raw))
  if (!res.ok) throw new Error(res.error)
  return res.data
}

const tokenFor = (data: TextFile, k: string): Token =>
  data.paragraphs.flatMap((p) => p.sentences.flatMap((s) => s.tokens)).find((t) => t.k === k)!

const view = (k: string, data = fixture()) => describeDefinition(tokenFor(data, k), data.words[k])

describe('describeDefinition', () => {
  it('shows a dictionary gloss, with the full dictionary definition when it differs', () => {
    expect(view('我')).toEqual({
      source: 'dictionary',
      label: 'Dictionary',
      def: 'I',
      dictionaryDef: 'I; me',
      detail: null,
    })
    expect(view('去').dictionaryDef).toBeNull()
  })

  it('shows a contextual gloss from the token with its basis, never the placeholder as dictionary', () => {
    expect(view('滤水器')).toEqual({
      source: 'contextual',
      label: 'Contextual',
      def: 'water filter',
      dictionaryDef: null,
      detail: {
        kind: 'basis',
        label: 'From its parts (transparent compound)',
        note: '滤 to filter + 水 water + 器 device',
      },
    })
  })

  it('shows a glossary gloss with its file and a linkable source', () => {
    expect(view('白诘报社')).toEqual({
      source: 'glossary',
      label: 'Glossary',
      def: 'Lucky Clover Gazette (organization)',
      dictionaryDef: null,
      detail: {
        kind: 'glossary',
        file: 'totk.tsv',
        source: 'https://zelda.kiranico.com/zh-Hant/totk/map',
        url: 'https://zelda.kiranico.com/zh-Hant/totk/map',
      },
    })
  })

  it('labels an unknown word and hides the placeholder', () => {
    expect(view('叽萌')).toEqual({
      source: 'unknown',
      label: 'Unknown',
      def: null,
      dictionaryDef: null,
      detail: null,
    })
  })

  it('treats a contextual occurrence without a gloss (local fallback) as unknown', () => {
    const data = fixture()
    const token = { ...tokenFor(data, '滤水器'), def: UNAVAILABLE, src: 'local' as const }
    expect(describeDefinition(token, data.words['滤水器'])).toMatchObject({
      source: 'unknown',
      label: 'Unknown',
      def: null,
      detail: null,
    })
  })

  it('keeps the plain display for files without provenance (API mode)', () => {
    const entry: WordEntry = { simp: '银行', trad: '銀行', py: 'yínháng', def: 'bank; CL:家', countInText: 1, hsk: '3' }
    const token: Token = { t: '银行', k: '银行', py: 'yínháng', def: 'bank', src: 'claude' }
    expect(describeDefinition(token, entry)).toEqual({
      source: null,
      label: 'Claude',
      def: 'bank',
      dictionaryDef: 'bank; CL:家',
      detail: null,
    })
    const local = describeDefinition({ ...token, def: UNAVAILABLE, src: 'local' }, { ...entry, def: UNAVAILABLE })
    expect(local).toMatchObject({ label: 'Local', def: null, dictionaryDef: null })
  })

  it('falls back to the raw basis kind for kinds it does not know', () => {
    const data = fixture()
    data.words['滤水器'].basis = { kind: 'new_kind', note: 'n' }
    expect(view('滤水器', data).detail).toEqual({ kind: 'basis', label: 'new_kind', note: 'n' })
  })
})

describe('sourceUrl', () => {
  it('links only http(s) sources', () => {
    expect(sourceUrl('https://zeldawiki.wiki/wiki/Robbie')).toBe('https://zeldawiki.wiki/wiki/Robbie')
    expect(sourceUrl('derived')).toBeNull()
    expect(sourceUrl('javascript:alert(1)')).toBeNull()
  })
})

describe('vocabProvenance', () => {
  it('saves what the panel showed', () => {
    const data = fixture()
    const save = (k: string) => vocabProvenance(view(k, data), data.words[k])
    expect(save('滤水器')).toEqual({ defSource: 'contextual', basis: data.words['滤水器'].basis })
    expect(save('白诘报社')).toEqual({ defSource: 'glossary', glossary: data.words['白诘报社'].glossary })
    expect(save('我')).toEqual({ defSource: 'dictionary' })
    expect(save('叽萌')).toEqual({ defSource: 'unknown' })
  })
})
