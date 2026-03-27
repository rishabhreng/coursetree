import { useState } from 'react'
import './App.css'

function App() {
  const [query, setQuery] = useState('')
  const [termCode, setTermCode] = useState('')
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const doSearch = async (useTerm = false) => {
    if (!query.trim()) {
      setResults([])
      return
    }

    setLoading(true)
    setError(null)

    try {
      // Use /api/courses and /api/courses/all endpoints from backend
      const endpoint = useTerm && termCode.trim() ? '/api/courses/' : '/api/courses/all'
      const searchParams = new URLSearchParams({ q: query.trim() })
      if (useTerm && termCode.trim()) {
        searchParams.set('term_code', termCode.trim())
      }

      const res = await fetch(`${endpoint}?${searchParams.toString()}`)
      if (!res.ok) throw new Error(`Search failed ${res.status}`)
      const json = await res.json()

      // backend returns grouped course buckets; flatten + dedupe by CRN.
      const flat = Object.values(json)
        .flat()
        .map((course) => ({ ...course }))

      const deduped = Array.from(
        flat.reduce((acc, course) => {
          const key = course.crn || `${course.crs || ''}-${course.title || ''}`
          if (!acc.has(key)) acc.set(key, course)
          return acc
        }, new Map())
          .values(),
      )

      setResults(deduped)
    } catch (err) {
      setError(err.message ?? 'Unable to fetch')
    } finally {
      setLoading(false)
    }
  }

  const onEnter = (e) => {
    if (e.key === 'Enter') {
      doSearch(Boolean(termCode.trim()))
    }
  }

  return (
    <div className="dark-root">
      <div className="app-container">
        <section className="search-box">
          <h1>Rice Course Explorer</h1>
          <div className="fieldset">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={onEnter}
              placeholder="Type course text, e.g. MATH, 212, fundamentals"
            />
            <input
              value={termCode}
              onChange={(e) => setTermCode(e.target.value)}
              onKeyDown={onEnter}
              placeholder="Optional term code (e.g. 202620)"
            />
            <button onClick={() => doSearch(Boolean(termCode.trim()))}>
              Search
            </button>
          </div>

          {loading && <p className="status">Loading...</p>}
          {error && <p className="error">{error}</p>}
          {!loading && results.length === 0 && query && !error && <p className="status">No results yet</p>}

          <ul className="result-list">
            {results.map((r) => {
              const uniqueKey = r.crn ? `${r.crn}` : `${r.crs || r.course_key}-${r.title}`
              return (
                <li key={uniqueKey} className="result-item">
                  <div className="result-header">
                    <strong>{r.crs || r.course_key || 'Unknown course'}</strong> {r.title}
                  </div>
                  <div className="result-detail">Term: {r.term || termCode || 'all'}</div>
                  <div className="result-detail">CRN: {r.crn || 'N/A'}</div>
                  <div className="result-detail">Instructors: {r.instructors || 'TBA'}</div>
                  {r.meeting_times ? <div className="result-detail">Times: {r.meeting_times}</div> : null}
                  {r.credits ? <div className="result-detail">Credits: {r.credits}</div> : null}
                  <div className="result-detail">Score: {typeof r.score === 'number' ? r.score.toFixed(1) : '—'}</div>
                  {r.course_page ? (
                    <a className="result-link" href={r.course_page} target="_blank" rel="noreferrer">
                      Course page
                    </a>
                  ) : (
                    <a
                      className="result-link"
                      href={`https://courses.rice.edu/admweb/!SWKSCAT.cat?p_action=COURSE&p_term=${r.term || termCode || '202710'}&p_crn=${r.crn || ''}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Course page (fallback)
                    </a>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      </div>
    </div>
  )
}

export default App