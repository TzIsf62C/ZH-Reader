import { useLiveQuery } from 'dexie-react-hooks'
import { useMemo, useState } from 'react'
import { wordStatus } from '../core/stats.ts'
import type { VocabEntry } from '../core/types.ts'
import { db, updateSettings } from '../db/db.ts'
import {
  filterAndSortVocab,
  removeVocab,
  setVocabNote,
  type HskFilter,
  type StatusFilter,
  type VocabSort,
} from '../db/vocab.ts'
import { hskLabel, useSettings } from './hooks.ts'
import { Modal } from './Modal.tsx'

const HSK_OPTIONS: [HskFilter, string][] = [
  ['all', 'All levels'],
  ['1', 'HSK 1'],
  ['2', 'HSK 2'],
  ['3', 'HSK 3'],
  ['4', 'HSK 4'],
  ['5', 'HSK 5'],
  ['6', 'HSK 6'],
  ['7-9', 'HSK 7–9'],
  ['none', 'Not in HSK'],
]

export default function Vocab() {
  const settings = useSettings()
  const entries = useLiveQuery(() => db.vocab.toArray(), [])
  const stats = useLiveQuery(() => db.wordStats.toArray(), [])
  const [hsk, setHsk] = useState<HskFilter>('all')
  const [status, setStatus] = useState<StatusFilter>('all')
  const [sort, setSort] = useState<VocabSort>('date')
  const [toRemove, setToRemove] = useState<VocabEntry | null>(null)

  const reads = useMemo(() => new Map((stats ?? []).map((s) => [s.key, s.timesRead])), [stats])
  const visible = useMemo(
    () =>
      filterAndSortVocab(entries ?? [], reads, {
        hsk,
        status,
        sort,
        threshold: settings.learnedThreshold,
        script: settings.scriptDisplay,
      }),
    [entries, reads, hsk, status, sort, settings.learnedThreshold, settings.scriptDisplay],
  )

  return (
    <div className="screen">
      <h1>Vocab</h1>
      <div className="row">
        <label>
          <span className="muted">HSK </span>
          <select value={hsk} onChange={(e) => setHsk(e.target.value)} aria-label="Filter by HSK level">
            {HSK_OPTIONS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <select value={status} onChange={(e) => setStatus(e.target.value as StatusFilter)} aria-label="Filter by status">
          <option value="all">New &amp; Learned</option>
          <option value="new">New</option>
          <option value="learned">Learned</option>
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value as VocabSort)} aria-label="Sort by">
          <option value="date">Date saved</option>
          <option value="reads">Read count</option>
          <option value="alpha">Alphabetical</option>
        </select>
        <div className="seg" role="group" aria-label="Script">
          <button aria-pressed={settings.scriptDisplay === 'simp'} onClick={() => void updateSettings({ scriptDisplay: 'simp' })}>
            简
          </button>
          <button aria-pressed={settings.scriptDisplay === 'trad'} onClick={() => void updateSettings({ scriptDisplay: 'trad' })}>
            繁
          </button>
        </div>
      </div>

      <p className="muted" aria-live="polite">
        {entries ? `${visible.length} of ${entries.length} words` : ''}
      </p>
      {entries && entries.length === 0 && (
        <p className="muted">No saved words yet. Hold a word while reading and tap “Add to vocab”.</p>
      )}

      {visible.map((e) => {
        const times = reads.get(e.key) ?? 0
        const st = wordStatus(times, settings.learnedThreshold)
        return (
          <div className="card" key={e.key} data-testid="vocab-item">
            <div className="row">
              <span className="vocab-word" lang={settings.scriptDisplay === 'trad' ? 'zh-Hant' : 'zh-Hans'}>
                {settings.scriptDisplay === 'trad' ? e.trad : e.simp}
              </span>
              <span>{e.py}</span>
              <span className="badge hsk">{hskLabel(e.hsk)}</span>
              <span className="badge">{st === 'learned' ? 'Learned' : 'New'}</span>
            </div>
            <div>{e.def}</div>
            <div className="muted">
              Read {times}× · saved {new Date(e.savedAt).toLocaleDateString()}
            </div>
            <div className="row">
              <input
                type="text"
                defaultValue={e.note}
                placeholder="Add a note"
                aria-label={`Note for ${e.simp}`}
                style={{ flex: 1 }}
                onBlur={(ev) => ev.target.value !== e.note && void setVocabNote(e.key, ev.target.value)}
              />
              <button className="danger" onClick={() => setToRemove(e)} aria-label={`Remove ${e.simp}`}>
                Remove
              </button>
            </div>
          </div>
        )
      })}

      {toRemove && (
        <Modal
          title="Remove from vocab?"
          onClose={() => setToRemove(null)}
          actions={
            <>
              <button onClick={() => setToRemove(null)}>Cancel</button>
              <button
                className="primary"
                onClick={() => {
                  void removeVocab(toRemove.key)
                  setToRemove(null)
                }}
              >
                Remove
              </button>
            </>
          }
        >
          <p>Remove “{toRemove.simp}” from your vocab list? Its read count is not affected.</p>
        </Modal>
      )}
    </div>
  )
}
