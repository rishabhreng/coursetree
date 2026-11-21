import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import { Analytics } from '@vercel/analytics/react'
import { SpeedInsights } from '@vercel/speed-insights/react'
import TermSelect from './TermSelect.jsx'
import CourseGroup from './CourseGroup.jsx'
import { getInstructorNames, prereqCodes, sectionKey } from './format.js'

const DEFAULT_TERM_CODE = '202710'
// In dev, requests go to /api on the page's own origin and Vite proxies them to
// the local API (see vite.config.js). That avoids CORS and works when the dev
// server is reached via 127.0.0.1, another port, or remote port forwarding.
const API_BASE_URL = import.meta.env.DEV
  ? ''
  : (import.meta.env.VITE_API_URL || 'https://api-ricecourses.duckdns.org')

const DISTRIBUTION_CHOICES = [
  { code: 'all', short: null, label: 'All Groups' },
  { code: 'Distribution Group I', short: 'I', label: 'Group I' },
  { code: 'Distribution Group II', short: 'II', label: 'Group II' },
  { code: 'Distribution Group III', short: 'III', label: 'Group III' },
]

const sanitizeQuery = (value) => value.replace(/[^a-zA-Z0-9\- ]/g, '')

const hasActiveSearch = ({ query, distributionGroup, analyzingDiversity }) =>
  Boolean(query.trim()) || distributionGroup !== 'all' || analyzingDiversity

// ---- URL state: ?q=&from=&to=&dist=&ad=1 ----

const readUrlState = () => {
  const params = new URLSearchParams(window.location.search)
  const termStart = params.get('from') || 'all'
  const dist = DISTRIBUTION_CHOICES.find((c) => c.short && c.short === params.get('dist'))
  return {
    // Not sanitized: instructor-name searches ("Last, First M.") contain punctuation.
    query: params.get('q') || '',
    termStart,
    termEnd: termStart === 'all' ? 'all' : (params.get('to') || termStart),
    distributionGroup: dist ? dist.code : 'all',
    analyzingDiversity: params.get('ad') === '1',
  }
}

const buildQueryString = ({ query, termStart, termEnd, distributionGroup, analyzingDiversity }) => {
  const params = new URLSearchParams()
  if (query.trim()) params.set('q', query.trim())
  if (termStart !== 'all') {
    params.set('from', termStart)
    if (termEnd !== 'all' && termEnd !== termStart) params.set('to', termEnd)
  }
  const dist = DISTRIBUTION_CHOICES.find((c) => c.code === distributionGroup)
  if (dist?.short) params.set('dist', dist.short)
  if (analyzingDiversity) params.set('ad', '1')
  return params.toString()
}

const urlFor = (state) => {
  const qs = buildQueryString(state)
  return `${window.location.pathname}${qs ? `?${qs}` : ''}`
}

const normalizeResults = (payload) => {
  if (Array.isArray(payload)) {
    return payload.reduce((acc, course) => {
      const key = course.crs || `${course.term}-${course.crn}`
      if (!acc[key]) acc[key] = []
      acc[key].push(course)
      return acc
    }, {})
  }
  if (payload && typeof payload === 'object') return payload
  return {}
}

