import { useLiveQuery } from 'dexie-react-hooks'
import { useState } from 'react'
import { describeDefinition, vocabProvenance } from '../core/definition.ts'
import { wordStatus } from '../core/stats.ts'
import type { Token, WordEntry } from '../core/types.ts'
import { db } from '../db/db.ts'
import { addVocab } from '../db/vocab.ts'
import { hskLabel, useSettings } from './hooks.ts'
import { SourceDetail } from './SourceDetail.tsx'

export interface InfoPanelProps {
  token: Token | null
  entry: WordEntry | null
  hasSentence: boolean
  translation: string
  showTranslation: boolean
  onToggleTranslation: () => void
}

export function InfoPanel({
  token,
  entry,
  hasSentence,
  translation,
  showTranslation,
  onToggleTranslation,
}: InfoPanelProps) {
  const settings = useSettings()
  const key = token?.k
  const stat = useLiveQuery(() => (key ? db.wordStats.get(key) : undefined), [key])
  const saved = useLiveQuery(async () => (key ? !!(await db.vocab.get(key)) : false), [key])
  const [altState, setAltState] = useState<{ key?: string; on: boolean }>({ on: false })
  const alt = altState.key === key && altState.on

  let wordBlock = null
  if (token && entry && key) {
    const altForm = token.t === entry.trad ? entry.simp : entry.trad
    const canToggle = entry.simp !== entry.trad
    const shown = alt && canToggle ? altForm : token.t
    const times = stat?.timesRead ?? 0
    const status = wordStatus(times, settings.learnedThreshold)
    const view = describeDefinition(token, entry)
    wordBlock = (
      <div data-testid="word-info">
        <div className="word-line">
          <span className="word" lang="zh">
            {shown}
          </span>
          <span className="py">{token.py ?? entry.py}</span>
          <span className="badge hsk" data-testid="hsk-badge">
            {hskLabel(entry.hsk)}
          </span>
          {view.label && (
            <span className={`badge source ${view.source ?? ''}`} data-testid="def-source">
              {view.label}
            </span>
          )}
          {canToggle && (
            <button
              aria-label={`Show ${alt ? 'original' : 'alternate'} script`}
              onClick={(e) => {
                e.stopPropagation()
                setAltState({ key, on: !alt })
              }}
            >
              ⇄ {alt ? token.t : altForm}
            </button>
          )}
        </div>
        <div className="def" data-testid="word-def">
          {view.def ?? <span className="muted">No definition found</span>}
        </div>
        {view.dictionaryDef && <div className="muted">Dictionary: {view.dictionaryDef}</div>}
        <SourceDetail key={key} detail={view.detail} />
        <div className="btn-row">
          <span data-testid="word-status">
            Read {times}× · {status === 'learned' ? 'Learned' : 'New'}
          </span>
          <span className="spacer" />
          <button
            className={saved ? '' : 'primary'}
            disabled={!!saved}
            onClick={(e) => {
              e.stopPropagation()
              void addVocab({
                key,
                entry,
                py: token.py,
                def: view.def ?? undefined,
                provenance: vocabProvenance(view, entry),
              })
            }}
          >
            {saved ? 'In vocab ✓' : 'Add to vocab'}
          </button>
        </div>
      </div>
    )
  }

  return (
    <div
      className="info-panel"
      data-testid="info-panel"
      role="region"
      aria-label="Word and translation"
      onClick={() => hasSentence && onToggleTranslation()}
    >
      {wordBlock}
      {showTranslation && hasSentence && (
        <div className="translation" data-testid="translation">
          {translation || <span className="muted">No translation (local annotation).</span>}
        </div>
      )}
      {!wordBlock && !hasSentence && (
        <div className="hint">Tap a word to select its sentence. Hold a word for its details.</div>
      )}
      {hasSentence && !showTranslation && (
        <div className="hint">Tap here to show the sentence translation.</div>
      )}
    </div>
  )
}
