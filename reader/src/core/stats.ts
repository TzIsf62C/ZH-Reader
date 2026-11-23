import { addDays, weekStart } from './dates.ts'
import type { GoalEntry, ReadingSession, WordStat } from './types.ts'

export type WordStatus = 'new' | 'learned'

export function wordStatus(timesRead: number, threshold: number): WordStatus {
  return timesRead >= threshold ? 'learned' : 'new'
}

/** Goal in effect for a week: the latest entry that started on or before it. */
export function goalForWeek(week: string, history: GoalEntry[]): number {
  const sorted = [...history].sort((a, b) => a.weekStart.localeCompare(b.weekStart))
  let goal = sorted[0]?.goalDays ?? 3
  for (const e of sorted) if (e.weekStart <= week) goal = e.goalDays
  return goal
}

/** Sets the goal from the given week onward without touching earlier weeks. */
export function setGoalFromWeek(history: GoalEntry[], week: string, goalDays: number): GoalEntry[] {
  const kept = history.filter((e) => e.weekStart < week)
  return [...kept, { weekStart: week, goalDays }].sort((a, b) => a.weekStart.localeCompare(b.weekStart))
}

export function readingDates(sessions: ReadingSession[]): Set<string> {
  return new Set(sessions.filter((s) => !s.undone).map((s) => s.completedDate))
}

export interface WeekResult {
  weekStart: string
  days: string[]
  daysRead: number
  goal: number
  met: boolean
  current: boolean
}

export function weekResult(
  week: string,
  dates: Set<string>,
  history: GoalEntry[],
  today: string,
): WeekResult {
  const days = Array.from({ length: 7 }, (_, i) => addDays(week, i)).filter((d) => dates.has(d))
  const goal = goalForWeek(week, history)
  return {
    weekStart: week,
    days,
    daysRead: days.length,
    goal,
    met: days.length >= goal,
    current: week === weekStart(today),
  }
}

/** Newest first, from the week of the first reading up to the current week. */
export function weeklyHistory(
  sessions: ReadingSession[],
  history: GoalEntry[],
  today: string,
): WeekResult[] {
  const dates = readingDates(sessions)
  const thisWeek = weekStart(today)
  const first = [...dates].sort()[0]
  const results: WeekResult[] = []
  if (!first) return [weekResult(thisWeek, dates, history, today)]
  for (let w = thisWeek; w >= weekStart(first); w = addDays(w, -7)) {
    results.push(weekResult(w, dates, history, today))
  }
  return results
}

export interface Totals {
  textsFinished: number
  totalExposures: number
  newWords: number
  learnedWords: number
}

export function computeTotals(
  sessions: ReadingSession[],
  stats: WordStat[],
  threshold: number,
): Totals {
  const active = sessions.filter((s) => !s.undone)
  let newWords = 0
  let learnedWords = 0
  let totalExposures = 0
  for (const w of stats) {
    totalExposures += w.timesRead
    if (wordStatus(w.timesRead, threshold) === 'learned') learnedWords++
    else newWords++
  }
  return {
    textsFinished: new Set(active.map((s) => s.textId)).size,
    totalExposures,
    newWords,
    learnedWords,
  }
}
