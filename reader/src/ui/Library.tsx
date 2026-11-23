import { useLiveQuery } from 'dexie-react-hooks'
import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import type { TextFile, TextMeta } from '../core/types.ts'
import { backupReminderDue } from '../db/backup.ts'
import { db } from '../db/db.ts'
import { deleteText, importTexts, type DuplicateDecision, type ImportOutcome } from '../db/importer.ts'
import { useSettings } from './hooks.ts'
import { Modal } from './Modal.tsx'

const SCRIPT_LABEL = { simp: 'Simplified', trad: 'Traditional', mixed: 'Mixed' } as const

function Coverage({ coverage }: { coverage: Record<string, number> }) {
  const order = ['1', '2', '3', '4', '5', '6', '7-9', 'none']
  const entries = order.filter((k) => coverage[k]).map((k) => [k, coverage[k]] as const)
  if (entries.length === 0) return null
  const inHsk = entries.filter(([k]) => k !== 'none').reduce((n, [, v]) => n + v, 0)
  return (
    <div title={entries.map(([k, v]) => `${k === 'none' ? 'Not in HSK' : `HSK ${k}`}: ${Math.round(v * 100)}%`).join(', ')}>
      <div className="coverage" aria-hidden="true">
        {entries
          .filter(([k]) => k !== 'none')
          .map(([k, v], i) => (
            <span key={k} style={{ width: `${v * 100}%`, opacity: 1 - i * 0.1 }} />
          ))}
      </div>
      <span className="muted">HSK coverage {Math.round(inHsk * 100)}%</span>
    </div>
  )
}

export default function Library() {
  const settings = useSettings()
  const texts = useLiveQuery(() => db.texts.orderBy('importedAt').reverse().toArray(), [])
  const hasSessions = useLiveQuery(async () => (await db.readingSessions.count()) > 0, [])
  const [outcomes, setOutcomes] = useState<ImportOutcome[]>([])
  const [dup, setDup] = useState<{
    existing: TextMeta
    incoming: TextFile
    resolve: (d: DuplicateDecision) => void
  } | null>(null)
  const [toDelete, setToDelete] = useState<TextMeta | null>(null)
  const input = useRef<HTMLInputElement>(null)

  async function onFiles(list: FileList | null) {
    if (!list || list.length === 0) return
    const files = await Promise.all(
      Array.from(list).map(async (f) => ({ name: f.name, text: await f.text() })),
    )
    const result = await importTexts(
      files,
      (existing, incoming) =>
        new Promise<DuplicateDecision>((resolve) => setDup({ existing, incoming, resolve })),
    )
    setOutcomes(result)
    if (input.current) input.current.value = ''
  }

  const remind =
    !!hasSessions && backupReminderDue(settings.lastExportAt, settings.exportReminderDays)

  return (
    <div className="screen">
      <h1>Library</h1>
      {remind && (
        <div className="banner" role="status">
          Back up your data.{' '}
          {settings.lastExportAt
            ? `Last export: ${new Date(settings.lastExportAt).toLocaleDateString()}.`
            : 'You have never exported.'}{' '}
          <Link to="/settings">Export now</Link>
        </div>
      )}
      <div className="row">
        <button className="primary" onClick={() => input.current?.click()}>
          Import texts…
        </button>
        <input
          ref={input}
          type="file"
          accept=".json,application/json"
          multiple
          hidden
          aria-label="Import text files"
          data-testid="import-input"
          onChange={(e) => void onFiles(e.target.files)}
        />
      </div>

      <div aria-live="polite">
        {outcomes.map((o, i) => (
          <div key={i} className={`notice${o.status === 'error' ? ' error' : ''}`}>
            <strong>{o.fileName}</strong>:{' '}
            {o.status === 'error' && o.message}
            {o.status === 'imported' && `Imported “${o.title}”.`}
            {o.status === 'replaced' && `Replaced “${o.title}”.`}
            {o.status === 'skipped' && `Skipped “${o.title}” (already in library).`}
          </div>
        ))}
      </div>

      <h2>Texts</h2>
      {texts && texts.length === 0 && (
        <p className="muted">No texts yet. Import annotated JSON files produced by the preprocessor.</p>
      )}
      {texts?.map((t) => {
        const progress = t.tokenCount ? Math.min(1, t.position / t.tokenCount) : 0
        return (
          <div className="card" key={t.textId} data-testid="library-item">
            <Link to={`/read/${t.textId}`} style={{ color: 'inherit', textDecoration: 'none' }}>
              <div className="card-title">{t.title}</div>
              <div className="row muted">
                <span className="badge">{SCRIPT_LABEL[t.script]}</span>
                <span>{t.length.toLocaleString()} characters</span>
                <span>{t.wordCount.toLocaleString()} words</span>
                <span>Finished ×{t.finishedCount}</span>
              </div>
              <Coverage coverage={t.hskCoverage} />
              <div
                className="progress"
                role="progressbar"
                aria-valuenow={Math.round(progress * 100)}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label="Reading progress"
              >
                <div style={{ width: `${progress * 100}%` }} />
              </div>
              <span className="muted">{Math.round(progress * 100)}% read</span>
            </Link>
            <div className="row">
              <span className="spacer" />
              <button className="danger" onClick={() => setToDelete(t)} aria-label={`Delete ${t.title}`}>
                Delete
              </button>
            </div>
          </div>
        )
      })}

      {dup && (
        <Modal
          title="Already in your library"
          actions={
            <>
              <button
                onClick={() => {
                  dup.resolve('skip')
                  setDup(null)
                }}
              >
                Skip
              </button>
              <button
                className="primary"
                onClick={() => {
                  dup.resolve('replace')
                  setDup(null)
                }}
              >
                Replace
              </button>
            </>
          }
        >
          <p>
            “{dup.incoming.title}” is already imported. Replace it with this file? Your reading position and
            finish history are kept.
          </p>
        </Modal>
      )}

      {toDelete && (
        <Modal
          title="Delete text?"
          onClose={() => setToDelete(null)}
          actions={
            <>
              <button onClick={() => setToDelete(null)}>Cancel</button>
              <button
                className="primary"
                onClick={() => {
                  void deleteText(toDelete.textId)
                  setToDelete(null)
                }}
              >
                Delete
              </button>
            </>
          }
        >
          <p>
            Remove “{toDelete.title}” from the library? Word counts and reading history are not affected.
          </p>
        </Modal>
      )}
    </div>
  )
}
