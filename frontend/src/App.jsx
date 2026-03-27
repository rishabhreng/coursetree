import { useState, useEffect } from 'react'
import './App.css'

function App() {
  const [query, setQuery] = useState('')
  const [termCode, setTermCode] = useState('')
  const [terms, setTerms] = useState([])
  const [results, setResults] = useState({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [expandedCourses, setExpandedCourses] = useState(new Set())

  // Fetch available terms on mount
  useEffect(() => {
    const fetchTerms = async () => {
      try {
        const res = await fetch('/api/terms')
        if (!res.ok) throw new Error('Failed to fetch terms')
        const data = await res.json()
        setTerms(data)
        // Set default to first term
        if (data.length > 0) {
          setTermCode(data[0].code)
        }
      } catch (err) {
        console.error('Error fetching terms:', err)
      }
    }
    fetchTerms()
  }, [])

  // Convert term code to readable format (e.g., 202710 -> Fall 2026)
  const getTermLabel = (code) => {
    const foundTerm = terms.find(t => t.code === code)
    return foundTerm ? foundTerm.term : code
  }

  const doSearch = async (useTerm = false) => {
    if (!query.trim()) {
      setResults({})
      return
    }

    setLoading(true)
    setError(null)

    try {
      const endpoint = useTerm && termCode.trim() ? '/api/courses/' : '/api/courses/all'
      const searchParams = new URLSearchParams({ q: query.trim() })
      if (useTerm && termCode.trim()) {
        searchParams.set('term_code', termCode.trim())
      }

      const res = await fetch(`${endpoint}?${searchParams.toString()}`)
      if (!res.ok) throw new Error(`Search failed ${res.status}`)
      const json = await res.json()

      setResults(json)
      setExpandedCourses(new Set())
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
    if (!instructorStr || instructorStr === 'TBA') return 'TBA'
    // Split by common delimiters
    const instructors = instructorStr
      .split(/,|;\s*/)
      .map(s => s.trim())
      .filter(s => s.length > 0)
    return instructors.join(', ')
  }

  const courseEntries = Object.entries(results).sort()

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
              <label htmlFor="query">Course Search</label>
              <input
                id="query"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={onEnter}
                placeholder="e.g., MATH, Linear Algebra, Data Science"
              />
            </div>

            <div className="input-group">
              <label htmlFor="term">Term</label>
              <select
                id="term"
                value={termCode}
                onChange={(e) => setTermCode(e.target.value)}
              >
                <option value="">All Terms</option>
                {terms.map((term) => (
                  <option key={term.code} value={term.code}>
                    {term.term}
                  </option>
                ))}
              </select>
            </div>

            <button
              className="search-btn"
              onClick={() => doSearch(Boolean(termCode.trim()))}
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
                `https://courses.rice.edu/admweb/!SWKSCAT.cat?p_action=COURSE&p_term=${firstCourse.term}&p_crn=${firstCourse.crn}`

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
                    {displayInstances.map((course, idx) => (
                      <a
                        key={`${course.crn}-${course.term}`}
                        className="course-card course-link"
                        href={course.course_page || `https://courses.rice.edu/admweb/!SWKSCAT.cat?p_action=COURSE&p_term=${course.term}&p_crn=${course.crn}`}
                        target="_blank"
                        rel="noreferrer"
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
                      </a>
                    ))}
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