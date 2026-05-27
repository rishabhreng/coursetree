import { useEffect, useMemo, useRef, useState } from 'react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, Cell } from 'recharts'
import './App.css'
import { Analytics } from '@vercel/analytics/react';
import { SpeedInsights } from '@vercel/speed-insights/react';

const DEFAULT_TERM_CODE = '202710'
const PAGE_SIZE = 10
const API_BASE_URL = import.meta.env.VITE_API_URL
  || (typeof process !== 'undefined' ? process.env.REACT_APP_API_URL : undefined)
  || (import.meta.env.DEV ? 'http://localhost:8000' : 'https://api-ricecourses.duckdns.org')
const ESTHER_AUTH_REQUIRED_MESSAGE = 'ESTHER login required. Use the Login to ESTHER button.'
const ESTHER_CLIENT_ID_KEY = 'coursetree-esther-client-id'

const getOrCreateClientId = () => {
  if (typeof window === 'undefined') {
    return 'server'
  }

  const existing = window.localStorage.getItem(ESTHER_CLIENT_ID_KEY)
  if (existing) {
    return existing
  }

  const generated = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `client-${Date.now()}-${Math.random().toString(36).slice(2)}`

  window.localStorage.setItem(ESTHER_CLIENT_ID_KEY, generated)
  return generated
}