function App() {
  const [initialUrlState] = useState(readUrlState)
  const [query, setQuery] = useState(initialUrlState.query)
  const [termStart, setTermStart] = useState(initialUrlState.termStart)
  const [termEnd, setTermEnd] = useState(initialUrlState.termEnd)
  const [distributionGroup, setDistributionGroup] = useState(initialUrlState.distributionGroup)
  const [analyzingDiversity, setAnalyzingDiversity] = useState(initialUrlState.analyzingDiversity)
  const [terms, setTerms] = useState([])
  const [results, setResults] = useState({})
  // Cached course relations: { leadsTo: { crs: [crs] }, titles: { crs: title } }
  const [relations, setRelations] = useState({ leadsTo: {}, titles: {} })
  const relationsRequestedRef = useRef(new Set())
  // Query string of the search whose results are currently displayed.
  const [resultsKey, setResultsKey] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [expandedCourses, setExpandedCourses] = useState(new Set())
  // sectionKey -> 'syllabus' | 'eval' | 'instructor'
  const [openPanels, setOpenPanels] = useState({})
  const [syllabusLookup, setSyllabusLookup] = useState({})
  const [evaluationLookup, setEvaluationLookup] = useState({})
  const [instructorEvalLookup, setInstructorEvalLookup] = useState({})
  const [collapsedInstructorSections, setCollapsedInstructorSections] = useState(new Set())
  const [previousSearch, setPreviousSearch] = useState(null)
  const searchInputRef = useRef(null)
  const syllabusLookupRef = useRef({})
  const activeSearchControllerRef = useRef(null)
  const searchIdRef = useRef(0)

  const searchState = { query, termStart, termEnd, distributionGroup, analyzingDiversity }
  const currentKey = buildQueryString(searchState)
  const isSearchActive = hasActiveSearch(searchState)

  const apiFetch = useCallback((path, options = {}) => fetch(`${API_BASE_URL}${path}`, options), [])

  useEffect(() => {
    const handleShortcut = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        searchInputRef.current?.focus()
        searchInputRef.current?.select?.()
      }
    }
    window.addEventListener('keydown', handleShortcut)
    return () => window.removeEventListener('keydown', handleShortcut)
  }, [])

  // Restore search state when the user navigates with back/forward.
  useEffect(() => {
    const handlePopState = () => {
      const state = readUrlState()
      setQuery(state.query)
      setTermStart(state.termStart)
      setTermEnd(state.termEnd)
      setDistributionGroup(state.distributionGroup)
      setAnalyzingDiversity(state.analyzingDiversity)
      setPreviousSearch(null)
    }
    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  useEffect(() => {
    const fetchTerms = async () => {
      try {
        const res = await apiFetch('/api/terms')
        if (!res.ok) throw new Error(`Failed to fetch terms: ${res.status}`)
        const data = await res.json()
        setTerms(Array.isArray(data) ? data : [])
      } catch (err) {
        console.error('Error fetching terms:', err)
      }
    }
    fetchTerms()
  }, [apiFetch])

  const termOptions = useMemo(() => {
    const list = Array.isArray(terms) ? [...terms] : []
    if (!list.some((term) => term.code === DEFAULT_TERM_CODE)) {
      list.push({ code: DEFAULT_TERM_CODE, term: 'Current Term' })
    }
    return list
      .filter((term) => term?.code)
      .sort((a, b) => Number(b.code) - Number(a.code))
  }, [terms])

  const getTermLabel = useCallback((code) => {
    if (code === 'all') return 'All terms'
    const foundTerm = termOptions.find((term) => term.code === String(code))
    if (foundTerm) return foundTerm.term
    if (String(code) === DEFAULT_TERM_CODE) return 'Current Term'
    return code
  }, [termOptions])

  const latestTermCode = termOptions.length ? termOptions[0].code : DEFAULT_TERM_CODE
  const startIndex = termOptions.findIndex((term) => term.code === termStart)
  const endOptions = startIndex >= 0 ? termOptions.slice(0, startIndex + 1) : termOptions
  const showTermEnd = termStart !== 'all' && termStart !== latestTermCode

  const clearPreviousSearch = () => {
    if (previousSearch) setPreviousSearch(null)
  }

  const handleTermStartChange = (nextCode) => {
    clearPreviousSearch()
    const wasAll = termStart === 'all'
    setTermStart(nextCode)
    if (wasAll && nextCode !== 'all') {
      setTermEnd(nextCode)
    }
  }

  const handleTermEndChange = (nextCode) => {
    clearPreviousSearch()
    setTermEnd(nextCode)
  }

  // Keep the term range valid. Waits for the real term list so a range
  // restored from the URL isn't reset before terms have loaded.
  useEffect(() => {
    if (!terms.length) return

    if (termStart !== 'all' && startIndex === -1) {
      setTermStart('all')
      setTermEnd('all')
      return
    }

    if (termStart === 'all') {
      if (termEnd !== 'all') setTermEnd('all')
      return
    }

    if (termStart === latestTermCode) {
      if (termEnd !== termStart) setTermEnd(termStart)
      return
    }

    if (termEnd === 'all') {
      setTermEnd(latestTermCode)
      return
    }

    const endIndex = termOptions.findIndex((term) => term.code === termEnd)
    if (endIndex !== -1 && startIndex !== -1 && endIndex > startIndex) {
      setTermEnd(termStart)
    }
  }, [terms, termOptions, termStart, termEnd, latestTermCode, startIndex])

  const resetSyllabi = useCallback(() => {
    Object.values(syllabusLookupRef.current).forEach((entry) => {
      if (entry?.url) URL.revokeObjectURL(entry.url)
    })
    setSyllabusLookup({})
  }, [])

  const doSearch = useCallback(async () => {
    const state = { query, termStart, termEnd, distributionGroup, analyzingDiversity }
    const key = buildQueryString(state)
    const currentSearchId = ++searchIdRef.current
    activeSearchControllerRef.current?.abort()
    activeSearchControllerRef.current = null

    if (!hasActiveSearch(state)) {
      setResults({})
      setResultsKey(key)
      setLoading(false)
      setError(null)
      return
    }

    const controller = new AbortController()
    activeSearchControllerRef.current = controller
    setLoading(true)
    setError(null)

    try {
      const searchParams = new URLSearchParams({ q: query.trim(), term_code: 'all' })
      if (termStart !== 'all') searchParams.set('term_start', termStart)
      if (termEnd !== 'all') searchParams.set('term_end', termEnd)
      if (distributionGroup !== 'all') searchParams.set('distribution_group', distributionGroup)
      if (analyzingDiversity) searchParams.set('analyzing_diversity', 'true')

      const res = await apiFetch(`/api/courses/?${searchParams.toString()}`, { signal: controller.signal })
      if (!res.ok) throw new Error(`Search failed (${res.status})`)
      const json = await res.json()

      if (currentSearchId !== searchIdRef.current) return

      setResults(normalizeResults(json))
      setResultsKey(key)
      setExpandedCourses(new Set())
      setOpenPanels({})
      resetSyllabi()
    } catch (err) {
      if (err.name === 'AbortError') return
      if (currentSearchId === searchIdRef.current) {
        setError(err.message ?? 'Unable to fetch')
      }
    } finally {
      if (currentSearchId === searchIdRef.current) {
        setLoading(false)
      }
    }
  }, [apiFetch, query, termStart, termEnd, distributionGroup, analyzingDiversity, resetSyllabi])

  // Debounced search; also mirrors the search into the URL so it can be shared or refreshed.
  useEffect(() => {
    const timerId = setTimeout(() => {
      doSearch()
      const url = urlFor({ query, termStart, termEnd, distributionGroup, analyzingDiversity })
      if (url !== `${window.location.pathname}${window.location.search}`) {
        window.history.replaceState(null, '', url)
      }
    }, 250)
    return () => clearTimeout(timerId)
  }, [doSearch, query, termStart, termEnd, distributionGroup, analyzingDiversity])

  useEffect(() => {
    syllabusLookupRef.current = syllabusLookup
  }, [syllabusLookup])

  useEffect(() => () => {
    Object.values(syllabusLookupRef.current).forEach((entry) => {
      if (entry?.url) URL.revokeObjectURL(entry.url)
    })
  }, [])

  // Fetch "leads to" lists and titles for displayed courses and their prereqs.
  useEffect(() => {
    const codes = new Set()
    for (const [courseCode, instances] of Object.entries(results)) {
      codes.add(courseCode)
      instances.forEach((course) => prereqCodes(course.prerequisites).forEach((code) => codes.add(code)))
    }
    const missing = [...codes].filter((code) => !relationsRequestedRef.current.has(code))
    if (missing.length === 0) return
    missing.forEach((code) => relationsRequestedRef.current.add(code))

    const params = new URLSearchParams()
    missing.forEach((code) => params.append('crs', code))
    apiFetch(`/api/course-relations?${params.toString()}`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`Relations failed (${res.status})`))))
      .then((data) => setRelations((prev) => ({
        leadsTo: { ...prev.leadsTo, ...data.leads_to },
        titles: { ...prev.titles, ...data.titles },
      })))
      .catch((err) => {
        // Allow a retry on the next search.
        missing.forEach((code) => relationsRequestedRef.current.delete(code))
        console.error('Error fetching course relations:', err)
      })
  }, [results, apiFetch])

  // Runs a new search for an instructor or course code, keeping the current
  // search reachable via Back and the "previous search" button.
  const searchFor = (text) => {
    const trimmed = String(text || '').trim()
    if (!trimmed || trimmed.toUpperCase() === 'TBA') return

    const priorQuery = query.trim()
    if (priorQuery || termStart !== 'all' || termEnd !== 'all' || distributionGroup !== 'all' || analyzingDiversity) {
      setPreviousSearch({ query: priorQuery, termStart, termEnd, distributionGroup, analyzingDiversity })
    }

    // Record the current search as its own history entry so Back returns to it.
    window.history.replaceState(null, '', urlFor(searchState))
    window.history.pushState(null, '', urlFor({ ...searchState, query: trimmed }))

    setQuery(trimmed)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const handleRestorePreviousSearch = () => {
    if (!previousSearch) return
    setQuery(previousSearch.query || '')
    setTermStart(previousSearch.termStart || 'all')
    setTermEnd(previousSearch.termEnd || 'all')
    setDistributionGroup(previousSearch.distributionGroup || 'all')
    setAnalyzingDiversity(Boolean(previousSearch.analyzingDiversity))
    setPreviousSearch(null)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const handleQueryChange = (e) => {
    clearPreviousSearch()
    setQuery(sanitizeQuery(e.target.value))
  }

  const toggleExpanded = (courseCode) => {
    setExpandedCourses((prev) => {
      const next = new Set(prev)
      if (next.has(courseCode)) next.delete(courseCode)
      else next.add(courseCode)
      return next
    })
  }

  const toggleInstructorSectionCollapsed = (key) => {
    setCollapsedInstructorSections((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  // ---- Section data fetchers ----

  const fetchEvaluation = async (course) => {
    const key = sectionKey(course)
    setEvaluationLookup((prev) => ({ ...prev, [key]: { status: 'loading' } }))

    try {
      const subject = course.crs ? course.crs.split(' ')[0] : ''
      const params = new URLSearchParams({ term: course.term, crn: course.crn, subject, course_code: course.crs || '' })
      const res = await apiFetch(`/api/evaluate?${params.toString()}`)
      if (!res.ok) throw new Error(`Evaluation lookup failed (${res.status})`)

      const data = await res.json()
      setEvaluationLookup((prev) => ({
        ...prev,
        [key]: data.success && data.html
          ? { status: 'available', html: data.html, charts: data.charts || [] }
          : { status: 'none', message: data.message || 'No evaluation data found for this section.' },
      }))
    } catch (err) {
      setEvaluationLookup((prev) => ({
        ...prev,
        [key]: { status: 'error', message: err.message || 'Unable to fetch evaluation' },
      }))
    }
  }

  const fetchInstructorEvaluations = async (course) => {
    const key = sectionKey(course)

    if (getInstructorNames(course).length === 0) {
      setInstructorEvalLookup((prev) => ({
        ...prev,
        [key]: { status: 'none', message: 'No instructors are listed for this section.' },
      }))
      return
    }

    setInstructorEvalLookup((prev) => ({ ...prev, [key]: { status: 'loading' } }))

    try {
      const params = new URLSearchParams({ term: course.term, crn: course.crn })
      const res = await apiFetch(`/api/instructor-evaluate?${params.toString()}`)
      if (!res.ok) throw new Error(`Instructor evaluation lookup failed (${res.status})`)

      const data = await res.json()
      setInstructorEvalLookup((prev) => ({
        ...prev,
        [key]: data.success && Array.isArray(data.results)
          ? { status: 'available', results: data.results, missing_instructors: data.missing_instructors || [] }
          : { status: 'none', message: data.message || 'No instructor evaluation data found.' },
      }))
    } catch (err) {
      setInstructorEvalLookup((prev) => ({
        ...prev,
        [key]: { status: 'error', message: err.message || 'Unable to fetch instructor evaluations' },
      }))
    }
  }

  const fetchSyllabus = async (course) => {
    const key = sectionKey(course)
    setSyllabusLookup((prev) => ({ ...prev, [key]: { status: 'loading' } }))

    const readDetail = async (res, fallback) => {
      try {
        const data = await res.json()
        return data.detail || fallback
      } catch {
        return fallback
      }
    }

    try {
      const params = new URLSearchParams({ term: course.term, crn: course.crn })
      const res = await apiFetch(`/api/syllabus?${params.toString()}`)

      if (res.status === 404) {
        const message = await readDetail(res, 'No syllabus posted for this section.')
        setSyllabusLookup((prev) => ({ ...prev, [key]: { status: 'none', message } }))
        return
      }
      if (!res.ok) {
        throw new Error(await readDetail(res, `Syllabus lookup failed (${res.status})`))
      }

      const contentType = (res.headers.get('content-type') || '').toLowerCase()
      const disposition = res.headers.get('content-disposition') || ''
      const filenameMatch = disposition.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
      const filename = filenameMatch ? decodeURIComponent(filenameMatch[1]) : null

      let fileType = 'pdf'
      if (filename) {
        fileType = filename.split('.').pop().toLowerCase() || 'pdf'
      } else if (contentType.includes('text/plain')) {
        fileType = 'txt'
      } else if (contentType.includes('wordprocessingml') || contentType.includes('docx')) {
        fileType = 'docx'
      } else if (contentType.includes('msword')) {
        fileType = 'doc'
      }

      if (fileType === 'txt' || contentType.includes('text/plain')) {
        const textContent = await res.text()
        setSyllabusLookup((prev) => ({
          ...prev,
          [key]: { status: 'available', textContent, fileType: 'txt', filename },
        }))
        return
      }

      const objectUrl = URL.createObjectURL(await res.blob())
      setSyllabusLookup((prev) => {
        if (prev[key]?.url) URL.revokeObjectURL(prev[key].url)
        return { ...prev, [key]: { status: 'available', url: objectUrl, fileType, filename } }
      })
    } catch (err) {
      setSyllabusLookup((prev) => ({
        ...prev,
        [key]: { status: 'error', message: err.message || 'Unable to fetch syllabus' },
      }))
    }
  }

  const lookups = { syllabus: syllabusLookup, eval: evaluationLookup, instructor: instructorEvalLookup }
  const fetchers = { syllabus: fetchSyllabus, eval: fetchEvaluation, instructor: fetchInstructorEvaluations }

  // Opens one panel per section; fetches its data the first time (or after an error).
  const togglePanel = (course, type) => {
    const key = sectionKey(course)
    const isOpen = openPanels[key] === type
    setOpenPanels((prev) => ({ ...prev, [key]: isOpen ? null : type }))
    if (isOpen) return

    const state = lookups[type][key]
    if (!state || state.status === 'error') fetchers[type](course)
  }

  const retryPanel = (course, type) => fetchers[type](course)

  // Preserve the backend's relevance ordering (don't re-sort alphabetically)
  const courseEntries = Object.entries(results)
  const isPending = isSearchActive && (loading || resultsKey !== currentKey)
  const showNoResults = isSearchActive && !isPending && !error && courseEntries.length === 0

  return (
    <div className="app">
      <header className="header">
        <div className="header-copy">
          <h1>Rice Course Viewer</h1>
          <p className="tagline">Search for Rice courses across the years</p>
        </div>
      </header>

      <main className="container">
        <section className="search-section" aria-label="Search">
          {previousSearch && (
            <div className="previous-search-row">
              <button type="button" className="previous-search-btn" onClick={handleRestorePreviousSearch}>
                <span className="previous-search-icon" aria-hidden="true">↩</span>
                <span>Back to previous search{previousSearch.query ? `: "${previousSearch.query}"` : ''}</span>
              </button>
            </div>
          )}
          <div className="search-inputs">
            <div className="input-group">
              <label htmlFor="query">Course Search</label>
              <input
                id="query"
                type="search"
                ref={searchInputRef}
                value={query}
                onChange={handleQueryChange}
                onKeyDown={(e) => { if (e.key === 'Enter') doSearch() }}
                placeholder="CRN/code/title/instructor (Ctrl+K)"
                autoComplete="off"
              />
            </div>

            <div className="input-group">
              <label htmlFor="term-start">Term Range</label>
              <div className={`term-range ${showTermEnd ? '' : 'term-range-single'}`}>
                <div className="term-range-field">
                  {showTermEnd && <span className="term-range-label" aria-hidden="true">From</span>}
                  <TermSelect
                    id="term-start"
                    value={termStart}
                    onChange={handleTermStartChange}
                    options={[{ code: 'all', term: 'All terms' }, ...termOptions]}
                    placeholder="Type or select term"
                  />
                </div>
                {showTermEnd && (
                  <div className="term-range-field">
                    <label className="term-range-label" htmlFor="term-end">To</label>
                    <TermSelect
                      id="term-end"
                      value={termEnd}
                      onChange={handleTermEndChange}
                      options={endOptions}
                      placeholder="Type or select term"
                    />
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="distribution-filters-container">
            <div className="distribution-filter-row">
              <div className="distribution-group-select-wrap">
                <span className="distribution-filters-label" id="dist-label">Distribution Group</span>
                <div className="distribution-chips-group" role="group" aria-labelledby="dist-label">
                  {DISTRIBUTION_CHOICES.map((opt) => (
                    <button
                      key={opt.code}
                      type="button"
                      aria-pressed={distributionGroup === opt.code}
                      className={`distribution-chip-btn ${distributionGroup === opt.code ? 'active' : ''}`}
                      onClick={() => {
                        clearPreviousSearch()
                        setDistributionGroup(opt.code)
                      }}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="distribution-ad-wrap">
                <span className="distribution-filters-label">Diversity Requirement</span>
                <label className={`distribution-checkbox-chip ${analyzingDiversity ? 'active' : ''}`}>
                  <input
                    type="checkbox"
                    checked={analyzingDiversity}
                    onChange={(e) => {
                      clearPreviousSearch()
                      setAnalyzingDiversity(e.target.checked)
                    }}
                  />
                  <span className="checkbox-custom" aria-hidden="true"></span>
                  <span className="checkbox-label">Analyzing Diversity Course</span>
                </label>
              </div>
            </div>
          </div>
        </section>

        <section className={`results-section ${isPending ? 'is-pending' : ''}`} aria-busy={isPending}>
          {isSearchActive && (
            <div className="results-meta" aria-live="polite">
              {isPending ? (
                <span className="searching"><span className="spinner" aria-hidden="true" />Searching…</span>
              ) : !error && courseEntries.length > 0 ? (
                <span>{courseEntries.length} {courseEntries.length === 1 ? 'course' : 'courses'}</span>
              ) : null}
            </div>
          )}

          {error && <p className="status error">{error}</p>}
          {showNoResults && <p className="status empty">No courses match this search.</p>}

          {isSearchActive && (
            <div className="courses-grid">
              {courseEntries.map(([courseCode, courseInstances]) => (
                <CourseGroup
                  key={courseCode}
                  courseCode={courseCode}
                  instances={courseInstances}
                  isExpanded={expandedCourses.has(courseCode)}
                  onToggleExpanded={toggleExpanded}
                  getTermLabel={getTermLabel}
                  openPanels={openPanels}
                  lookups={lookups}
                  onTogglePanel={togglePanel}
                  onRetryPanel={retryPanel}
                  onInstructorClick={searchFor}
                  onCourseClick={searchFor}
                  relations={relations}
                  collapsedInstructorSections={collapsedInstructorSections}
                  onToggleInstructorSection={toggleInstructorSectionCollapsed}
                />
              ))}
            </div>
          )}
        </section>
      </main>
    </div>
  )
}

function MainContainer() {
  return (
    <>
      <App />
      <Analytics />
      <SpeedInsights />
    </>
  )
}

export default MainContainer
