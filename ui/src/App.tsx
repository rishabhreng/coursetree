import { useEffect, useState } from 'react'
import './App.css'

type CourseSection = {
  term: string
  crn: string
  crs: string
  title: string
  instructors: string
  meeting_times: string
  credits: string
  course_page?: string | null
  score?: number
}

type SearchResponse = Record<string, CourseSection[]>

function App() {
  const [q, setQ] = useState('COMP 182')
  const [term, setTerm] = useState('202710')
  const [top, setTop] = useState(15)
  const [results, setResults] = useState<SearchResponse>({})
  const [error, setError] = useState<string | null>(null)

  const apiBase = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'

  const fetchData = async (endpoint: 'search' | 'searchall') => {
    try {
      setError(null)
      const url =
        endpoint === 'search'
          ? `${apiBase}/search/?q=${encodeURIComponent(q)}&term_code=${encodeURIComponent(term)}&top_n_results=${top}`
          : `${apiBase}/searchall?q=${encodeURIComponent(q)}&top_n_results=${top}`

      const response = await fetch(url)
      const text = await response.text()
      if (!response.ok) {
        throw new Error(`Status ${response.status}: ${text}`)
      }
      if (!text.trim().startsWith('{') && !text.trim().startsWith('[')) {
        throw new Error(`Expected JSON response but got: ${text.slice(0, 260)}`)
      }
      const data = (JSON.parse(text) as SearchResponse)
      setResults(data)
    } catch (err) {
      setError((err as Error).message || 'Unexpected error')
    }
  }

  useEffect(() => {
    fetchData('search')
  }, [])

  return (
    <main className="app-shell">
      <div className="toolbar">
        <div className="title-area">
          <h1>Course Search Debug (React App.tsx)</h1>
          <p>Use this as your app UI for deployment.</p>
        </div>

        <div className="controls">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="query" />
          <input value={term} onChange={(e) => setTerm(e.target.value)} placeholder="term" />
          <input type="number" min={1} value={top} onChange={(e) => setTop(Number(e.target.value))} />
          <button onClick={() => fetchData('search')}>Search</button>
          <button onClick={() => fetchData('searchall')}>Search All</button>
        </div>
      </div>

      {error && <div className="error">Error: {error}</div>}

      <section className="results">
        {Object.entries(results).map(([courseCode, sections]) => (
          <details key={courseCode} className="course-group">
            <summary>
              {courseCode} <span className="badge">{sections.length}</span>
            </summary>
            <div className="section-list">
              {sections.map((section, idx) => (
                <article key={`${courseCode}-${section.crn}-${idx}`} className="section-card">
                  <h3>{section.crs} - {section.title}</h3>
                  <p className="meta">
                    <span>{section.term}</span>
                    <span>{section.crn}</span>
                    <span>{section.credits} cr</span>
                  </p>
                  <p><strong>Instructor:</strong> {section.instructors || 'TBA'}</p>
                  <p><strong>Time:</strong> {section.meeting_times || 'TBA'}</p>
                  {section.course_page && (
                    <a className="link" href={section.course_page} target="_blank" rel="noreferrer">
                      Course page
                    </a>
                  )}
                </article>
              ))}
            </div>
          </details>
        ))}
      </section>
    </main>
  )
}

export default App
