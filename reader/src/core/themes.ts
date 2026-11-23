import type { FontChoice, ThemeName } from './types.ts'

export interface ThemeColors {
  bg: string
  text: string
  muted: string
  panel: string
  border: string
  sentenceHighlight: string
  wordHold: string
  accent: string
  accentText: string
}

export const THEMES: Record<ThemeName, ThemeColors> = {
  light: {
    bg: '#fffdf8',
    text: '#1c1c1e',
    muted: '#5c5c60',
    panel: '#f3efe6',
    border: '#d9d3c4',
    sentenceHighlight: '#fff0a6',
    wordHold: '#ffd84d',
    accent: '#1f4e8c',
    accentText: '#ffffff',
  },
  dark: {
    bg: '#000000',
    text: '#f2f2f2',
    muted: '#a8a8ad',
    panel: '#141416',
    border: '#2c2c2e',
    sentenceHighlight: '#3a3a3c',
    wordHold: '#5c5c60',
    accent: '#7cb2ff',
    accentText: '#000000',
  },
  sepia: {
    bg: '#f4ecd8',
    text: '#3b2f1e',
    muted: '#6b5a40',
    panel: '#eadfc5',
    border: '#d2c4a2',
    sentenceHighlight: '#e6d3a3',
    wordHold: '#d4b06a',
    accent: '#7a4b12',
    accentText: '#ffffff',
  },
}

export const FONT_STACKS: Record<FontChoice, string> = {
  sans: '"PingFang SC", "PingFang TC", "Noto Sans CJK SC", "Noto Sans SC", "Microsoft YaHei", sans-serif',
  serif: '"Songti SC", "Songti TC", "Noto Serif CJK SC", "Noto Serif SC", "Source Han Serif SC", serif',
  kai: '"Kaiti SC", "Kaiti TC", "STKaiti", "KaiTi", serif',
}

function channel(v: number): number {
  const c = v / 255
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

export function luminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16)
  return 0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255)
}

export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

export function themeCssVars(theme: ThemeName): Record<string, string> {
  const c = THEMES[theme]
  return {
    '--bg': c.bg,
    '--text': c.text,
    '--muted': c.muted,
    '--panel': c.panel,
    '--border': c.border,
    '--sentence-highlight': c.sentenceHighlight,
    '--word-hold': c.wordHold,
    '--accent': c.accent,
    '--accent-text': c.accentText,
  }
}
