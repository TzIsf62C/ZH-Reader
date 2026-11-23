import { useRef, useState } from 'react'
import { backupReminderDue, buildBackup, checkBackup, markExported, restoreBackup, type Backup } from '../db/backup.ts'
import { updateSettings } from '../db/db.ts'
import { DisplaySettings } from './DisplaySettings.tsx'
import { useSettings } from './hooks.ts'
import { Modal } from './Modal.tsx'

async function saveFile(name: string, json: string): Promise<boolean> {
  const file = new File([json], name, { type: 'application/json' })
  if (navigator.canShare?.({ files: [file] })) {
    try {
      await navigator.share({ files: [file], title: name })
      return true
    } catch (err) {
      if ((err as DOMException).name === 'AbortError') return false
    }
  }
  const url = URL.createObjectURL(file)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.append(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
  return true
}

export default function SettingsPage() {
  const s = useSettings()
  const [message, setMessage] = useState('')
  const [pending, setPending] = useState<Backup | null>(null)
  const input = useRef<HTMLInputElement>(null)

  async function exportAll() {
    const now = Date.now()
    const backup = await buildBackup(now)
    const stamp = new Date(now).toISOString().slice(0, 10)
    const saved = await saveFile(`zh-reader-backup-${stamp}.json`, JSON.stringify(backup))
    if (saved) {
      await markExported(now)
      setMessage('Backup exported.')
    }
  }

  async function onPick(file: File | undefined) {
    if (!file) return
    setMessage('')
    try {
      const check = checkBackup(JSON.parse(await file.text()))
      if (check.ok) setPending(check.backup)
      else setMessage(check.error)
    } catch {
      setMessage('That file is not valid JSON.')
    }
    if (input.current) input.current.value = ''
  }

  const due = backupReminderDue(s.lastExportAt, s.exportReminderDays)

  return (
    <div className="screen">
      <h1>Settings</h1>

      <h2>Display</h2>
      <DisplaySettings />
      <div className="field">
        <span id="lbl-script">Vocab script</span>
        <div className="seg" role="group" aria-labelledby="lbl-script">
          <button aria-pressed={s.scriptDisplay === 'simp'} onClick={() => void updateSettings({ scriptDisplay: 'simp' })}>
            Simplified
          </button>
          <button aria-pressed={s.scriptDisplay === 'trad'} onClick={() => void updateSettings({ scriptDisplay: 'trad' })}>
            Traditional
          </button>
        </div>
      </div>

      <h2>Learning</h2>
      <div className="field">
        <label htmlFor="threshold">Learned after this many reads</label>
        <input
          id="threshold"
          type="number"
          min={1}
          max={999}
          value={s.learnedThreshold}
          style={{ width: 90 }}
          onChange={(e) => {
            const n = Math.round(Number(e.target.value))
            if (n >= 1) void updateSettings({ learnedThreshold: n })
          }}
        />
      </div>

      <h2>Backup and restore</h2>
      <p className="muted" data-testid="last-export">
        Last export: {s.lastExportAt ? new Date(s.lastExportAt).toLocaleString() : 'never'}
        {due ? ' — a backup is due.' : ''}
      </p>
      <div className="field">
        <label htmlFor="reminder">Remind me every (days, 0 = never)</label>
        <input
          id="reminder"
          type="number"
          min={0}
          max={365}
          value={s.exportReminderDays}
          style={{ width: 90 }}
          onChange={(e) => {
            const n = Math.round(Number(e.target.value))
            if (n >= 0) void updateSettings({ exportReminderDays: n })
          }}
        />
      </div>
      <div className="row">
        <button className="primary" onClick={() => void exportAll()}>
          Export everything
        </button>
        <button onClick={() => input.current?.click()}>Restore from backup…</button>
        <input
          ref={input}
          type="file"
          accept=".json,application/json"
          hidden
          aria-label="Restore backup file"
          data-testid="restore-input"
          onChange={(e) => void onPick(e.target.files?.[0])}
        />
      </div>
      <p className="muted">
        The backup contains your texts, vocab, word counts, sessions and settings. Phone storage can be cleared
        if the app is unused for a while, so export regularly.
      </p>
      <div role="status" aria-live="polite">
        {message}
      </div>

      {pending && (
        <Modal
          title="Replace all data?"
          onClose={() => setPending(null)}
          actions={
            <>
              <button onClick={() => setPending(null)}>Cancel</button>
              <button
                className="primary"
                onClick={async () => {
                  await restoreBackup(pending)
                  setPending(null)
                  setMessage('Backup restored.')
                }}
              >
                Replace everything
              </button>
            </>
          }
        >
          <p>
            Restoring replaces <strong>all</strong> existing texts, vocab, statistics, sessions and settings on
            this device with the backup from {new Date(pending.exportedAt).toLocaleString()}. This cannot be
            undone.
          </p>
        </Modal>
      )}
    </div>
  )
}
