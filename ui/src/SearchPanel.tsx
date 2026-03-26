import type { FormEvent } from 'react'

export type CourseSummary = {
  crn: string
  crs: string
  title: string
  instructors: string
  meeting_times?: string
  credits?: string
}

type Props = {
  query: string
  isLoading: boolean
  error: string | null
  results: CourseSummary[]
  onQueryChange: (next: string) => void
  onSelect: (course: CourseSummary) => void
}

export default function SearchPanel({
  query,
  isLoading,
  error,
  results,
  onQueryChange,
  onSelect,
}: Props) {
  function handleChange(evt: FormEvent<HTMLInputElement>) {
    onQueryChange(evt.currentTarget.value)
  }

  return (
    <div className="search-panel-inner">
      <label htmlFor="search-input" className="search-label">
        Search courses
      </label>
      <input
        id="search-input"
        type="text"
        value={query}
        onChange={handleChange}
        placeholder="e.g. MATH, 212, fundamentals"
        autoComplete="off"
      />
      {isLoading && <div className="search-status">Searching…</div>}
      {error && <div className="search-error">{error}</div>}

      {results.length > 0 ? (
        <ul className="search-results">
          {results.map((course) => (
            <li key={course.crn}>
              <button
                type="button"
                className="search-result"
                onClick={() => onSelect(course)}
              >
                <div className="result-title">
                  <span className="result-code">{course.crs}</span>
                  <span className="result-name">{course.title}</span>
                </div>
                <div className="result-meta">
                  <span className="result-instructors">{course.instructors}</span>
                  {course.meeting_times ? (
                    <span className="result-times"> — {course.meeting_times}</span>
                  ) : null}
                </div>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        query && !isLoading && <div className="search-empty">No results</div>
      )}
    </div>
  )
}
