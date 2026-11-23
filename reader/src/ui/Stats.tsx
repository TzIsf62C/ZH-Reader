import { useLiveQuery } from 'dexie-react-hooks'
import { useState } from 'react'
import { formatDate, localDateString, weekDays, weekStart } from '../core/dates.ts'
import {
  computeTotals,
  goalForWeek,
  readingDates,
  setGoalFromWeek,
  weeklyHistory,
} from '../core/stats.ts'
import { db, updateSettings } from '../db/db.ts'
import { getLastSession, undoLastFinish } from '../db/finish.ts'
import { useSettings } from './hooks.ts'

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

export default function Stats() {
  const settings = useSettings()
  const sessions = useLiveQuery(() => db.readingSessions.toArray(), [])
  const wordStats = useLiveQuery(() => db.wordStats.toArray(), [])
  const lastSession = useLiveQuery(() => getLastSession(), [])
  const texts = useLiveQuery(() => db.texts.toArray(), [])
  const [message, setMessage] = useState('')

  if (!sessions || !wordStats) return null
  const today = localDateString()
  const thisWeek = weekStart(today)
  const dates = readingDates(sessions)
  const goal = goalForWeek(thisWeek, settings.goalHistory)
  const days = weekDays(thisWeek)
  const daysRead = days.filter((d) => dates.has(d)).length
  const history = weeklyHistory(sessions, settings.goalHistory, today).filter((w) => !w.current)
  const totals = computeTotals(sessions, wordStats, settings.learnedThreshold)
  const titleOf = (id: string) => texts?.find((t) => t.textId === id)?.title ?? 'a removed text'

  return (
    <div className="screen">
      <h1>Stats</h1>

      <h2>This week</h2>
      <div className="week" aria-label="Reading days this week">
        {days.map((d, i) => (
          <div
            key={d}
            className={`day${dates.has(d) ? ' done' : ''}${d === today ? ' today' : ''}`}
            aria-label={`${DAY_LABELS[i]} ${dates.has(d) ? 'read' : 'not read'}`}
            data-testid="week-day"
          >
            {DAY_LABELS[i]}
            <div aria-hidden="true">{dates.has(d) ? '✓' : '·'}</div>
          </div>
        ))}
      </div>
      <div data-testid="goal-progress">
        {daysRead} of {goal} {goal === 1 ? 'day' : 'days'} {daysRead >= goal ? '— goal met 🎉' : ''}
      </div>
      <div className="progress" style={{ margin: '6px 0 10px' }} role="progressbar" aria-valuenow={Math.min(daysRead, goal)} aria-valuemin={0} aria-valuemax={goal} aria-label="Weekly goal progress">
        <div style={{ width: `${Math.min(1, daysRead / goal) * 100}%` }} />
      </div>
      <div className="field">
        <label htmlFor="goal">Weekly goal (reading days). Applies from this week onward.</label>
        <select
          id="goal"
          value={goal}
          onChange={(e) =>
            void updateSettings({
              goalHistory: setGoalFromWeek(settings.goalHistory, thisWeek, Number(e.target.value)),
            })
          }
        >
          {[1, 2, 3, 4, 5, 6, 7].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </div>

      <h2>Totals</h2>
      <div className="card">
        <div>Texts finished: <strong data-testid="total-texts">{totals.textsFinished}</strong></div>
        <div>Word exposures: <strong data-testid="total-exposures">{totals.totalExposures}</strong></div>
        <div>
          Unique words: <strong data-testid="total-new">{totals.newWords}</strong> New ·{' '}
          <strong data-testid="total-learned">{totals.learnedWords}</strong> Learned
        </div>
        <div className="muted">Learned = read {settings.learnedThreshold}+ times in finished texts.</div>
      </div>

      <h2>Past weeks</h2>
      {history.length === 0 && <p className="muted">No completed weeks yet.</p>}
      {history.map((w) => (
        <div className="card row" key={w.weekStart} data-testid="history-week">
          <span>Week of {formatDate(w.weekStart)}</span>
          <span className="spacer" />
          <span>
            {w.daysRead}/{w.goal} days
          </span>
          <span className="badge" aria-label={w.met ? 'Goal met' : 'Goal not met'}>
            {w.met ? '✓ Met' : '✗ Missed'}
          </span>
        </div>
      ))}

      <h2>Undo</h2>
      {lastSession ? (
        <div>
          <p className="muted">
            Most recent finish: “{titleOf(lastSession.textId)}” on {formatDate(lastSession.completedDate)} (
            {lastSession.wordCount} words).
          </p>
          <button
            onClick={async () => {
              const res = await undoLastFinish()
              setMessage(res.status === 'undone' ? 'Last finish undone.' : 'Nothing to undo.')
            }}
          >
            Undo last finish
          </button>
        </div>
      ) : (
        <p className="muted">Nothing to undo.</p>
      )}
      <div role="status" aria-live="polite">
        {message}
      </div>
    </div>
  )
}
