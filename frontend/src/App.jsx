import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, Cell } from 'recharts'
import './App.css'
import { Analytics } from '@vercel/analytics/react';
import { SpeedInsights } from '@vercel/speed-insights/react';

const DEFAULT_TERM_CODE = '202710'
const PAGE_SIZE = 10
const API_BASE_URL = import.meta.env.VITE_API_URL
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

function TermSelect({ id, value, onChange, options, placeholder = "Select term" }) {
  const [isOpen, setIsOpen] = useState(false)
  const [openUpward, setOpenUpward] = useState(false)
  const [filterText, setFilterText] = useState('')
  const [highlightedIndex, setHighlightedIndex] = useState(0)
  const dropdownRef = useRef(null)
  const optionsListRef = useRef(null)
  const inputRef = useRef(null)

  const selectedOption = options.find((opt) => opt.code === value)
  const currentLabel = selectedOption ? selectedOption.term : (value === 'all' ? 'All terms' : value)

  useEffect(() => {
    if (!isOpen) {
      setFilterText(currentLabel)
    } else if (dropdownRef.current) {
      const rect = dropdownRef.current.getBoundingClientRect()
      const spaceBelow = window.innerHeight - rect.bottom
      if (spaceBelow < 260 && rect.top > 260) {
        setOpenUpward(true)
      } else {
        setOpenUpward(false)
      }
    }
  }, [value, currentLabel, isOpen])

  const filteredOptions = useMemo(() => {
    if (!isOpen || !filterText.trim()) return options
    const query = filterText.toLowerCase().trim()
    const tokens = query.split(/\s+/).filter(Boolean)

    const matches = options.filter((opt) => {
      const termLower = opt.term.toLowerCase()
      const codeLower = opt.code.toLowerCase()
      const combined = `${termLower} ${codeLower}`

      return tokens.every((token) => combined.includes(token))
    })

    return matches.sort((a, b) => {
      const aTermLower = a.term.toLowerCase()
      const bTermLower = b.term.toLowerCase()
      const aCodeLower = a.code.toLowerCase()
      const bCodeLower = b.code.toLowerCase()

      const aExact = (a.code === 'all' && query === 'all') || aTermLower === query || aCodeLower === query
      const bExact = (b.code === 'all' && query === 'all') || bTermLower === query || bCodeLower === query
      if (aExact && !bExact) return -1
      if (!aExact && bExact) return 1

      const firstToken = tokens[0] || query
      const aStartsWith = aTermLower.startsWith(query) || aCodeLower.startsWith(query) || aTermLower.startsWith(firstToken)
      const bStartsWith = bTermLower.startsWith(query) || bCodeLower.startsWith(query) || bTermLower.startsWith(firstToken)
      if (aStartsWith && !bStartsWith) return -1
      if (!aStartsWith && bStartsWith) return 1

      return 0
    })
  }, [options, filterText, isOpen])

  useEffect(() => {
    setHighlightedIndex(0)
  }, [filteredOptions])

  useEffect(() => {
    if (isOpen && optionsListRef.current) {
      const highlightedEl = optionsListRef.current.children[highlightedIndex]
      if (highlightedEl) {
        highlightedEl.scrollIntoView({ block: 'nearest' })
      }
    }
  }, [highlightedIndex, isOpen])

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setIsOpen(false)
        setFilterText(currentLabel)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [currentLabel])

  const handleSelect = (optionCode) => {
    onChange(optionCode)
    setIsOpen(false)
    inputRef.current?.blur()
  }

  const handleKeyDown = (e) => {
    if (!isOpen) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter') {
        setIsOpen(true)
        setFilterText('')
        e.preventDefault()
      }
      return
    }

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlightedIndex((prev) => Math.min(prev + 1, filteredOptions.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlightedIndex((prev) => Math.max(prev - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (filteredOptions[highlightedIndex]) {
        handleSelect(filteredOptions[highlightedIndex].code)
      }
    } else if (e.key === 'Escape' || e.key === 'Tab') {
      setIsOpen(false)
      setFilterText(currentLabel)
      inputRef.current?.blur()
    }
  }

  return (
    <div className="term-select-container" ref={dropdownRef}>
      <div className="term-select-input-wrap">
        <input
          id={id}
          ref={inputRef}
          type="text"
          className="term-select-input"
          value={isOpen ? filterText : currentLabel}
          placeholder={placeholder}
          onFocus={(e) => {
            setIsOpen(true)
            setFilterText('')
            e.target.select()
          }}
          onChange={(e) => {
            setFilterText(e.target.value)
            if (!isOpen) setIsOpen(true)
          }}
          onKeyDown={handleKeyDown}
          autoComplete="off"
        />
        <span className={`term-select-arrow ${isOpen ? 'open' : ''}`}>▾</span>
      </div>

      {isOpen && (
        <div className={`term-select-dropdown ${openUpward ? 'open-upward' : ''}`} ref={optionsListRef}>
          {filteredOptions.length > 0 ? (
            filteredOptions.map((opt, idx) => (
              <div
                key={opt.code}
                className={`term-select-option ${opt.code === value ? 'selected' : ''} ${idx === highlightedIndex ? 'highlighted' : ''}`}
                onMouseEnter={() => setHighlightedIndex(idx)}
                onMouseDown={(e) => {
                  e.preventDefault()
                  handleSelect(opt.code)
                }}
              >
                <span className="term-name">{opt.term}</span>
                {opt.code !== 'all' && <span className="term-code-badge">{opt.code}</span>}
              </div>
            ))
          ) : (
            <div className="term-select-no-results">No matching terms</div>
          )}
        </div>
      )}
    </div>
  )
}

function App() {
  const [clientId] = useState(() => getOrCreateClientId())
  const [query, setQuery] = useState('')
  const [termStart, setTermStart] = useState('all')
  const [termEnd, setTermEnd] = useState('all')
  const [termStartText, setTermStartText] = useState('')
  const [termEndText, setTermEndText] = useState('')
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
  const searchInputRef = useRef(null)
  const syllabusLookupRef = useRef({})

  const apiFetch = useCallback((path, options = {}) => {
    const headers = new Headers(options.headers || {})
    headers.set('X-Client-Id', clientId)
    return fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers,
    })
  }, [clientId])

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
  }, [apiFetch])

  useEffect(() => {
    const handleShortcut = (event) => {
      const isSearchShortcut = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k'
      if (!isSearchShortcut) return

      event.preventDefault()
      searchInputRef.current?.focus()
      searchInputRef.current?.select?.()
    }

    window.addEventListener('keydown', handleShortcut)
    return () => window.removeEventListener('keydown', handleShortcut)
  }, [])

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

  const getTermLabel = (code) => {
    if (code === 'all') return 'All terms'
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

  const handleTermStartChange = (nextCode) => {
    if (previousSearch) setPreviousSearch(null)
    const wasAll = termStart === 'all'
    setTermStart(nextCode)
    if (wasAll && nextCode !== 'all') {
      setTermEnd(nextCode)
    }
  }

  const handleTermEndChange = (nextCode) => {
    if (previousSearch) setPreviousSearch(null)
    setTermEnd(nextCode)
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

  const doSearch = useCallback(async () => {
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
  }, [apiFetch, query, termStart, termEnd])

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
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const handleRestorePreviousSearch = () => {
    if (!previousSearch) return
    setQuery(previousSearch.query || '')
    setTermStart(previousSearch.termStart || 'all')
    setTermEnd(previousSearch.termEnd || 'all')
    setPreviousSearch(null)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const handleQueryChange = (e) => {
    const value = e.target.value.replace(/[^a-zA-Z0-9\- ]/g, '')
    if (previousSearch) {
      setPreviousSearch(null)
    }
    setQuery(value)
  }

  useEffect(() => {
    const timerId = setTimeout(() => {
      doSearch()
    }, 250)

    return () => clearTimeout(timerId)
  }, [doSearch])

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

  const toggleEvalCollapsed = (evalKey, course) => {
    const instrKey = course ? getInstructorEvalKey(course) : null
    const newCollapsed = new Set(collapsedEvals)
    if (newCollapsed.has(evalKey)) {
      newCollapsed.delete(evalKey)
      if (instrKey) {
        setCollapsedInstructorEvals((prev) => new Set(prev).add(instrKey))
      }
    } else {
      newCollapsed.add(evalKey)
    }
    setCollapsedEvals(newCollapsed)
  }

  const toggleInstructorEvalCollapsed = (instrKey, course) => {
    const evalKey = course ? getEvaluationKey(course) : null
    const newCollapsed = new Set(collapsedInstructorEvals)
    if (newCollapsed.has(instrKey)) {
      newCollapsed.delete(instrKey)
      if (evalKey) {
        setCollapsedEvals((prev) => new Set(prev).add(evalKey))
      }
    } else {
      newCollapsed.add(instrKey)
    }
    setCollapsedInstructorEvals(newCollapsed)
  }

  const handleCourseEvalClick = (course) => {
    const evalKey = getEvaluationKey(course)
    const instrKey = getInstructorEvalKey(course)

    setCollapsedInstructorEvals((prev) => new Set(prev).add(instrKey))

    const evalState = evaluationLookup[evalKey]
    if (!evalState) {
      setCollapsedEvals((prev) => {
        const next = new Set(prev)
        next.delete(evalKey)
        return next
      })
      fetchEvaluation(course)
    } else if (evalState.status === 'available') {
      setCollapsedEvals((prev) => {
        const next = new Set(prev)
        if (next.has(evalKey)) {
          next.delete(evalKey)
        } else {
          next.add(evalKey)
        }
        return next
      })
    } else {
      setCollapsedEvals((prev) => {
        const next = new Set(prev)
        next.delete(evalKey)
        return next
      })
      fetchEvaluation(course)
    }
  }

  const handleInstructorEvalClick = (course) => {
    const evalKey = getEvaluationKey(course)
    const instrKey = getInstructorEvalKey(course)

    setCollapsedEvals((prev) => new Set(prev).add(evalKey))

    const instrState = instructorEvalLookup[instrKey]
    if (!instrState) {
      setCollapsedInstructorEvals((prev) => {
        const next = new Set(prev)
        next.delete(instrKey)
        return next
      })
      fetchInstructorEvaluations(course)
    } else if (instrState.status === 'available') {
      setCollapsedInstructorEvals((prev) => {
        const next = new Set(prev)
        if (next.has(instrKey)) {
          next.delete(instrKey)
        } else {
          next.add(instrKey)
        }
        return next
      })
    } else {
      setCollapsedInstructorEvals((prev) => {
        const next = new Set(prev)
        next.delete(instrKey)
        return next
      })
      fetchInstructorEvaluations(course)
    }
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
      } catch (error) {
        void error
      }
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
      } catch (error) {
        void error
      }
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
    let cleaned = html
    let cleaned = html
      .replace(/<div class="charts">[\s\S]*?<div class="comments">/g, '<div class="comments">')
      .replace(/<div class="chart">[\s\S]*?<\/div>\s*<\/div>/g, '')
      .replace(/<img[^>]*>/g, '')
      .replace(/(?:<p[^>]*>)?\s*Class Mean\s*-\s*Average score within the CRN[\s\S]*?across all CRNs at Rice for the term\.?\s*(?:<\/p>)?/gi, '')
      .replace(/Class Mean\s*-\s*Average score within the CRN[\s\S]*?across all CRNs at Rice for the term\.?/gi, '')
      .replace(/(?:<p[^>]*>|<div[^>]*>)?\s*<a[^>]*>\s*Report a Concern\s*<\/a>\s*(?:<\/p>|<\/div>)?/gi, '')
      .replace(/<a[^>]*>\s*Report a Concern\s*<\/a>/gi, '')

    try {
      const parser = new DOMParser()
      const doc = parser.parseFromString(cleaned, 'text/html')

      let totalText = ''
      const totalMatch = doc.body.textContent.match(/Total Comments:\s*\d+/i)
      if (totalMatch) {
        totalText = totalMatch[0]
      }

      // Convert line breaks and block element closings to newlines for reliable text splitting
      const tempDiv = doc.createElement('div')
      tempDiv.innerHTML = cleaned
        .replace(/<br\s*\/?>/gi, '\n')
        .replace(/<\/(p|div|tr|td|li|h\d)>/gi, '\n')

      const rawText = tempDiv.textContent || ''
      const lines = rawText.split('\n').map((s) => s.trim()).filter(Boolean)
      const dateRegex = /\d{2}\/\d{2}\/\d{4}\s+\d{1,2}:\d{2}\s*(?:[AP]\.?M\.?)/gi
      const items = []

      let currentParts = []
      lines.forEach((line) => {
        if (/student comments/i.test(line) || /total comments/i.test(line)) return

        const dateMatch = line.match(dateRegex)
        if (dateMatch) {
          const dateStr = dateMatch[0]
          const remaining = line.replace(dateRegex, '').trim()
          if (remaining) {
            currentParts.push(remaining)
          }
          const fullCommentText = currentParts.join(' ').trim()
          if (fullCommentText) {
            items.push({ text: fullCommentText, date: dateStr })
          }
          currentParts = []
        } else {
          currentParts.push(line)
        }
      })

      if (items.length > 0) {
        let formattedHtml = `<div class="comments-container">`
        formattedHtml += `<div class="comments-header">`
        formattedHtml += `<h4 class="comments-title">Student Comments</h4>`
        if (totalText) {
          formattedHtml += `<span class="comments-count">${totalText}</span>`
        }
        formattedHtml += `</div>`

        formattedHtml += `<div class="comments-list">`
        items.forEach((item) => {
          formattedHtml += `<div class="comment-card">`
          formattedHtml += `<div class="comment-text">${item.text}</div>`
          formattedHtml += `<div class="comment-date">${item.date}</div>`
          formattedHtml += `</div>`
        })
        formattedHtml += `</div></div>`

        return formattedHtml
      }
    } catch (err) {
      console.error('Error formatting comments:', err)
    }

    return cleaned
  }

  const renderCharts = (charts, is3x3 = false) => {
    if (!charts || charts.length === 0) return null

    return (
      <div className="charts-section">
        <div className="charts-title">Survey Results</div>
        <div className={`charts-grid ${is3x3 ? 'charts-grid-3x3' : ''}`}>
          {charts.map((chart, idx) => {
            const chartData = chart.labels.map((label, i) => ({
              name: label,
              percent: chart.values[i],
            }))

            return (
              <div key={idx} className="chart-container">
                <div className="chart-title">{chart.title}</div>
                <div className="chart-meta">
                  <span>Total Responses: {chart.total}</span>
                </div>
                <div className="chart-wrapper">
                  <ResponsiveContainer width="100%" height={260}>
                    <BarChart data={chartData} margin={{ top: 10, right: 15, left: -15, bottom: 25 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                      <XAxis
                        dataKey="name"
                        angle={-35}
                        angle={-35}
                        textAnchor="end"
                        height={70}
                        interval={0}
                        tick={{ fill: '#E8E8E8', fontSize: 11, fontWeight: 500 }}
                        tick={{ fill: '#E8E8E8', fontSize: 11, fontWeight: 500 }}
                      />
                      <YAxis tick={{ fill: '#E8E8E8', fontSize: 11 }} />
                      <YAxis tick={{ fill: '#E8E8E8', fontSize: 11 }} />
                      <Tooltip
                        isAnimationActive={false}
                        content={({ active, payload }) => {
                          if (active && payload && payload.length) {
                            return (
                              <div className="custom-chart-tooltip">
                                {`${payload[0].value}%`}
                              </div>
                            )
                          }
                          return null
                        }}
                      />
                      <Bar dataKey="percent" fill="#667CC7" radius={[4, 4, 0, 0]} />
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
            <h1>Rice Course Viewer</h1>
            <p className="tagline">Search for Rice courses across the years</p>
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
                <span className="previous-search-icon">↩</span>
                <span>Back to previous search{previousSearch.query ? `: "${previousSearch.query}"` : ''}</span>
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
                {/* <div className="tooltip-wrap">
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
                </div> */}
              </div>
              <input
                id="query"
                ref={searchInputRef}
                value={query}
                onChange={handleQueryChange}
                onKeyDown={onEnter}
                placeholder="Search courses (Ctrl+K)"
              />
            </div>

            <div className="input-group">
              <label>Term Range</label>
              <div className={`term-range ${showTermEnd ? '' : 'term-range-single'}`}>
                <div className="term-range-field">
                  {showTermEnd && <span className="term-range-label">From</span>}
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
                    <span className="term-range-label">To</span>
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

              return (
                <div key={courseCode} className="course-group">
                  <div className="course-header">
                    <div className="header-left">
                      <h3>{courseCode}</h3>
                      {courseInstances.length > 0 && (
                        <p className="course-title">{courseInstances[0].title}</p>
                      )}
                    </div>
                    {courseInstances.length > 1 && (
                      <div
                        className="expand-indicator"
                        onClick={() => toggleExpanded(courseCode)}
                      >
                        <span className="badge">{courseInstances.length}</span>
                        <span className={`chevron ${isExpanded ? 'open' : ''}`}>▸</span>
                      </div>
                    )}
                  </div>

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
                                          <button
                                            type="button"
                                            className="instructor-btn"
                                            title={`Search courses taught by ${instructor}`}
                                            onClick={() => handleInstructorClick(instructor)}
                                          >
                                            <span className="instructor-search-icon">🔎</span>
                                            <span>{instructor}</span>
                                          </button>
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
                              Details
                            </a>
                            <button
                              type="button"
                              className="syllabus-btn"
                              onClick={() => fetchSyllabus(course)}
                              disabled={syllabusState?.status === 'loading'}
                            >
                              {syllabusState?.status === 'loading' ? 'Checking...' : 'View syllabus'}
                            </button>
                            <button
                              type="button"
                              className="evaluation-btn"
                              onClick={() => handleCourseEvalClick(course)}
                              disabled={evaluationState?.status === 'loading'}
                            >
                              {evaluationState?.status === 'loading' ? 'Loading...' : 'Course Evals'}
                            </button>
                            <button
                              type="button"
                              className="instructor-eval-btn"
                              onClick={() => handleInstructorEvalClick(course)}
                              disabled={instructorEvalState?.status === 'loading'}
                            >
                              {instructorEvalState?.status === 'loading' ? 'Loading...' : 'Instructor Evals'}
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
                                onClick={() => toggleEvalCollapsed(getEvaluationKey(course), course)}
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
                                onClick={() => toggleInstructorEvalCollapsed(getInstructorEvalKey(course), course)}
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
                                    const isSingleInstructor = instructorEvalState.results.length === 1

                                    return (
                                      <div key={instructorKey} className={isSingleInstructor ? '' : 'instructor-eval-card'}>
                                        {!isSingleInstructor && (
                                          <button
                                            type="button"
                                            className="collapse-instructor-btn"
                                            onClick={() => toggleInstructorSectionCollapsed(instructorKey)}
                                          >
                                            {isCollapsed ? `▸ ${instructorLabel}` : `▾ ${instructorLabel}`}
                                          </button>
                                        )}
                                        {(!isSingleInstructor ? !isCollapsed : true) && (
                                          <div className="instructor-eval-sections">
                                            {(!result.sections || result.sections.length === 0) && (
                                              <p className="evaluation-status neutral">{result.message || 'No evaluation data found'}</p>
                                            )}
                                            {result.sections?.map((section, idx) => (
                                              <div key={`${instructorKey}-${idx}`} className="instructor-eval-section">
                                                {renderCharts(section.charts, true)}
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

      {/* <footer className="app-footer">
        <p>Built by Rishabh Rengarajan, Rice '29</p>
      </footer> */}

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
              <div className="auth-fields-row">
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
