import { useEffect, useState } from 'react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, Cell } from 'recharts'
import './App.css'

const DEFAULT_TERM_CODE = '202710'

function App() {
  const [query, setQuery] = useState('')
  const [termCode, setTermCode] = useState('all')
  const [terms, setTerms] = useState([])
  const [subjects, setSubjects] = useState([])
  const [results, setResults] = useState({})
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState(null)
  const [expandedCourses, setExpandedCourses] = useState(new Set())
  const [syllabusLookup, setSyllabusLookup] = useState({})
  const [evaluationLookup, setEvaluationLookup] = useState({})
  const [collapsedEvals, setCollapsedEvals] = useState(new Set())
  const [weightRecency, setWeightRecency] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [currentOffset, setCurrentOffset] = useState(0)
  const [lastQuery, setLastQuery] = useState('')
  const [lastTermCode, setLastTermCode] = useState('')
  const [activeSyllabusKey, setActiveSyllabusKey] = useState(null)

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

    const fetchSubjects = async () => {
      try {
        const res = await fetch('/api/subjects')
        if (!res.ok) throw new Error(`Failed to fetch subjects: ${res.status}`)
        const data = await res.json()
        setSubjects(Array.isArray(data) ? data : [])
      } catch (err) {
        console.error('Error fetching subjects:', err)
      }
    }

    fetchTerms()
    fetchSubjects()
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
      setHasMore(false)
      setCurrentOffset(0)
      return
    }

    setLoading(true)
    setError(null)

    try {
      const searchParams = new URLSearchParams({ q: query.trim(), offset: '0', top_n_results: '50' })
      if (termCode.trim()) {
        searchParams.set('term_code', termCode.trim())
      }
      if (termCode === 'all' && weightRecency) {
        searchParams.set('weight_recency', 'true')
      }

      const res = await fetch(`/api/courses/?${searchParams.toString()}`)
      if (!res.ok) throw new Error(`Search failed ${res.status}`)
      const json = await res.json()

      const normalized = normalizeResults(json)
      setResults(normalized)
      setExpandedCourses(new Set())
      setSyllabusLookup({})

      // Check if we got exactly 50 results (meaning there might be more)
      const totalCourses = Object.values(normalized).flat().length
      setHasMore(totalCourses === 50)
      setCurrentOffset(50)
      setLastQuery(query.trim())
      setLastTermCode(termCode.trim())
    } catch (err) {
      setError(err.message ?? 'Unable to fetch')
    } finally {
      setLoading(false)
    }
  }

  const loadMore = async () => {
    if (!lastQuery) return

    setLoadingMore(true)
    setError(null)

    try {
      const searchParams = new URLSearchParams({ q: lastQuery, offset: currentOffset.toString(), top_n_results: '50' })
      if (lastTermCode) {
        searchParams.set('term_code', lastTermCode)
      }
      if (lastTermCode === 'all' && weightRecency) {
        searchParams.set('weight_recency', 'true')
      }

      const res = await fetch(`/api/courses/?${searchParams.toString()}`)
      if (!res.ok) throw new Error(`Search failed ${res.status}`)
      const json = await res.json()

      const newResults = normalizeResults(json)

      // Merge results instead of replacing them
      const mergedResults = { ...results }
      for (const [courseCode, courseInstances] of Object.entries(newResults)) {
        if (mergedResults[courseCode]) {
          mergedResults[courseCode] = [...mergedResults[courseCode], ...courseInstances]
        } else {
          mergedResults[courseCode] = courseInstances
        }
      }

      setResults(mergedResults)

      // Check if we got exactly 50 results (meaning there might be more)
      const totalNewCourses = Object.values(newResults).flat().length
      setHasMore(totalNewCourses === 50)
      setCurrentOffset(currentOffset + 50)
    } catch (err) {
      setError(err.message ?? 'Unable to fetch more')
    } finally {
      setLoadingMore(false)
    }
  }

  const onEnter = (e) => {
    if (e.key === 'Enter') {
      doSearch()
    }
  }

  const handleSubjectClick = (subjectCode) => {
    setQuery(subjectCode)
  }

  const handleQueryChange = (e) => {
    const value = e.target.value.replace(/[^a-zA-Z0-9\- ]/g, '')
    setQuery(value)
  }

  useEffect(() => {
    const timerId = setTimeout(() => {
      doSearch()
    }, 50)

    return () => clearTimeout(timerId)
  }, [query, termCode, weightRecency])

  // Cleanup blob URLs when component unmounts or syllabus lookup changes
  useEffect(() => {
    return () => {
      Object.values(syllabusLookup).forEach((entry) => {
        if (entry?.blobUrl && entry?.url) {
          URL.revokeObjectURL(entry.url)
        }
      })
    }
  }, [])

  const toggleExpanded = (courseCode) => {
    const newExpanded = new Set(expandedCourses)
    if (newExpanded.has(courseCode)) {
      newExpanded.delete(courseCode)
    } else {
      newExpanded.add(courseCode)
    }
    setExpandedCourses(newExpanded)
  }

  const toggleEvalCollapsed = (evalKey) => {
    const newCollapsed = new Set(collapsedEvals)
    if (newCollapsed.has(evalKey)) {
      newCollapsed.delete(evalKey)
    } else {
      newCollapsed.add(evalKey)
    }
    setCollapsedEvals(newCollapsed)
  }

  // Format meeting times: split by common delimiters and return array
  const formatMeetingTimes = (timesStr) => {
    if (!timesStr || timesStr === 'TBA' || timesStr === '[]') return ['TBA']

    const trimmed = timesStr.trim()

    // Parse JSON-like arrays, e.g. ["MWF 10:00 AM - 11:00 AM", "TR 2:00 PM - 3:00 PM"]
    if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
      try {
        const parsed = JSON.parse(trimmed)
        if (Array.isArray(parsed)) {
          const times = parsed.map((time) => String(time).trim()).filter(Boolean)
          return times.length > 0 ? times : ['TBA']
        }
      } catch {
        // Fall through to delimiter-based parsing below.
      }
    }

    // If it looks like comma or semicolon separated values, split and rejoin
    const timesList = timesStr
      .split(/,|;\s*/)
      .map(s => s.trim())
      .filter(s => s.length > 0)

    return timesList.length > 0 ? timesList : ['TBA']
  }
  const formatInstructors = (instructorStr) => {
    if (!instructorStr || instructorStr === 'TBA' || instructorStr === '[]') return ['TBA']

    const trimmed = instructorStr.trim()

    // Parse JSON-like arrays from the scraper, e.g. ["A", "B"]
    if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
      try {
        const parsed = JSON.parse(trimmed)
        if (Array.isArray(parsed)) {
          const names = parsed.map((name) => String(name).trim()).filter(Boolean)
          return names.length > 0 ? names : ['TBA']
        }
      } catch {
        // Fall through to delimiter-based parsing below.
      }
    }

    const instructors = instructorStr
      .split(/,|;\s*/)
      .map(s => s.trim())
      .filter(s => s.length > 0)

    return instructors.length > 0 ? instructors : ['TBA']
  }

  const getEvaluationKey = (course) => `${course.term}-${course.crn}`

  const fetchEvaluation = async (course) => {
    const key = getEvaluationKey(course)

    setEvaluationLookup((prev) => ({
      ...prev,
      [key]: { status: 'loading', message: 'Loading evaluation...' },
    }))

    try {
      const subject = course.crs ? course.crs.split(' ')[0] : ''
      const params = new URLSearchParams({ term: course.term, crn: course.crn, subject })
      const res = await fetch(`/api/evaluate?${params.toString()}`)
      if (!res.ok) {
        throw new Error(`Evaluation lookup failed ${res.status}`)
      }

      const data = await res.json()
      if (data.success && data.html) {
        setEvaluationLookup((prev) => ({
          ...prev,
          [key]: {
            status: 'available',
            html: data.html,
            charts: data.charts || [],
            message: 'Evaluation loaded',
          },
        }))
      } else {
        setEvaluationLookup((prev) => ({
          ...prev,
          [key]: {
            status: 'none',
            message: data.message || 'No evaluation data found',
          },
        }))
      }
    } catch (err) {
      setEvaluationLookup((prev) => ({
        ...prev,
        [key]: {
          status: 'error',
          message: err.message || 'Unable to fetch evaluation',
        },
      }))
    }
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
        // Fetch the PDF as a blob and create a data URL
        const pdfRes = await fetch(data.syllabus_url)
        if (!pdfRes.ok) {
          throw new Error(`Failed to fetch PDF: ${pdfRes.status}`)
        }
        const pdfBlob = await pdfRes.blob()

        // Debug logging
        console.log(`[SYLLABUS] Fetched PDF - Size: ${pdfBlob.size} bytes, Type: ${pdfBlob.type}`)

        // Check if we got a valid PDF
        if (pdfBlob.type !== 'application/pdf' && !pdfBlob.type.includes('pdf')) {
          console.warn(`[SYLLABUS] Warning: Unexpected blob type: ${pdfBlob.type}, size: ${pdfBlob.size} bytes`)
        }

        const pdfUrl = URL.createObjectURL(pdfBlob)
        console.log(`[SYLLABUS] Created blob URL: ${pdfUrl}`)

        setSyllabusLookup((prev) => ({
          ...prev,
          [key]: {
            status: 'available',
            message: data.message || 'Syllabus available',
            url: pdfUrl,
            blobUrl: true, // Mark this as a blob URL for cleanup
          },
        }))
        // Automatically open the PDF viewer
        setActiveSyllabusKey(key)
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
                    Type CRN (12345), CRS (ABCD 123), course title (Intro to Life I), instructor (John Doe), or any combination.
                  </div>
                </div>
                <div className="tooltip-wrap">
                  <button
                    type="button"
                    className="tooltip-trigger subjects-info-btn"
                    aria-label="Subject codes reference"
                    aria-describedby="subjects-tooltip"
                  >
                    ⊕
                  </button>
                  <div id="subjects-tooltip" role="tooltip" className="tooltip-text subjects-tooltip">
                    <strong>Subject Codes:</strong>
                    <div className="subject-codes-list">
                      {subjects.map((subject) => (
                        <div
                          key={subject.code}
                          className="subject-code-item"
                          onClick={() => {
                            handleSubjectClick(subject.code)
                          }}
                        >
                          <span className="code">{subject.code}</span>
                          <span className="meaning">{subject.subject}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
              <input
                id="query"
                value={query}
                onChange={handleQueryChange}
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

            {termCode === 'all' && (
              <div className="checkbox-group">
                <label htmlFor="weight-recency" className="checkbox-label">
                  <input
                    id="weight-recency"
                    type="checkbox"
                    checked={weightRecency}
                    onChange={(e) => setWeightRecency(e.target.checked)}
                  />
                  <span>Weight by Recency</span>
                </label>
              </div>
            )}
          </div>

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
                            {course.credits && <span className="credits">{course.credits} {parseInt(course.credits) === 1 && !course.credits.includes(' ') ? 'CREDIT' : 'CREDITS'}</span>}
                          </div>

                          <div className="course-details">
                            {course.instructors && (
                              <div className="detail-row">
                                <strong>Instructors:</strong>
                                <div className="detail-items">
                                  {formatInstructors(course.instructors).map((instructor, idx) => (
                                    <div key={idx} className="detail-item">{instructor}</div>
                                  ))}
                                </div>
                              </div>
                            )}
                            {course.meeting_times && (
                              <div className="detail-row">
                                <strong>Times:</strong>
                                <div className="detail-items">
                                  {formatMeetingTimes(course.meeting_times).map((time, idx) => (
                                    <div key={idx} className="detail-item">{time}</div>
                                  ))}
                                </div>
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
                              Course Page
                            </a>
                            <button
                              type="button"
                              className="syllabus-btn"
                              onClick={() => fetchSyllabus(course)}
                              disabled={syllabusState?.status === 'loading'}
                            >
                              {syllabusState?.status === 'loading' ? 'Checking...' : 'Get syllabus'}
                            </button>
                            <button
                              type="button"
                              className="evaluation-btn"
                              onClick={() => fetchEvaluation(course)}
                              disabled={evaluationLookup[getEvaluationKey(course)]?.status === 'loading'}
                            >
                              {evaluationLookup[getEvaluationKey(course)]?.status === 'loading' ? 'Loading...' : 'Get Evaluation'}
                            </button>
                          </div>

                          {syllabusState?.status === 'available' && syllabusState.url && (
                            <>
                              <button
                                type="button"
                                className="toggle-syllabus-btn"
                                onClick={() => setActiveSyllabusKey(activeSyllabusKey === getSyllabusKey(course) ? null : getSyllabusKey(course))}
                              >
                                {activeSyllabusKey === getSyllabusKey(course) ? '▾ Hide Syllabus PDF' : '▸ View Syllabus PDF'}
                              </button>
                              {activeSyllabusKey === getSyllabusKey(course) && (
                                <div className="syllabus-viewer">
                                  <iframe
                                    src={syllabusState.url}
                                    type="application/pdf"
                                    className="syllabus-iframe"
                                    title="Syllabus PDF"
                                  />
                                </div>
                              )}
                            </>
                          )}

                          {syllabusState?.status === 'none' && (
                            <p className="syllabus-status neutral">{syllabusState.message}</p>
                          )}

                          {syllabusState?.status === 'error' && (
                            <p className="syllabus-status error">{syllabusState.message}</p>
                          )}

                          {evaluationLookup[getEvaluationKey(course)]?.status === 'available' && (
                            <>
                              <button
                                type="button"
                                className="collapse-eval-btn"
                                onClick={() => toggleEvalCollapsed(getEvaluationKey(course))}
                              >
                                {collapsedEvals.has(getEvaluationKey(course)) ? '▸ Show Evaluation' : '▾ Hide Evaluation'}
                              </button>
                              {!collapsedEvals.has(getEvaluationKey(course)) && (
                                <div className="evaluation-results">
                                  {evaluationLookup[getEvaluationKey(course)]?.charts && evaluationLookup[getEvaluationKey(course)].charts.length > 0 && (
                                    <div className="charts-section">
                                      <div className="charts-title">Survey Results</div>
                                      <div className="charts-grid">
                                        {evaluationLookup[getEvaluationKey(course)].charts.map((chart, idx) => {
                                          // Prepare data for recharts - values are now actual counts
                                          const chartData = chart.labels.map((label, i) => ({
                                            name: label,
                                            count: chart.values[i],
                                          }))

                                          // Use a color palette for bars
                                          const colors = ['#667CC7', '#7B8FD7', '#90A3E7', '#A5B7F7', '#BAC5FF']

                                          return (
                                            <div key={idx} className="chart-container">
                                              <div className="chart-title">{chart.title}</div>
                                              <div className="chart-meta">
                                                <span>Total Responses: {chart.total}</span>
                                              </div>
                                              <div className="chart-wrapper">
                                                <ResponsiveContainer width="100%" height={200}>
                                                  <BarChart data={chartData} margin={{ top: 10, right: 30, left: 0, bottom: 50 }}>
                                                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                                                    <XAxis
                                                      dataKey="name"
                                                      angle={-45}
                                                      textAnchor="end"
                                                      height={80}
                                                      interval={0}
                                                      tick={{ fill: '#E8E8E8', fontSize: 12, fontWeight: 500 }}
                                                    />
                                                    <YAxis tick={{ fill: '#E8E8E8' }} />
                                                    <Tooltip
                                                      contentStyle={{
                                                        backgroundColor: 'rgba(0, 26, 71, 0.95)',
                                                        border: '1px solid rgba(168, 85, 247, 0.3)',
                                                        borderRadius: '4px',
                                                        color: '#E8E8E8',
                                                      }}
                                                      labelStyle={{ color: '#E8E8E8' }}
                                                      formatter={(value) => [value, 'Count']}
                                                      wrapperStyle={{ color: '#E8E8E8' }}
                                                    />
                                                    <Bar dataKey="count" radius={[8, 8, 0, 0]}>
                                                      {chartData.map((_, i) => (
                                                        <Cell key={`cell-${i}`} fill={colors[i % colors.length]} />
                                                      ))}
                                                    </Bar>
                                                  </BarChart>
                                                </ResponsiveContainer>
                                              </div>
                                            </div>
                                          )
                                        })}
                                      </div>
                                    </div>
                                  )}
                                  <div className="comments-section">
                                    <div
                                      dangerouslySetInnerHTML={{
                                        __html: evaluationLookup[getEvaluationKey(course)]?.html
                                          .replace(/<div class="charts">[\s\S]*?<div class="comments">/g, '<div class="comments">')
                                          .replace(/<div class="chart">[\s\S]*?<\/div>\s*<\/div>/g, '')
                                          .replace(/<img[^>]*>/g, ''),
                                      }}
                                    />
                                  </div>
                                </div>
                              )}
                            </>
                          )}

                          {evaluationLookup[getEvaluationKey(course)]?.status === 'none' && (
                            <p className="evaluation-status neutral">{evaluationLookup[getEvaluationKey(course)]?.message}</p>
                          )}

                          {evaluationLookup[getEvaluationKey(course)]?.status === 'error' && (
                            <p className="evaluation-status error">{evaluationLookup[getEvaluationKey(course)]?.message}</p>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
              )
            })}
          </div>

          {hasMore && !loading && (
            <div className="load-more-container">
              <button
                className="load-more-btn"
                onClick={loadMore}
                disabled={loadingMore}
              >
                {loadingMore ? 'Loading more...' : 'Load More Results'}
              </button>
            </div>
          )}
        </section>
      </div>

      <footer className="app-footer">
        <p>Built by Rishabh Rengarajan, Rice '29</p>
      </footer>
    </div>
  )
}

export default App