function App() {
  const [clientId] = useState(() => getOrCreateClientId())
  const [query, setQuery] = useState('')
  const [termStart, setTermStart] = useState('all')
  const [termEnd, setTermEnd] = useState('all')
  const [terms, setTerms] = useState([])
  const [subjects, setSubjects] = useState([])
  const [results, setResults] = useState({})
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState(null)
  const [expandedCourses, setExpandedCourses] = useState(new Set())
  const [syllabusLookup, setSyllabusLookup] = useState({})
  const [evaluationLookup, setEvaluationLookup] = useState({})
  const [instructorEvalLookup, setInstructorEvalLookup] = useState({})
  const [collapsedEvals, setCollapsedEvals] = useState(new Set())
  const [collapsedInstructorEvals, setCollapsedInstructorEvals] = useState(new Set())
  const [collapsedInstructorSections, setCollapsedInstructorSections] = useState(new Set())
  const [hasMore, setHasMore] = useState(false)
  const [currentOffset, setCurrentOffset] = useState(0)
  const [lastQuery, setLastQuery] = useState('')
  const [lastTermStart, setLastTermStart] = useState('all')
  const [lastTermEnd, setLastTermEnd] = useState('all')
  const [previousSearch, setPreviousSearch] = useState(null)
  const [activeSyllabusKey, setActiveSyllabusKey] = useState(null)
  const [estherAuthState, setEstherAuthState] = useState('checking')
  const [showAuthModal, setShowAuthModal] = useState(false)
  const [netid, setNetid] = useState('')
  const [password, setPassword] = useState('')
  const [authLoading, setAuthLoading] = useState(false)
  const [authError, setAuthError] = useState(null)
  const syllabusLookupRef = useRef({})

  const apiFetch = (path, options = {}) => {
    const headers = new Headers(options.headers || {})
    headers.set('X-Client-Id', clientId)
    return fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers,
    })
  }

  useEffect(() => {
    const fetchAuthStatus = async () => {
      try {
        const res = await apiFetch('/api/auth/status')
        if (!res.ok) {
          throw new Error(`Auth status failed ${res.status}`)
        }

        const data = await res.json()
        setEstherAuthState(data.authenticated ? 'authenticated' : 'unauthenticated')
      } catch (err) {
        console.error('Error checking ESTHER auth status:', err)
        setEstherAuthState('unauthenticated')
      }
    }

    fetchAuthStatus()
  }, [clientId])

  const handleAuthSubmit = async (e) => {
    e.preventDefault();
    setAuthLoading(true);
    setAuthError(null);

    try {
      const res = await apiFetch('/api/auth', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ netid, password })
      });

      if (!res.ok) {
        const errorBody = await res.json().catch(() => null)
        throw new Error(errorBody?.detail || 'Login failed or Duo push was denied.')
      }

      setEstherAuthState('authenticated')
      setShowAuthModal(false);
      setPassword('')

    } catch (err) {
      setAuthError(err.message);
    } finally {
      setAuthLoading(false);
    }
  };

  useEffect(() => {
    const fetchTerms = async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/api/terms`)
        if (!res.ok) throw new Error(`Failed to fetch terms: ${res.status}`)
        const data = await res.json()
        setTerms(Array.isArray(data) ? data : [])
      } catch (err) {
        console.error('Error fetching terms:', err)
      }
    }

    const fetchSubjects = async () => {
      try {
        const res = await apiFetch('/api/subjects')
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

  const termOptions = useMemo(() => {
    const list = Array.isArray(terms) ? [...terms] : []
    if (!list.some((term) => term.code === DEFAULT_TERM_CODE)) {
      list.push({ code: DEFAULT_TERM_CODE, term: 'Current Term' })
    }
    return list
      .filter((term) => term?.code)
      .sort((a, b) => Number(b.code) - Number(a.code))
  }, [terms])

  const getTermLabel = (code) => {
    const foundTerm = termOptions.find((term) => term.code === code)
    if (foundTerm) return foundTerm.term
    if (code === DEFAULT_TERM_CODE) return 'Current Term'
    return code
  }
  const latestTermCode = termOptions.length
    ? termOptions[0].code
    : DEFAULT_TERM_CODE
  const startIndex = termOptions.findIndex((term) => term.code === termStart)
  const endOptions = startIndex >= 0 ? termOptions.slice(0, startIndex + 1) : termOptions
  const showTermEnd = termStart !== 'all' && termStart !== latestTermCode

  const handleTermStartSelect = (event) => {
    const nextValue = event.target.value
    const wasAll = termStart === 'all'

    setTermStart(nextValue)
    if (wasAll && nextValue !== 'all') {
      setTermEnd(nextValue)
    }
  }

  useEffect(() => {
    if (!termOptions.length) return

    if (termStart !== 'all' && startIndex === -1) {
      setTermStart('all')
      setTermEnd('all')
      return
    }

    if (termStart === 'all') {
      if (termEnd !== 'all') {
        setTermEnd('all')
      }
      return
    }

    if (termStart === latestTermCode) {
      if (termEnd !== termStart) {
        setTermEnd(termStart)
      }
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
  }, [termOptions, termStart, termEnd, latestTermCode, startIndex])

  const normalizeResults = (payload) => {
    if (Array.isArray(payload)) {
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
      const searchParams = new URLSearchParams({
        q: query.trim(),
        offset: '0',
        top_n_results: String(PAGE_SIZE),
        term_code: 'all',
      })
      if (termStart && termStart !== 'all') {
        searchParams.set('term_start', termStart)
      }
      if (termEnd && termEnd !== 'all') {
        searchParams.set('term_end', termEnd)
      }

      const res = await apiFetch(`/api/courses/?${searchParams.toString()}`)
      if (!res.ok) throw new Error(`Search failed ${res.status}`)
      const json = await res.json()

      const normalized = normalizeResults(json)
      setResults(normalized)
      setExpandedCourses(new Set())
      setSyllabusLookup({})

      const uniqueCourses = Object.keys(normalized).length
      setHasMore(uniqueCourses === PAGE_SIZE)
      setCurrentOffset(uniqueCourses)
      setLastQuery(query.trim())
      setLastTermStart(termStart)
      setLastTermEnd(termEnd)
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
      const searchParams = new URLSearchParams({
        q: lastQuery,
        offset: currentOffset.toString(),
        top_n_results: String(PAGE_SIZE),
        term_code: 'all',
      })
      if (lastTermStart && lastTermStart !== 'all') {
        searchParams.set('term_start', lastTermStart)
      }
      if (lastTermEnd && lastTermEnd !== 'all') {
        searchParams.set('term_end', lastTermEnd)
      }

      const res = await apiFetch(`/api/courses/?${searchParams.toString()}`)
      if (!res.ok) throw new Error(`Search failed ${res.status}`)
      const json = await res.json()

      const newResults = normalizeResults(json)

      const mergedResults = { ...results }
      for (const [courseCode, courseInstances] of Object.entries(newResults)) {
        if (mergedResults[courseCode]) {
          mergedResults[courseCode] = [...mergedResults[courseCode], ...courseInstances]
        } else {
          mergedResults[courseCode] = courseInstances
        }
      }

      setResults(mergedResults)

      const uniqueNewCourses = Object.keys(newResults).length
      setHasMore(uniqueNewCourses === PAGE_SIZE)
      setCurrentOffset((prev) => prev + uniqueNewCourses)
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

  const handleInstructorClick = (instructor) => {
    const trimmed = String(instructor || '').trim()
    if (!trimmed || trimmed.toUpperCase() === 'TBA') return

    const priorQuery = query.trim()
    if (priorQuery || termStart !== 'all' || termEnd !== 'all') {
      setPreviousSearch({ query: priorQuery, termStart, termEnd })
    }

    setQuery(trimmed)
  }

  const handleRestorePreviousSearch = () => {
    if (!previousSearch) return
    setQuery(previousSearch.query || '')
    setTermStart(previousSearch.termStart || 'all')
    setTermEnd(previousSearch.termEnd || 'all')
    setPreviousSearch(null)
  }

  const handleQueryChange = (e) => {
    const value = e.target.value.replace(/[^a-zA-Z0-9\- ]/g, '')
    setQuery(value)
  }

  useEffect(() => {
    const timerId = setTimeout(() => {
      doSearch()
    }, 250)

    return () => clearTimeout(timerId)
  }, [query, termStart, termEnd])

  useEffect(() => {
    syllabusLookupRef.current = syllabusLookup
  }, [syllabusLookup])

  useEffect(() => {
    return () => {
      Object.values(syllabusLookupRef.current).forEach((entry) => {
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

  const toggleInstructorEvalCollapsed = (evalKey) => {
    const newCollapsed = new Set(collapsedInstructorEvals)
    if (newCollapsed.has(evalKey)) {
      newCollapsed.delete(evalKey)
    } else {
      newCollapsed.add(evalKey)
    }
    setCollapsedInstructorEvals(newCollapsed)
  }

  const toggleInstructorSectionCollapsed = (sectionKey) => {
    const newCollapsed = new Set(collapsedInstructorSections)
    if (newCollapsed.has(sectionKey)) {
      newCollapsed.delete(sectionKey)
    } else {
      newCollapsed.add(sectionKey)
    }
    setCollapsedInstructorSections(newCollapsed)
  }

  const formatMeetingTimes = (timesStr) => {
    if (!timesStr || timesStr === 'TBA' || timesStr === '[]') return ['TBA']
    const trimmed = timesStr.trim()
    if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
      try {
        const parsed = JSON.parse(trimmed)
        if (Array.isArray(parsed)) {
          const times = parsed.map((time) => String(time).trim()).filter(Boolean)
          return times.length > 0 ? times : ['TBA']
        }
      } catch { }
    }
    const timesList = timesStr.split(/,|;\s*/).map(s => s.trim()).filter(s => s.length > 0)
    return timesList.length > 0 ? timesList : ['TBA']
  }

  const formatInstructors = (instructorStr) => {
    if (!instructorStr || instructorStr === 'TBA' || instructorStr === '[]') return ['TBA']
    const trimmed = instructorStr.trim()
    if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
      try {
        const parsed = JSON.parse(trimmed)
        if (Array.isArray(parsed)) {
          const names = parsed.map((name) => String(name).trim()).filter(Boolean)
          return names.length > 0 ? names : ['TBA']
        }
      } catch { }
    }
    const instructors = instructorStr.split(/,|;\s*/).map(s => s.trim()).filter(s => s.length > 0)
    return instructors.length > 0 ? instructors : ['TBA']
  }

  const getInstructorNamesForCourse = (course) => {
    return formatInstructors(course.instructors).filter(
      (name) => name && name.toUpperCase() !== 'TBA'
    )
  }

  const getEvaluationKey = (course) => `${course.term}-${course.crn}`
  const getInstructorEvalKey = (course) => `instr-${course.term}-${course.crn}`

  const formatEvalHtml = (html) => {
    if (!html) return ''
    return html
      .replace(/<div class="charts">[\s\S]*?<div class="comments">/g, '<div class="comments">')
      .replace(/<div class="chart">[\s\S]*?<\/div>\s*<\/div>/g, '')
      .replace(/<img[^>]*>/g, '')
  }

  const renderCharts = (charts) => {
    if (!charts || charts.length === 0) return null

    return (
      <div className="charts-section">
        <div className="charts-title">Survey Results</div>
        <div className="charts-grid">
          {charts.map((chart, idx) => {
            const chartData = chart.labels.map((label, i) => ({
              name: label,
              percent: chart.values[i],
            }))

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
                        itemStyle={{ color: '#E8E8E8' }}
                        formatter={(value) => [`${value}%`, 'Percent']}
                        wrapperStyle={{ color: '#E8E8E8' }}
                      />
                      <Bar dataKey="percent" radius={[8, 8, 0, 0]}>
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
    )
  }

  const fetchEvaluation = async (course) => {
    const key = getEvaluationKey(course)

    setEvaluationLookup((prev) => ({
      ...prev,
      [key]: { status: 'loading', message: 'Loading evaluation...' },
    }))

    try {
      const subject = course.crs ? course.crs.split(' ')[0] : ''
      const params = new URLSearchParams({ term: course.term, crn: course.crn, subject })
      const res = await apiFetch(`/api/evaluate?${params.toString()}`)

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
      if (err.message.includes('401') || err.message.includes('403')) {
        setEstherAuthState('unauthenticated')
        setEvaluationLookup((prev) => ({
          ...prev,
          [key]: { status: 'none', message: ESTHER_AUTH_REQUIRED_MESSAGE },
        }));
      } else {
        setEvaluationLookup((prev) => ({
          ...prev,
          [key]: {
            status: 'error',
            message: err.message || 'Unable to fetch evaluation',
          },
        }))
      }
    }
  }

  const fetchInstructorEvaluations = async (course) => {
    const key = getInstructorEvalKey(course)
    const instructorNames = getInstructorNamesForCourse(course)

    if (instructorNames.length === 0) {
      setInstructorEvalLookup((prev) => ({
        ...prev,
        [key]: { status: 'none', message: 'No instructors listed for this course' },
      }))
      return
    }

    setInstructorEvalLookup((prev) => ({
      ...prev,
      [key]: { status: 'loading', message: 'Loading instructor evaluations...' },
    }))

    try {
      const params = new URLSearchParams({
        term: course.term,
        crn: course.crn,
        instructor_names: instructorNames.join('|'),
      })
      const res = await apiFetch(`/api/instructor-evaluate?${params.toString()}`)

      if (!res.ok) {
        throw new Error(`Instructor evaluation lookup failed ${res.status}`)
      }

      const data = await res.json()
      if (data.success && Array.isArray(data.results)) {
        setInstructorEvalLookup((prev) => ({
          ...prev,
          [key]: {
            status: 'available',
            message: 'Instructor evaluations loaded',
            results: data.results,
            missing_instructors: data.missing_instructors || [],
          },
        }))
      } else {
        setInstructorEvalLookup((prev) => ({
          ...prev,
          [key]: {
            status: 'none',
            message: data.message || 'No instructor evaluation data found',
            results: data.results || [],
            missing_instructors: data.missing_instructors || [],
          },
        }))
      }
    } catch (err) {
      if (err.message.includes('401') || err.message.includes('403')) {
        setEstherAuthState('unauthenticated')
        setInstructorEvalLookup((prev) => ({
          ...prev,
          [key]: { status: 'none', message: ESTHER_AUTH_REQUIRED_MESSAGE },
        }));
      } else {
        setInstructorEvalLookup((prev) => ({
          ...prev,
          [key]: {
            status: 'error',
            message: err.message || 'Unable to fetch instructor evaluations',
          },
        }))
      }
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
      const res = await apiFetch(`/api/syllabus?${params.toString()}`)

      if (!res.ok) {
        throw new Error(`Syllabus lookup failed ${res.status}`)
      }

      const contentType = (res.headers.get('content-type') || '').toLowerCase()

      if (contentType.includes('application/pdf')) {
        const pdfBlob = await res.blob()
        const pdfUrl = URL.createObjectURL(pdfBlob)

        setSyllabusLookup((prev) => {
          const prior = prev[key]
          if (prior?.blobUrl && prior?.url) {
            URL.revokeObjectURL(prior.url)
          }

          return {
            ...prev,
            [key]: {
              status: 'available',
              message: 'Syllabus available',
              url: pdfUrl,
              blobUrl: true,
            },
          }
        })

        setActiveSyllabusKey(key)
        return
      }

      const data = await res.json()
      if (data.syllabus_url) {
        const syllabusUrl = data.syllabus_url.startsWith('http')
          ? data.syllabus_url
          : `${API_BASE_URL}${data.syllabus_url}`
        const pdfRes = await fetch(syllabusUrl)
        if (!pdfRes.ok) {
          throw new Error(`Failed to fetch PDF: ${pdfRes.status}`)
        }

        const pdfBlob = await pdfRes.blob()
        const pdfUrl = URL.createObjectURL(pdfBlob)

        setSyllabusLookup((prev) => {
          const prior = prev[key]
          if (prior?.blobUrl && prior?.url) {
            URL.revokeObjectURL(prior.url)
          }

          return {
            ...prev,
            [key]: {
              status: 'available',
              message: data.message || 'Syllabus available',
              url: pdfUrl,
              blobUrl: true,
            },
          }
        })

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
      if (err.message.includes('401') || err.message.includes('403')) {
        setEstherAuthState('unauthenticated')
        setSyllabusLookup((prev) => ({
          ...prev,
          [key]: { status: 'none', message: ESTHER_AUTH_REQUIRED_MESSAGE },
        }));
      } else {
        setSyllabusLookup((prev) => ({
          ...prev,
          [key]: {
            status: 'error',
            message: err.message || 'Unable to fetch syllabus',
          },
        }))
      }
    }
  }



  return (
    <div className="app">
      <div className="header">
        <div className="header-top">
          <div className="header-copy">
            <h1>Rice Course Explorer</h1>
            <p className="tagline">Search for courses across all terms</p>
          </div>
          <div className="esther-auth-panel">
            <button
              type="button"
              className="esther-auth-btn"
              onClick={() => setShowAuthModal(true)}
            >
              Login to ESTHER
            </button>
            <span className={`esther-auth-status ${estherAuthState === 'authenticated' ? 'active' : 'inactive'}`}>
              {estherAuthState === 'authenticated' ? 'Authenticated' : 'Not signed in'}
            </span>
          </div>
        </div>
      </div>

      <div className="container">
        <section className="search-section">
          {previousSearch && (
            <div className="previous-search-row">
              <button
                type="button"
                className="previous-search-btn"
                onClick={handleRestorePreviousSearch}
              >
                Back to previous search
              </button>
            </div>
          )}
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
              <label>Term Range</label>
              <div className={`term-range ${showTermEnd ? '' : 'term-range-single'}`}>
                <div className="term-range-field">
                  <span className="term-range-label">From</span>
                  <select
                    id="term-start"
                    value={termStart}
                    onChange={handleTermStartSelect}
                  >
                    <option value="all">All terms</option>
                    {termOptions.map((term) => (
                      <option key={term.code} value={term.code}>
                        {term.term}
                      </option>
                    ))}
                  </select>
                </div>
                {showTermEnd && (
                  <div className="term-range-field">
                    <span className="term-range-label">To</span>
                    <select
                      id="term-end"
                      value={termEnd}
                      onChange={(e) => setTermEnd(e.target.value)}
                    >
                      {endOptions.map((term) => (
                        <option key={term.code} value={term.code}>
                          {term.term}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
              </div>
            </div>
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
                      const evaluationState = evaluationLookup[getEvaluationKey(course)]
                      const instructorEvalState = instructorEvalLookup[getInstructorEvalKey(course)]
                      const coursePageUrl = course.course_page || `https://courses.rice.edu/courses/courses/!SWKSCAT.cat?p_action=COURSE&p_term=${course.term}&p_crn=${course.crn}`
                      const authRequired = [syllabusState, evaluationState, instructorEvalState].some(
                        (state) => state?.status === 'none' && state?.message === ESTHER_AUTH_REQUIRED_MESSAGE
                      )

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
                                  {formatInstructors(course.instructors).map((instructor, idx) => {
                                    const trimmedInstructor = String(instructor).trim()
                                    const isTba = trimmedInstructor.toUpperCase() === 'TBA'

                                    return (
                                      <div key={idx} className="detail-item">
                                        {isTba ? (
                                          <span className="instructor-text muted">{instructor}</span>
                                        ) : (
                                          <a
                                            href="#"
                                            className="instructor-link"
                                            onClick={(e) => {
                                              e.preventDefault()
                                              handleInstructorClick(instructor)
                                            }}
                                          >
                                            {instructor}
                                          </a>
                                        )}
                                      </div>
                                    )
                                  })}
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
                              disabled={evaluationState?.status === 'loading'}
                            >
                              {evaluationState?.status === 'loading' ? 'Loading...' : 'Get Evaluation'}
                            </button>
                            <button
                              type="button"
                              className="instructor-eval-btn"
                              onClick={() => fetchInstructorEvaluations(course)}
                              disabled={instructorEvalState?.status === 'loading'}
                            >
                              {instructorEvalState?.status === 'loading' ? 'Loading...' : 'Show Instructor Evals'}
                            </button>
                          </div>

                          {authRequired && (
                            <p className="evaluation-status neutral">{ESTHER_AUTH_REQUIRED_MESSAGE}</p>
                          )}

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

                          {syllabusState?.status === 'none' && syllabusState.message !== ESTHER_AUTH_REQUIRED_MESSAGE && (
                            <p className="syllabus-status neutral">{syllabusState.message}</p>
                          )}

                          {syllabusState?.status === 'error' && (
                            <p className="syllabus-status error">{syllabusState.message}</p>
                          )}

                          {evaluationState?.status === 'available' && (
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
                                  {renderCharts(evaluationState?.charts)}
                                  <div className="comments-section">
                                    <div
                                      dangerouslySetInnerHTML={{
                                        __html: formatEvalHtml(evaluationState?.html),
                                      }}
                                    />
                                  </div>
                                </div>
                              )}
                            </>
                          )}

                          {instructorEvalState?.status === 'available' && (
                            <>
                              <button
                                type="button"
                                className="collapse-instructor-eval-btn"
                                onClick={() => toggleInstructorEvalCollapsed(getInstructorEvalKey(course))}
                              >
                                {collapsedInstructorEvals.has(getInstructorEvalKey(course)) ? '▸ Show Instructor Evals' : '▾ Hide Instructor Evals'}
                              </button>
                              {!collapsedInstructorEvals.has(getInstructorEvalKey(course)) && (
                                <div className="instructor-eval-results">
                                  {instructorEvalState?.missing_instructors?.length > 0 && (
                                    <p className="evaluation-status neutral">
                                      No instructor match for: {instructorEvalState.missing_instructors.join(', ')}
                                    </p>
                                  )}
                                  {instructorEvalState?.results?.map((result) => {
                                    const instructorLabel = result.instructor_name || result.instructor_id || 'Instructor'
                                    const instructorKey = `${getInstructorEvalKey(course)}-${result.instructor_id || instructorLabel}`
                                    const isCollapsed = collapsedInstructorSections.has(instructorKey)

                                    return (
                                      <div key={instructorKey} className="instructor-eval-card">
                                        <button
                                          type="button"
                                          className="collapse-instructor-btn"
                                          onClick={() => toggleInstructorSectionCollapsed(instructorKey)}
                                        >
                                          {isCollapsed ? `▸ ${instructorLabel}` : `▾ ${instructorLabel}`}
                                        </button>
                                        {!isCollapsed && (
                                          <div className="instructor-eval-sections">
                                            {(!result.sections || result.sections.length === 0) && (
                                              <p className="evaluation-status neutral">{result.message || 'No evaluation data found'}</p>
                                            )}
                                            {result.sections?.map((section, idx) => (
                                              <div key={`${instructorKey}-${idx}`} className="instructor-eval-section">
                                                {renderCharts(section.charts)}
                                                <div className="comments-section">
                                                  <div
                                                    dangerouslySetInnerHTML={{
                                                      __html: formatEvalHtml(section.html),
                                                    }}
                                                  />
                                                </div>
                                              </div>
                                            ))}
                                          </div>
                                        )}
                                      </div>
                                    )
                                  })}
                                </div>
                              )}
                            </>
                          )}

                          {instructorEvalState?.status === 'none' && instructorEvalState?.message !== ESTHER_AUTH_REQUIRED_MESSAGE && (
                            <p className="evaluation-status neutral">{instructorEvalState?.message}</p>
                          )}

                          {instructorEvalState?.status === 'error' && (
                            <p className="evaluation-status error">{instructorEvalState?.message}</p>
                          )}

                          {evaluationState?.status === 'none' && evaluationState?.message !== ESTHER_AUTH_REQUIRED_MESSAGE && (
                            <p className="evaluation-status neutral">{evaluationState?.message}</p>
                          )}

                          {evaluationState?.status === 'error' && (
                            <p className="evaluation-status error">{evaluationState?.message}</p>
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

      {showAuthModal && (
        <div className="modal-overlay auth-modal-overlay">
          <div className="modal-content auth-modal-content">
            <h2 className="auth-modal-title">ESTHER Login</h2>
            <div className="auth-modal-disclaimer">
              Credentials are not saved by this site. They are sent directly to ESTHER for Duo push-notification authentication. This is only required for viewing syllabi and evaluations.
            </div>
            <p className="auth-modal-copy">
              Sign in with your Rice NetID to open an ESTHER session for this browser.
            </p>

            <form onSubmit={handleAuthSubmit}>
              <div className="auth-field">
                <label>Rice NetID</label>
                <input
                  type="text"
                  value={netid}
                  onChange={(e) => setNetid(e.target.value)}
                  required
                />
              </div>
              <div className="auth-field">
                <label>Password</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </div>

              {authError && <p className="auth-error">{authError}</p>}

              <div className="auth-actions">
                <button
                  type="button"
                  onClick={() => setShowAuthModal(false)}
                  className="auth-cancel-btn"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={authLoading}
                  className="auth-submit-btn"
                >
                  {authLoading ? 'Waiting for Duo...' : 'Login and Send Duo'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
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