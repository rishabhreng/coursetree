import { useEffect, useState } from 'react'
import './App.css'

const DEFAULT_TERM_CODE = '202710'

function App() {
  const [query, setQuery] = useState('')
  const [termCode, setTermCode] = useState('all')
  const [terms, setTerms] = useState([])
  const [results, setResults] = useState({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [expandedCourses, setExpandedCourses] = useState(new Set())
  const [syllabusLookup, setSyllabusLookup] = useState({})

  useEffect(() => {
    const fetchTerms = async () => {
      try {
        const res = await fetch('/api/terms')
        if (!res.ok) throw new Error(`Failed to fetch terms: ${res.status}`)
        const data = await res.json()
        setTerms(Array.isArray(data) ? data : [])
      } catch (err) {
        console.error('Error fetching terms:', err)
      }
    }

    fetchTerms()
  }, [])

  // Convert term code to readable format (e.g., 202710 -> Fall 2026)
  const getTermLabel = (code) => {
    const foundTerm = terms.find((term) => term.code === code)
    if (foundTerm) return foundTerm.term
    if (code === DEFAULT_TERM_CODE) return 'Current Term'
    return code
  }

  const normalizeResults = (payload) => {
    if (Array.isArray(payload)) {
      // Backward/forward compatible: some API versions return a list of rows.
      return payload.reduce((acc, course) => {
        const key = course.crs || `${course.term}-${course.crn}`
        if (!acc[key]) acc[key] = []
        acc[key].push(course)
        return acc
      }, {})
    }

    if (payload && typeof payload === 'object') {
      return payload
    }

    return {}
  }

  const doSearch = async () => {
    if (!query.trim()) {
      setResults({})
      return
    }

    setLoading(true)
    setError(null)

    try {
      const searchParams = new URLSearchParams({ q: query.trim() })
      if (termCode.trim()) {
        searchParams.set('term_code', termCode.trim())
      }

      const res = await fetch(`/api/courses/?${searchParams.toString()}`)
      if (!res.ok) throw new Error(`Search failed ${res.status}`)
      const json = await res.json()

      setResults(normalizeResults(json))
      setExpandedCourses(new Set())
      setSyllabusLookup({})
    } catch (err) {
      setError(err.message ?? 'Unable to fetch')
    } finally {
      setLoading(false)
    }
  }

  const onEnter = (e) => {
    if (e.key === 'Enter') {
      doSearch()
    }
  }

  const toggleExpanded = (courseCode) => {
    const newExpanded = new Set(expandedCourses)
    if (newExpanded.has(courseCode)) {
      newExpanded.delete(courseCode)
    } else {
      newExpanded.add(courseCode)
    }
    setExpandedCourses(newExpanded)
  }

  // Format instructors: split by common delimiters and join nicely
  const formatInstructors = (instructorStr) => {
    if (!instructorStr || instructorStr === 'TBA' || instructorStr === '[]') return 'TBA'

    const trimmed = instructorStr.trim()

    // Parse JSON-like arrays from the scraper, e.g. ["A", "B"]
    if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
      try {
        const parsed = JSON.parse(trimmed)
        if (Array.isArray(parsed)) {
          const names = parsed.map((name) => String(name).trim()).filter(Boolean)
          return names.length > 0 ? names.join(', ') : 'TBA'
        }
      } catch {
        // Fall through to delimiter-based parsing below.
      }
    }

    const instructors = instructorStr
      .split(/,|;\s*/)
      .map(s => s.trim())
      .filter(s => s.length > 0)

    return instructors.length > 0 ? instructors.join(', ') : 'TBA'
  }

  const courseEntries = Object.entries(results).sort()

  const getSyllabusKey = (course) => `${course.term}-${course.crn}`

  const fetchSyllabus = async (course) => {
    const key = getSyllabusKey(course)

    setSyllabusLookup((prev) => ({
      ...prev,
      [key]: { status: 'loading', message: 'Checking syllabus...' },
    }))

    try {
      const params = new URLSearchParams({ term_code: course.term, crn: course.crn })
      const res = await fetch(`/api/syllabus?${params.toString()}`)
      if (!res.ok) {
        throw new Error(`Syllabus lookup failed ${res.status}`)
      }

      const data = await res.json()
      if (data.syllabus_url) {
        setSyllabusLookup((prev) => ({
          ...prev,
          [key]: {
            status: 'available',
            message: data.message || 'Syllabus available',
            url: data.syllabus_url,
          },
        }))
      } else {
        setSyllabusLookup((prev) => ({
          ...prev,
          [key]: {
            status: 'none',
            message: data.message || 'No syllabus posted',
          },
        }))
      }
    } catch (err) {
      setSyllabusLookup((prev) => ({
        ...prev,
        [key]: {
          status: 'error',
          message: err.message || 'Unable to fetch syllabus',
        },
      }))
    }
  }

  return (
    <div className="app">
      <div className="header">
        <h1>Rice Course Explorer</h1>
        <p className="tagline">Search for courses across all terms</p>
      </div>

      <div className="container">
        <section className="search-section">
          <div className="search-inputs">
            <div className="input-group">
              <div className="label-with-tooltip">
                <label htmlFor="query">Course Search</label>
                <div className="tooltip-wrap">
                  <button
                    type="button"
                    className="tooltip-trigger"
                    aria-label="Search help"
                    aria-describedby="query-tooltip"
                  >
                    ?
                  </button>
                  <div id="query-tooltip" role="tooltip" className="tooltip-text">
                    Type CRN, CRS, course title, instructor, or any combination.
                  </div>
                </div>
              </div>
              <input
                id="query"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={onEnter}
                placeholder="Search courses"
              />
            </div>

            <div className="input-group">
              <label htmlFor="term">Term</label>
              <select
                id="term"
                value={termCode}
                onChange={(e) => setTermCode(e.target.value)}
              >
                <option value="all">All Terms</option>
                {terms.map((term) => (
                  <option key={term.code} value={term.code}>
                    {term.term}
                  </option>
                ))}
                {!terms.some((term) => term.code === DEFAULT_TERM_CODE) && (
                  <option value={DEFAULT_TERM_CODE}>Current Term</option>
                )}
              </select>
            </div>

            <button
              className="search-btn"
              onClick={doSearch}
            >
              Search
            </button>
          </div>

          {loading && <p className="status loading">Loading...</p>}
          {error && <p className="status error">❌ {error}</p>}
          {!loading && courseEntries.length === 0 && query && !error && (
            <p className="status empty">No courses found for your search</p>
          )}
        </section>

        <section className="results-section">
          <div className="courses-grid">
            {courseEntries.map(([courseCode, courseInstances]) => {
              const isExpanded = expandedCourses.has(courseCode)
              const displayInstances = isExpanded
                ? courseInstances
                : [courseInstances[0]]

              const firstCourse = courseInstances[0]
              const courseUrl = firstCourse.course_page ||
                `https://courses.rice.edu/courses/courses/!SWKSCAT.cat?p_action=COURSE&p_term=${firstCourse.term}&p_crn=${firstCourse.crn}`

              return (
                <div key={courseCode} className="course-group">
                  <a
                    href={courseUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="course-header"
                    onClick={(e) => {
                      // Allow expand/collapse if clicking the expand indicator
                      if (e.target.closest('.expand-indicator')) {
                        e.preventDefault()
                        courseInstances.length > 1 && toggleExpanded(courseCode)
                      }
                    }}
                  >
                    <div className="header-left">
                      <h3>{courseCode}</h3>
                      {courseInstances.length > 0 && (
                        <p className="course-title">{courseInstances[0].title}</p>
                      )}
                    </div>
                    {courseInstances.length > 1 && (
                      <div
                        className="expand-indicator"
                        onClick={(e) => {
                          e.stopPropagation()
                          e.preventDefault()
                          toggleExpanded(courseCode)
                        }}
                      >
                        <span className="badge">{courseInstances.length}</span>
                        <span className={`chevron ${isExpanded ? 'open' : ''}`}>▸</span>
                      </div>
                    )}
                  </a>

                  <div className="course-instances">
                    {displayInstances.map((course) => {
                      const syllabusState = syllabusLookup[getSyllabusKey(course)]
                      const coursePageUrl = course.course_page || `https://courses.rice.edu/courses/courses/!SWKSCAT.cat?p_action=COURSE&p_term=${course.term}&p_crn=${course.crn}`

                      return (
                        <div
                          key={`${course.crn}-${course.term}`}
                          className="course-card"
                        >
                          <div className="card-meta">
                            <span className="term">{getTermLabel(course.term)}</span>
                            <span className="crn">CRN: {course.crn}</span>
                            {course.credits && <span className="credits">{course.credits} credits</span>}
                          </div>

                          <div className="course-details">
                            {course.instructors && (
                              <div className="detail-row">
                                <strong>Instructors:</strong>
                                <span>{formatInstructors(course.instructors)}</span>
                              </div>
                            )}
                            {course.meeting_times && (
                              <div className="detail-row">
                                <strong>Times:</strong>
                                <span>{course.meeting_times}</span>
                              </div>
                            )}
                          </div>

                          <div className="card-actions">
                            <a
                              href={coursePageUrl}
                              target="_blank"
                              rel="noreferrer"
                              className="course-page-link"
                            >
                              Course page
                            </a>
                            <button
                              type="button"
                              className="syllabus-btn"
                              onClick={() => fetchSyllabus(course)}
                              disabled={syllabusState?.status === 'loading'}
                            >
                              {syllabusState?.status === 'loading' ? 'Checking...' : 'Get syllabus'}
                            </button>
                          </div>

                          {syllabusState?.status === 'available' && syllabusState.url && (
                            <p className="syllabus-status success">
                              <a href={syllabusState.url} target="_blank" rel="noreferrer">
                                Open syllabus
                              </a>
                            </p>
                          )}

                          {syllabusState?.status === 'none' && (
                            <p className="syllabus-status neutral">{syllabusState.message}</p>
                          )}

                          {syllabusState?.status === 'error' && (
                            <p className="syllabus-status error">{syllabusState.message}</p>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      </div>
    </div>
  )
}

export default App