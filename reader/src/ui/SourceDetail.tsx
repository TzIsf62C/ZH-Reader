import { useState } from 'react'
import type { SourceDetail as Detail } from '../core/definition.ts'

/**
 * A collapsed "Why?" (contextual) or "Source" (glossary) toggle. Clicks don't reach the
 * surrounding panel, so opening it doesn't toggle the sentence translation.
 */
export function SourceDetail({ detail }: { detail: Detail | null }) {
  const [open, setOpen] = useState(false)
  if (!detail) return null
  const title = detail.kind === 'basis' ? 'Why?' : 'Source'
  return (
    <div className="source-detail" onClick={(e) => e.stopPropagation()}>
      <button
        className="link-button"
        aria-expanded={open}
        data-testid="source-toggle"
        onClick={() => setOpen(!open)}
      >
        {open ? '▾' : '▸'} {title}
      </button>
      {open && (
        <div className="source-body" data-testid="source-detail">
          {detail.kind === 'basis' ? (
            <>
              <span className="source-kind">{detail.label}:</span> {detail.note}
            </>
          ) : (
            <>
              <span className="source-kind">Glossary:</span> {detail.file}
              {' · '}
              {detail.url ? (
                <a href={detail.url} target="_blank" rel="noopener noreferrer">
                  {displayUrl(detail.url)} ↗
                </a>
              ) : detail.source === 'derived' ? (
                'derived from a longer glossary entry'
              ) : (
                detail.source
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

function displayUrl(url: string): string {
  const u = new URL(url)
  return decodeURI(u.host + u.pathname).replace(/\/$/, '')
}
