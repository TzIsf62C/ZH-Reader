import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  firstTokenOnPage,
  groupTokens,
  linesPerPage,
  pageOfToken,
  sentenceOnPageTurn,
  tokenPagesFromLefts,
} from '../core/pagination.ts'
import { FONT_STACKS } from '../core/themes.ts'
import { flatten } from '../core/tokens.ts'
import type { TextFile, Token } from '../core/types.ts'
import { db } from '../db/db.ts'
import { DisplaySettings } from './DisplaySettings.tsx'
import { EndCard } from './EndCard.tsx'
import { useSettings } from './hooks.ts'
import { InfoPanel } from './InfoPanel.tsx'
import { Modal } from './Modal.tsx'

const PAD_X = 20
const PAD_Y = 12
const HOLD_MS = 420
const MOVE_SLOP = 10

interface SentenceSpec {
  index: number
  first: number
  tokens: Token[]
  groups: number[][]
}

const SentenceView = memo(function SentenceView({
  spec,
  selected,
  heldIndex,
}: {
  spec: SentenceSpec
  selected: boolean
  heldIndex: number
}) {
  return (
    <span className={selected ? 'sent sel' : 'sent'} data-sentence={spec.index}>
      {spec.groups.map((g, gi) => (
        <span className="grp" key={gi}>
          {g.map((li) => {
            const tok = spec.tokens[li]
            const ti = spec.first + li
            return tok.p ? (
              <span key={li} className="np" data-ti={ti}>
                {tok.t}
              </span>
            ) : (
              <span key={li} className={heldIndex === ti ? 'w held' : 'w'} data-ti={ti}>
                {tok.t}
              </span>
            )
          })}
        </span>
      ))}
    </span>
  )
})

export default function Reader() {
  const { textId = '' } = useParams()
  const [boot, setBoot] = useState<{ textId: string; data: TextFile; position: number } | 'missing' | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    let alive = true
    void (async () => {
      const [meta, stored] = await Promise.all([db.texts.get(textId), db.textData.get(textId)])
      if (alive) setBoot(meta && stored ? { textId, data: stored.data, position: meta.position } : 'missing')
    })()
    return () => {
      alive = false
    }
  }, [textId])

  if (boot === 'missing') {
    return (
      <div className="screen">
        <h1>Text not found</h1>
        <button onClick={() => navigate('/')}>Back to library</button>
      </div>
    )
  }
  if (!boot || boot.textId !== textId) return null
  return <ReaderView key={textId} textId={textId} data={boot.data} initialPosition={boot.position} />
}

interface Layout {
  pages: number
  tokenPage: number[]
}

