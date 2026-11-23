import { useLiveQuery } from 'dexie-react-hooks'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { formatDate } from '../core/dates.ts'
import { db } from '../db/db.ts'
import { finishText, getLastSession, undoLastFinish } from '../db/finish.ts'
import { Modal } from './Modal.tsx'

export function EndCard({
  textId,
  title,
  left,
  width,
  active,
}: {
  textId: string
  title: string
  left: number
  width: number
  active: boolean
}) {
  const navigate = useNavigate()
  const meta = useLiveQuery(() => db.texts.get(textId), [textId])
  const lastSession = useLiveQuery(() => getLastSession(), [])
  const [confirmDate, setConfirmDate] = useState<string | null>(null)
  const [message, setMessage] = useState('')

  async function finish(confirmRepeat: boolean) {
    const res = await finishText(textId, { confirmRepeat })
    if (res.status === 'needs-confirmation') setConfirmDate(res.lastDate)
    else if (res.status === 'finished') {
      setMessage(`Counted ${res.session.wordCount} words.`)
    }
  }

  async function undo() {
    const res = await undoLastFinish()
    setMessage(res.status === 'undone' ? 'Last finish undone.' : 'Nothing to undo.')
  }

  const canUndo = lastSession && lastSession.textId === textId

  return (
    <div className="end-card" style={{ left, width }} data-testid="end-card" inert={!active}>
      <h2>End of “{title}”</h2>
      <p className="muted">
        {meta?.finishedCount ? `Finished ×${meta.finishedCount}` : 'Not finished yet'}
      </p>
      <button className="primary" onClick={() => void finish(false)}>
        Mark as finished
      </button>
      {canUndo && (
        <button onClick={() => void undo()}>
          Undo last finish ({formatDate(lastSession.completedDate)})
        </button>
      )}
      <button onClick={() => navigate('/')}>Back to library</button>
      <div role="status" aria-live="polite" data-testid="finish-message">
        {message}
      </div>
      {confirmDate && (
        <Modal
          title="Finish again?"
          onClose={() => setConfirmDate(null)}
          actions={
            <>
              <button onClick={() => setConfirmDate(null)}>Cancel</button>
              <button
                className="primary"
                onClick={() => {
                  setConfirmDate(null)
                  void finish(true)
                }}
              >
                Count again
              </button>
            </>
          }
        >
          <p>You already finished this on {formatDate(confirmDate)}. Count it again?</p>
        </Modal>
      )}
    </div>
  )
}
