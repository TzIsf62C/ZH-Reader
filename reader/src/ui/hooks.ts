import { createContext, useContext } from 'react'
import type { Settings } from '../core/types.ts'
import { defaultSettings } from '../db/db.ts'

export const SettingsContext = createContext<Settings>(defaultSettings())

export function useSettings(): Settings {
  return useContext(SettingsContext)
}

export function hskLabel(hsk: string | null): string {
  if (hsk === null) return 'Not in HSK'
  return hsk === '7-9' ? 'HSK 7–9' : `HSK ${hsk}`
}
