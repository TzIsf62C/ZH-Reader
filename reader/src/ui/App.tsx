import { useLiveQuery } from 'dexie-react-hooks'
import { useEffect } from 'react'
import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { THEMES, themeCssVars } from '../core/themes.ts'
import { getSettings } from '../db/db.ts'
import { SettingsContext } from './hooks.ts'
import Library from './Library.tsx'
import Reader from './Reader.tsx'
import SettingsPage from './SettingsPage.tsx'
import Stats from './Stats.tsx'
import Vocab from './Vocab.tsx'

export default function App() {
  const settings = useLiveQuery(() => getSettings(), [])
  const { pathname } = useLocation()

  useEffect(() => {
    if (!settings) return
    const root = document.documentElement
    for (const [k, v] of Object.entries(themeCssVars(settings.theme))) root.style.setProperty(k, v)
    root.dataset.theme = settings.theme
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', THEMES[settings.theme].bg)
  }, [settings])

  useEffect(() => {
    // Ask the browser not to evict our storage (best effort; Safari may ignore it).
    void navigator.storage?.persist?.()
  }, [])

  if (!settings) return null
  const inReader = pathname.startsWith('/read/')

  return (
    <SettingsContext.Provider value={settings}>
      <div className="shell">
        <Routes>
          <Route path="/" element={<Library />} />
          <Route path="/read/:textId" element={<Reader />} />
          <Route path="/vocab" element={<Vocab />} />
          <Route path="/stats" element={<Stats />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Library />} />
        </Routes>
        {!inReader && (
          <nav className="nav" aria-label="Main">
            <NavLink to="/" end>
              Library
            </NavLink>
            <NavLink to="/vocab">Vocab</NavLink>
            <NavLink to="/stats">Stats</NavLink>
            <NavLink to="/settings">Settings</NavLink>
          </nav>
        )}
      </div>
    </SettingsContext.Provider>
  )
}