function ReaderView({ textId, data, initialPosition }: { textId: string; data: TextFile; initialPosition: number }) {
  const settings = useSettings()
  const navigate = useNavigate()
  const flat = useMemo(() => flatten(data), [data])
  const paragraphs = useMemo(() => {
    let si = 0
    let first = 0
    return data.paragraphs.map((p) =>
      p.sentences
        .filter((s) => s.tokens.length > 0)
        .map<SentenceSpec>((s) => {
          const spec = { index: si++, first, tokens: s.tokens, groups: groupTokens(s.tokens) }
          first += s.tokens.length
          return spec
        }),
    )
  }, [data])

  const areaRef = useRef<HTMLDivElement>(null)
  const colsRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState<{ w: number; h: number } | null>(null)
  const [layout, setLayout] = useState<Layout | null>(null)
  const [page, setPage] = useState(0)
  const [selected, setSelected] = useState(-1)
  const [held, setHeld] = useState<number | null>(null)
  const [showTr, setShowTr] = useState(false)
  const [dragX, setDragX] = useState(0)
  const [dragging, setDragging] = useState(false)
  const [anim, setAnim] = useState(false)
  const [fontsNonce, setFontsNonce] = useState(0)
  const [sheet, setSheet] = useState(false)

  const anchor = useRef(initialPosition)
  const firstLayout = useRef(true)
  const saveTimer = useRef<number | undefined>(undefined)
  const selectedRef = useRef(selected)
  selectedRef.current = selected

  const lineHeight = Math.round(settings.fontSize * settings.lineSpacing)
  const metrics = size && {
    cw: size.w - 2 * PAD_X,
    gap: 2 * PAD_X,
    step: size.w,
    ph: linesPerPage(size.h - 2 * PAD_Y, lineHeight) * lineHeight,
  }

  useLayoutEffect(() => {
    const el = areaRef.current
    if (!el) return
    const update = () =>
      setSize((prev) =>
        prev && prev.w === el.clientWidth && prev.h === el.clientHeight
          ? prev
          : { w: el.clientWidth, h: el.clientHeight },
      )
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    void document.fonts?.ready.then(() => setFontsNonce((n) => n + 1))
  }, [])

  // Measure which page every token landed on. Re-runs whenever anything that
  // changes line breaking changes (font size, spacing, font, viewport).
  useLayoutEffect(() => {
    const cols = colsRef.current
    if (!cols || !metrics) return
    const base = cols.getBoundingClientRect().left
    const lefts = Array.from(cols.querySelectorAll<HTMLElement>('[data-ti]'), (el) => {
      const r = el.getClientRects()[0]
      return (r ? r.left : base) - base
    })
    const tokenPage = tokenPagesFromLefts(lefts, metrics.step, Infinity)
    const pages = (tokenPage.at(-1) ?? 0) + 1
    const atEnd = anchor.current >= flat.tokens.length
    const p = atEnd ? pages : pageOfToken(tokenPage, anchor.current)
    setAnim(false)
    setLayout({ pages, tokenPage })
    setPage(p)
    if (firstLayout.current) {
      firstLayout.current = false
      setSelected(atEnd ? -1 : sentenceOnPageTurn(flat.tokenSentence, tokenPage, p))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size?.w, size?.h, settings.fontSize, settings.lineSpacing, settings.fontChoice, flat, fontsNonce])

  const scheduleSave = (pos: number) => {
    window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => {
      saveTimer.current = undefined
      void db.texts.update(textId, { position: pos })
    }, 250)
  }

  useEffect(
    () => () => {
      if (saveTimer.current !== undefined) {
        window.clearTimeout(saveTimer.current)
        void db.texts.update(textId, { position: anchor.current })
      }
    },
    [textId],
  )

  const goTo = (target: number) => {
    if (!layout) return
    const np = Math.min(Math.max(target, 0), layout.pages)
    if (np === page) return
    const onEnd = np >= layout.pages
    setAnim(true)
    setPage(np)
    setHeld(null)
    setShowTr(false)
    setSelected(onEnd ? -1 : sentenceOnPageTurn(flat.tokenSentence, layout.tokenPage, np))
    anchor.current = onEnd ? flat.tokens.length : firstTokenOnPage(layout.tokenPage, np)
    scheduleSave(anchor.current)
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (document.querySelector('[role="dialog"]')) return
      if (e.key === 'ArrowRight') goTo(page + 1)
      else if (e.key === 'ArrowLeft') goTo(page - 1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const selectSentence = (i: number) => {
    if (selectedRef.current !== i) {
      setSelected(i)
      setShowTr(false)
    }
  }

  // ---- gestures: swipe to turn, tap to select a sentence, hold for word info ----
  const g = useRef({ id: -1, x: 0, y: 0, t: 0, ti: -1, moved: false, held: false, dragging: false, timer: 0 })

  const wordAt = (target: EventTarget): number => {
    const el = (target as HTMLElement).closest?.('[data-ti]') as HTMLElement | null
    if (!el) return -1
    const ti = Number(el.dataset.ti)
    return flat.tokens[ti]?.p ? -1 : ti
  }

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement
    if (!e.currentTarget.contains(target)) return // events bubbling from portalled dialogs
    if (target.closest('button, a, input')) return
    if (e.pointerType === 'mouse' && e.button !== 0) return
    const s = g.current
    window.clearTimeout(s.timer)
    Object.assign(s, {
      id: e.pointerId,
      x: e.clientX,
      y: e.clientY,
      t: Date.now(),
      ti: wordAt(target),
      moved: false,
      held: false,
      dragging: false,
    })
    e.currentTarget.setPointerCapture(e.pointerId)
    if (s.ti >= 0) {
      s.timer = window.setTimeout(() => {
        s.held = true
        navigator.vibrate?.(8)
        setHeld(s.ti)
        selectSentence(flat.tokenSentence[s.ti])
      }, HOLD_MS)
    }
  }

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const s = g.current
    if (e.pointerId !== s.id) return
    let dx = e.clientX - s.x
    const dy = e.clientY - s.y
    if (!s.moved && Math.hypot(dx, dy) > MOVE_SLOP) {
      s.moved = true
      window.clearTimeout(s.timer)
      if (!s.held && Math.abs(dx) > Math.abs(dy)) {
        s.dragging = true
        setDragging(true)
      }
    }
    if (s.dragging && layout) {
      if ((page === 0 && dx > 0) || (page === layout.pages && dx < 0)) dx *= 0.25
      setDragX(dx)
    }
  }

  const finishPointer = (e: React.PointerEvent<HTMLDivElement>, cancelled: boolean) => {
    const s = g.current
    if (e.pointerId !== s.id) return
    window.clearTimeout(s.timer)
    s.id = -1
    if (s.dragging) {
      const dx = e.clientX - s.x
      const speed = Math.abs(dx) / Math.max(1, Date.now() - s.t)
      s.dragging = false
      setDragging(false)
      setAnim(true)
      setDragX(0)
      if (!cancelled && size && (Math.abs(dx) > size.w * 0.2 || (Math.abs(dx) > 40 && speed > 0.35))) {
        goTo(page + (dx < 0 ? 1 : -1))
      }
    } else if (!cancelled && !s.moved && !s.held && s.ti >= 0) {
      setHeld(null)
      selectSentence(flat.tokenSentence[s.ti])
    }
  }

  const heldToken = held !== null ? flat.tokens[held] : null
  const heldEntry = heldToken?.k ? (data.words[heldToken.k] ?? null) : null
  const selectedSentence = selected >= 0 ? flat.sentences[selected] : null
  const heldSentence = held !== null ? flat.tokenSentence[held] : -1
  const onEnd = layout !== null && page >= layout.pages

  return (
    <div className="reader" data-testid="reader">
      <InfoPanel
        token={heldToken}
        entry={heldEntry}
        hasSentence={!!selectedSentence}
        translation={selectedSentence?.translation ?? ''}
        showTranslation={showTr}
        onToggleTranslation={() => setShowTr((v) => !v)}
      />
      <div
        className="page-area"
        ref={areaRef}
        data-testid="page-area"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={(e) => finishPointer(e, false)}
        onPointerCancel={(e) => finishPointer(e, true)}
        onContextMenu={(e) => e.preventDefault()}
      >
        {size && metrics && (
          <div
            className={anim && !dragging ? 'strip anim' : 'strip'}
            data-testid="strip"
            style={{ transform: `translateX(${-page * size.w + dragX}px)` }}
          >
            <div
              className="columns"
              ref={colsRef}
              lang={data.script === 'trad' ? 'zh-Hant' : 'zh-Hans'}
              style={{
                left: PAD_X,
                top: PAD_Y,
                width: metrics.cw,
                height: metrics.ph,
                columnWidth: metrics.cw,
                columnGap: metrics.gap,
                fontSize: settings.fontSize,
                lineHeight: `${lineHeight}px`,
                fontFamily: FONT_STACKS[settings.fontChoice],
              }}
            >
              {paragraphs.map((sentences, pi) => (
                <p className="para" key={pi}>
                  {sentences.map((spec) => (
                    <SentenceView
                      key={spec.index}
                      spec={spec}
                      selected={spec.index === selected}
                      heldIndex={spec.index === heldSentence ? (held ?? -1) : -1}
                    />
                  ))}
                </p>
              ))}
            </div>
            {layout && (
              <EndCard
                textId={textId}
                title={data.title}
                left={layout.pages * size.w}
                width={size.w}
                active={onEnd}
              />
            )}
          </div>
        )}
      </div>
      <div className="reader-footer">
        <button onClick={() => navigate('/')} aria-label="Back to library">
          ‹ Library
        </button>
        <div className="count" data-testid="page-count" aria-live="polite">
          {layout ? (onEnd ? 'End' : `${page + 1} of ${layout.pages}`) : ''}
        </div>
        <button onClick={() => setSheet(true)} aria-label="Display settings">
          Aa
        </button>
      </div>
      {sheet && (
        <Modal
          title="Display"
          onClose={() => setSheet(false)}
          actions={
            <button className="primary" onClick={() => setSheet(false)}>
              Done
            </button>
          }
        >
          <DisplaySettings />
        </Modal>
      )}
    </div>
  )
}
