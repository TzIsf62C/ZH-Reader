// Builds the hand-annotated sample files in ../samples. The lexicon and the
// sentence segmentation are written by hand below; counts, stats and ids are
// computed the way the preprocessor will compute them. HSK levels here are
// illustrative sample data.
import { createHash } from 'node:crypto'
import { mkdirSync, writeFileSync } from 'node:fs'

const LEX = {
  今天: ['今天', 'jīntiān', 'today', '1'],
  昨天: ['昨天', 'zuótiān', 'yesterday', '2'],
  我: ['我', 'wǒ', 'I; me', '1'],
  去: ['去', 'qù', 'to go', '1'],
  了: ['了', 'le', 'completed-action particle', '1'],
  银行: ['銀行', 'yínháng', 'bank', '3'],
  里: ['裡', 'lǐ', 'inside', '2'],
  有: ['有', 'yǒu', 'to have; there is', '1'],
  很多: ['很多', 'hěnduō', 'many; a lot', '2'],
  人: ['人', 'rén', 'person; people', '1'],
  想: ['想', 'xiǎng', 'to want; to think', '1'],
  换: ['換', 'huàn', 'to exchange', '3'],
  钱: ['錢', 'qián', 'money', '1'],
  但是: ['但是', 'dànshì', 'but', '3'],
  没有: ['沒有', 'méiyǒu', 'not have', '1'],
  带: ['帶', 'dài', 'to bring; to carry', '3'],
  护照: ['護照', 'hùzhào', 'passport', null],
  工作人员: ['工作人員', 'gōngzuò rényuán', 'staff member', '7-9'],
  说: ['說', 'shuō', 'to say; to speak', '1'],
  明天: ['明天', 'míngtiān', 'tomorrow', '1'],
  再: ['再', 'zài', 'again', '2'],
  来: ['來', 'lái', 'to come', '1'],
  吧: ['吧', 'ba', 'suggestion particle', '2'],
  有点儿: ['有點兒', 'yǒudiǎnr', 'a little; somewhat', '3'],
  失望: ['失望', 'shīwàng', 'disappointed', '4'],
  可是: ['可是', 'kěshì', 'but; however', '3'],
  还是: ['還是', 'háishi', 'still; nevertheless', '3'],
  很: ['很', 'hěn', 'very', '1'],
  客气: ['客氣', 'kèqi', 'polite', '4'],
  点: ['點', 'diǎn', "o'clock; point", '1'],
  到: ['到', 'dào', 'to arrive', '1'],
}

const TRAD_TO_KEY = new Map(Object.entries(LEX).map(([k, v]) => [v[0], k]))
const NON_WORD = /^[\p{P}\p{S}\p{N}\s]+$|^[A-Za-z]+$/u

function tokenize(spec, src) {
  return spec.split(' ').map((t) => {
    if (NON_WORD.test(t)) return { t, p: true }
    const key = LEX[t] ? t : TRAD_TO_KEY.get(t)
    if (!key) throw new Error(`Unknown token ${t}`)
    const [, py, def] = LEX[key]
    return { t, k: key, py, def, src }
  })
}

function build({ title, script, paragraphs }) {
  const words = {}
  const out = paragraphs.map((sentences, pi) => ({
    sentences: sentences.map((s, si) => ({
      id: `p${pi}s${si}`,
      translation: s.tr,
      tokens: tokenize(s.s, s.src ?? 'claude'),
    })),
  }))
  for (const para of out)
    for (const sent of para.sentences)
      for (const tok of sent.tokens) {
        if (tok.p) continue
        const [trad, py, def, hsk] = LEX[tok.k]
        words[tok.k] ??= { simp: tok.k, trad, py, def, countInText: 0, hsk }
        words[tok.k].countInText++
      }
  const wordCount = Object.values(words).reduce((n, w) => n + w.countInText, 0)
  const hskCoverage = {}
  for (const w of Object.values(words)) {
    const lvl = w.hsk ?? 'none'
    hskCoverage[lvl] = (hskCoverage[lvl] ?? 0) + w.countInText
  }
  for (const k of Object.keys(hskCoverage)) hskCoverage[k] = +(hskCoverage[k] / wordCount).toFixed(4)
  const normalized = out
    .map((p) => p.sentences.map((s) => s.tokens.map((t) => t.t).join('')).join(''))
    .join('\n\n')
  return {
    schemaVersion: 1,
    textId: createHash('sha1').update(normalized).digest('hex'),
    title,
    script,
    createdAt: '2026-11-22',
    stats: { wordCount, uniqueWords: Object.keys(words).length, hskCoverage },
    paragraphs: out,
    words,
  }
}

const simp = [
  [
    { s: '今天 我 去 银行 。', tr: 'Today I went to the bank.' },
    { s: '银行 里 有 很多 人 。', tr: 'There were a lot of people in the bank.' },
  ],
  [
    { s: '我 9 点 到 了 银行 。', tr: 'I arrived at the bank at 9 o’clock.' },
    { s: '我 想 换 钱 ， 但是 我 没有 带 护照 。', tr: 'I wanted to exchange money, but I did not bring my passport.' },
  ],
  [
    { s: '工作人员 说 ： “ 明天 再 来 吧 。 ”', tr: 'The staff member said, “Come back tomorrow.”' },
    { s: '我 有点儿 失望 ， 可是 还是 很 客气 。', tr: 'I was a little disappointed, but I stayed polite.', src: 'local' },
  ],
  [{ s: '银行 里 有 ATM 。', tr: '', src: 'local' }],
]

const trad = [
  [
    { s: '昨天 我 去 了 銀行 。', tr: 'Yesterday I went to the bank.' },
    { s: '銀行 裡 有 很多 人 。', tr: 'There were a lot of people in the bank.' },
  ],
  [{ s: '我 想 換 錢 ， 但是 沒有 帶 護照 。', tr: 'I wanted to exchange money, but I did not bring my passport.' }],
  [
    { s: '工作人員 說 ： 「 明天 再 來 吧 。 」', tr: 'The staff member said, “Come back tomorrow.”' },
    { s: '我 有點兒 失望 ， 可是 還是 很 客氣 。', tr: 'I was a little disappointed, but I stayed polite.' },
  ],
]

const files = {
  'sample-simplified.json': build({ title: '去银行 (simplified sample)', script: 'simp', paragraphs: simp }),
  'sample-traditional.json': build({ title: '在銀行 (traditional sample)', script: 'trad', paragraphs: trad }),
}

// A long text (about 5,000 characters) for pagination and performance checks.
const pool = [...simp.flat(), ...trad.flat()].filter((s) => s.tr !== '')
const longParas = []
let chars = 0
let i = 0
while (chars < 5000) {
  const para = []
  for (let n = 0; n < 4; n++) {
    const s = pool[i++ % pool.length]
    para.push(s)
    chars += s.s.replaceAll(' ', '').length
  }
  longParas.push(para)
}
files['sample-long.json'] = build({
  title: '长文本 (5,000-character sample)',
  script: 'mixed',
  paragraphs: longParas,
})

mkdirSync(new URL('../../samples/', import.meta.url), { recursive: true })
for (const [name, data] of Object.entries(files)) {
  writeFileSync(new URL(`../../samples/${name}`, import.meta.url), JSON.stringify(data, null, 1) + '\n')
  console.log(name, data.stats.wordCount, 'words')
}
