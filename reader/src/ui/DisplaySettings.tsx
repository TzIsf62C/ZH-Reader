import type { FontChoice, ThemeName } from '../core/types.ts'
import { updateSettings } from '../db/db.ts'
import { useSettings } from './hooks.ts'

const THEME_LABELS: Record<ThemeName, string> = { light: 'Light', dark: 'Dark', sepia: 'Sepia' }
const FONT_LABELS: Record<FontChoice, string> = { sans: 'Sans', serif: 'Serif', kai: 'Kai' }

export function DisplaySettings() {
  const s = useSettings()
  return (
    <div>
      <div className="field">
        <span id="lbl-theme">Theme</span>
        <div className="seg" role="group" aria-labelledby="lbl-theme">
          {(Object.keys(THEME_LABELS) as ThemeName[]).map((t) => (
            <button key={t} aria-pressed={s.theme === t} onClick={() => void updateSettings({ theme: t })}>
              {THEME_LABELS[t]}
            </button>
          ))}
        </div>
      </div>
      <div className="field">
        <span id="lbl-font">Font</span>
        <div className="seg" role="group" aria-labelledby="lbl-font">
          {(Object.keys(FONT_LABELS) as FontChoice[]).map((f) => (
            <button key={f} aria-pressed={s.fontChoice === f} onClick={() => void updateSettings({ fontChoice: f })}>
              {FONT_LABELS[f]}
            </button>
          ))}
        </div>
      </div>
      <div className="field">
        <label htmlFor="fs">Text size ({s.fontSize}px)</label>
        <input
          id="fs"
          type="range"
          min={16}
          max={44}
          step={1}
          value={s.fontSize}
          onChange={(e) => void updateSettings({ fontSize: Number(e.target.value) })}
        />
      </div>
      <div className="field">
        <label htmlFor="ls">Line spacing ({s.lineSpacing.toFixed(1)})</label>
        <input
          id="ls"
          type="range"
          min={1.3}
          max={2.6}
          step={0.1}
          value={s.lineSpacing}
          onChange={(e) => void updateSettings({ lineSpacing: Number(Number(e.target.value).toFixed(1)) })}
        />
      </div>
    </div>
  )
}
