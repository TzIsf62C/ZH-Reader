import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { reconstructSentence, validateTextFile } from './schema.ts'
import type { TextFile } from './types.ts'

const load = (name: string) =>
  JSON.parse(readFileSync(new URL(`../../../samples/${name}`, import.meta.url), 'utf8'))

describe('schema validation', () => {
  it.each(['sample-simplified.json', 'sample-traditional.json', 'sample-long.json'])(
    'accepts %s',
    (name) => {
      expect(validateTextFile(load(name)).ok).toBe(true)
    },
  )

  it('rejects an unsupported schemaVersion with a clear message', () => {
    const res = validateTextFile({ ...load('sample-simplified.json'), schemaVersion: 2 })
    expect(res.ok).toBe(false)
    if (!res.ok) expect(res.error).toMatch(/Unsupported schemaVersion \(2\).*supports version 1/)
  })

  it('rejects a word token without a words entry', () => {
    const data = load('sample-simplified.json') as TextFile
    delete data.words['银行']
    expect(validateTextFile(data).ok).toBe(false)
  })

  it('keeps valid definition provenance and drops malformed provenance without failing', () => {
    const raw = JSON.parse(
      readFileSync(new URL('../../e2e/fixtures/def-sources.json', import.meta.url), 'utf8'),
    )
    const ok = validateTextFile(structuredClone(raw))
    expect(ok.ok).toBe(true)
    if (ok.ok) {
      expect(ok.data.words['白诘报社'].glossary?.file).toBe('totk.tsv')
      expect(ok.data.words['滤水器'].basis?.kind).toBe('transparent_compound')
    }
    raw.words['我'].defSource = 'guess'
    raw.words['我'].basis = { kind: 'components', note: 'x' }
    raw.words['滤水器'].basis = { kind: 'components', note: '' }
    raw.words['白诘报社'].glossary = { file: 'totk.tsv' }
    raw.words['叽萌'].glossary = { file: 'totk.tsv', source: 'derived' }
    const res = validateTextFile(raw)
    expect(res.ok).toBe(true)
    if (!res.ok) return
    expect(res.data.words['我']).not.toHaveProperty('defSource')
    expect(res.data.words['我']).not.toHaveProperty('basis')
    expect(res.data.words['滤水器']).toMatchObject({ defSource: 'contextual' })
    expect(res.data.words['滤水器']).not.toHaveProperty('basis')
    expect(res.data.words['白诘报社']).not.toHaveProperty('glossary')
    expect(res.data.words['叽萌']).not.toHaveProperty('glossary')
  })

  it('rejects non-objects and missing paragraphs', () => {
    expect(validateTextFile(null).ok).toBe(false)
    expect(validateTextFile({ ...load('sample-simplified.json'), paragraphs: [] }).ok).toBe(false)
  })

  it('maps 銀行 and 银行 to the same key and counts them together', () => {
    const simp = load('sample-simplified.json') as TextFile
    const trad = load('sample-traditional.json') as TextFile
    const keysOf = (d: TextFile, t: string) =>
      d.paragraphs.flatMap((p) => p.sentences.flatMap((s) => s.tokens)).filter((x) => x.t === t)
    expect(keysOf(simp, '银行')[0].k).toBe('银行')
    expect(keysOf(trad, '銀行')[0].k).toBe('银行')
    expect(trad.words['银行'].trad).toBe('銀行')
    expect(simp.words['银行'].countInText).toBe(4)
  })

  it('reconstructs every sentence to its source text', () => {
    const simp = load('sample-simplified.json') as TextFile
    const s = simp.paragraphs[0].sentences[0]
    expect(reconstructSentence(s.tokens)).toBe('今天我去银行。')
  })

  it('keeps empty translations and local tokens', () => {
    const simp = load('sample-simplified.json') as TextFile
    const last = simp.paragraphs.at(-1)!.sentences[0]
    expect(last.translation).toBe('')
    expect(last.tokens.find((t) => t.k)?.src).toBe('local')
  })
})
