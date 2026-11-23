import { describe, expect, it } from 'vitest'
import { addDays, localDateString, weekStart } from './dates.ts'
import {
  computeTotals,
  goalForWeek,
  setGoalFromWeek,
  weekResult,
  weeklyHistory,
  wordStatus,
  readingDates,
} from './stats.ts'
import type { GoalEntry, ReadingSession, WordStat } from './types.ts'

const session = (date: string, undone = false, id = 1): ReadingSession => ({
  id,
  textId: 't',
  completedDate: date,
  wordCount: 1,
  undone,
  counts: {},
})

describe('dates', () => {
  it('formats local dates without UTC shift', () => {
    expect(localDateString(new Date(2026, 10, 22, 23, 59))).toBe('2026-11-22')
    expect(localDateString(new Date(2026, 0, 1, 0, 5))).toBe('2026-01-01')
  })

  it('weeks run Monday to Sunday', () => {
    expect(weekStart('2026-11-22')).toBe('2026-11-16') // Sunday
    expect(weekStart('2026-11-16')).toBe('2026-11-16') // Monday
    expect(weekStart('2026-11-18')).toBe('2026-11-16')
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01')
  })
})

describe('word status', () => {
  it('is New below the threshold and Learned at or above it', () => {
    expect(wordStatus(9, 10)).toBe('new')
    expect(wordStatus(10, 10)).toBe('learned')
    expect(wordStatus(9, 5)).toBe('learned')
  })
})

describe('weekly goal', () => {
  const history: GoalEntry[] = [{ weekStart: '1970-01-05', goalDays: 3 }]

  it('counts two readings on one day as one goal day', () => {
    const dates = readingDates([session('2026-11-17', false, 1), session('2026-11-17', false, 2)])
    const r = weekResult('2026-11-16', dates, history, '2026-11-18')
    expect(r.daysRead).toBe(1)
    expect(r.met).toBe(false)
  })

  it('ignores undone sessions', () => {
    const dates = readingDates([session('2026-11-17', true)])
    expect(weekResult('2026-11-16', dates, history, '2026-11-18').daysRead).toBe(0)
  })

  it('meets the goal when distinct days reach the target', () => {
    const sessions = ['2026-11-16', '2026-11-18', '2026-11-22'].map((d, i) => session(d, false, i))
    const r = weekResult('2026-11-16', readingDates(sessions), history, '2026-11-22')
    expect(r.met).toBe(true)
  })

  it('changing the goal does not alter earlier weeks', () => {
    const sessions = ['2026-11-02', '2026-11-03', '2026-11-04'].map((d, i) => session(d, false, i))
    const before = weeklyHistory(sessions, history, '2026-11-18')
    const updated = setGoalFromWeek(history, '2026-11-16', 5)
    const after = weeklyHistory(sessions, updated, '2026-11-18')
    const pastBefore = before.find((w) => w.weekStart === '2026-11-02')!
    const pastAfter = after.find((w) => w.weekStart === '2026-11-02')!
    expect(pastAfter).toEqual(pastBefore)
    expect(pastAfter.met).toBe(true)
    expect(after[0].goal).toBe(5)
    expect(goalForWeek('2026-11-09', updated)).toBe(3)
    expect(goalForWeek('2026-11-23', updated)).toBe(5)
  })

  it('lists weeks newest first including empty weeks', () => {
    const res = weeklyHistory([session('2026-11-02')], history, '2026-11-18')
    expect(res.map((w) => w.weekStart)).toEqual(['2026-11-16', '2026-11-09', '2026-11-02'])
    expect(res[0].current).toBe(true)
  })
})

describe('totals', () => {
  it('splits unique words by status', () => {
    const stat = (key: string, timesRead: number): WordStat => ({
      key,
      simp: key,
      trad: key,
      py: '',
      def: '',
      hsk: null,
      timesRead,
      firstRead: '',
      lastRead: '',
    })
    const totals = computeTotals([session('2026-11-02')], [stat('a', 9), stat('b', 10), stat('c', 25)], 10)
    expect(totals).toEqual({ textsFinished: 1, totalExposures: 44, newWords: 1, learnedWords: 2 })
  })
})
