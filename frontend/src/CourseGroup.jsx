import { useMemo, useRef } from 'react'
import SectionPanel from './SectionPanel.jsx'
import { coursePageUrl, formatCredits, isTba, parseList, sectionKey } from './format.js'

// When expanded past this many sections, also show a collapse button at the bottom.
const FOOTER_COLLAPSE_THRESHOLD = 6

function summarize(instances) {
  const termCodes = [...new Set(instances.map((c) => String(c.term)))].sort((a, b) => Number(a) - Number(b))

  // Count the distinct terms each instructor taught this course in.
  const instructorTerms = new Map()
  for (const course of instances) {
    for (const name of parseList(course.instructors)) {
      if (isTba(name)) continue
      if (!instructorTerms.has(name)) instructorTerms.set(name, new Set())
      instructorTerms.get(name).add(String(course.term))
    }
  }
  const topInstructors = [...instructorTerms.entries()]
    .map(([name, terms]) => ({ name, termCount: terms.size }))
    .sort((a, b) => b.termCount - a.termCount)
    .slice(0, 3)

  const shared = (field) => {
    const first = instances[0]?.[field] || null
    return instances.every((c) => (c[field] || null) === first) ? first : null
  }

  return {
    termCount: termCodes.length,
    lastTerm: termCodes[termCodes.length - 1],
    topInstructors,
    sharedCredits: shared('credits'),
    sharedDistribution: shared('distribution'),
    // Values that differ between sections are shown per row instead of in the header.
    creditsVary: shared('credits') === null && instances.some((c) => c.credits),
    distributionVaries: shared('distribution') === null && instances.some((c) => c.distribution),
  }
}

function InstructorList({ course, onInstructorClick }) {
  return parseList(course.instructors).map((instructor, idx) => (
    isTba(instructor) ? (
      <span key={idx} className="muted">TBA</span>
    ) : (
      <button
        key={idx}
        type="button"
        className="instructor-btn"
        title={`Search courses taught by ${instructor}`}
        onClick={() => onInstructorClick(instructor)}
      >
        {instructor}
      </button>
    )
  ))
}

function SectionRow({ course, summary, getTermLabel, openPanel, panelState, onTogglePanel, onRetryPanel, onInstructorClick, collapsedInstructorSections, onToggleInstructorSection }) {
  const key = sectionKey(course)
  const times = parseList(course.meeting_times)

  const actionButton = (type, label) => (
    <button
      type="button"
      className={`action-btn ${openPanel === type ? 'is-active' : ''}`}
      aria-expanded={openPanel === type}
      onClick={() => onTogglePanel(course, type)}
    >
      {label}
    </button>
  )

  return (
    <div className={`section ${openPanel ? 'has-panel' : ''}`}>
      <div className="section-row">
        <div className="cell-term">
          <span className="term-label">{getTermLabel(course.term)}</span>
          {((summary.creditsVary && course.credits) || (summary.distributionVaries && course.distribution)) && (
            <span className="row-tags">
              {summary.creditsVary && course.credits && <span className="tag">{formatCredits(course.credits)}</span>}
              {summary.distributionVaries && course.distribution && <span className="tag tag-dist">{course.distribution}</span>}
            </span>
          )}
        </div>
        <div className="cell-crn"><span className="cell-label">CRN </span>{course.crn}</div>
        <div className="cell-instructors">
          <InstructorList course={course} onInstructorClick={onInstructorClick} />
        </div>
        <div className="cell-times">
          {times.map((time, idx) => (
            <span key={idx} className={isTba(time) ? 'muted' : ''}>{time}</span>
          ))}
        </div>
        <div className="cell-actions">
          <a href={coursePageUrl(course)} target="_blank" rel="noreferrer" className="action-btn">
            Details <span aria-hidden="true">↗</span>
          </a>
          {actionButton('syllabus', 'Syllabus')}
          {actionButton('eval', 'Course evals')}
          {actionButton('instructor', 'Instructor evals')}
        </div>
      </div>

      {openPanel && (
        <SectionPanel
          type={openPanel}
          course={course}
          state={panelState}
          panelKey={key}
          onClose={() => onTogglePanel(course, openPanel)}
          onRetry={() => onRetryPanel(course, openPanel)}
          collapsedInstructorSections={collapsedInstructorSections}
          onToggleInstructorSection={onToggleInstructorSection}
        />
      )}
    </div>
  )
}

export default function CourseGroup({
  courseCode,
  instances,
  isExpanded,
  onToggleExpanded,
  getTermLabel,
  openPanels,
  lookups,
  onTogglePanel,
  onRetryPanel,
  onInstructorClick,
  collapsedInstructorSections,
  onToggleInstructorSection,
}) {
  const summary = useMemo(() => summarize(instances), [instances])
  const displayInstances = isExpanded ? instances : instances.slice(0, 1)
  const sectionCount = instances.length
  const title = instances[0]?.title
  const groupRef = useRef(null)

  const toggleButton = (isFooter = false) => (
    <button
      type="button"
      className={`expand-btn ${isFooter ? 'expand-btn-footer' : ''}`}
      aria-expanded={isExpanded}
      onClick={() => {
        onToggleExpanded(courseCode)
        // Collapsing from the bottom of a long list: keep the course in view.
        if (isFooter) groupRef.current?.scrollIntoView({ block: 'start' })
      }}
    >
      {isExpanded ? 'Show latest only' : `Show all ${sectionCount} sections`}
      <span className={`chevron ${isExpanded ? 'open' : ''}`} aria-hidden="true">▾</span>
    </button>
  )

  return (
    <article className="course-group" ref={groupRef}>
      <header className="course-header">
        <div className="course-heading">
          <h3>{courseCode}</h3>
          {title && <p className="course-title">{title}</p>}
          {(summary.sharedCredits || summary.sharedDistribution) && (
            <div className="course-tags">
              {summary.sharedCredits && <span className="tag">{formatCredits(summary.sharedCredits)}</span>}
              {summary.sharedDistribution && <span className="tag tag-dist">{summary.sharedDistribution}</span>}
            </div>
          )}
        </div>
        {sectionCount > 1 && toggleButton()}
      </header>

      {summary.termCount > 1 && (
        <div className="course-summary">
          <span>
            Offered in <strong>{summary.termCount} terms</strong>
            {' · '}Last offered {getTermLabel(summary.lastTerm)}
          </span>
          {summary.topInstructors.length > 0 && (
            <span>
              Most often taught by{' '}
              {summary.topInstructors.map((instr, idx) => (
                <span key={instr.name}>
                  {idx > 0 && ', '}
                  <button
                    type="button"
                    className="instructor-btn"
                    title={`Search courses taught by ${instr.name}`}
                    onClick={() => onInstructorClick(instr.name)}
                  >
                    {instr.name}
                  </button>
                  <span className="muted"> ({instr.termCount})</span>
                </span>
              ))}
            </span>
          )}
        </div>
      )}

      <div className="sections">
        <div className="section-row section-row-head" aria-hidden="true">
          <span>Term</span>
          <span>CRN</span>
          <span>Instructors</span>
          <span>Times</span>
          <span />
        </div>
        {displayInstances.map((course) => {
          const key = sectionKey(course)
          const openPanel = openPanels[key] || null
          return (
            <SectionRow
              key={key}
              course={course}
              summary={summary}
              getTermLabel={getTermLabel}
              openPanel={openPanel}
              panelState={openPanel ? lookups[openPanel][key] : null}
              onTogglePanel={onTogglePanel}
              onRetryPanel={onRetryPanel}
              onInstructorClick={onInstructorClick}
              collapsedInstructorSections={collapsedInstructorSections}
              onToggleInstructorSection={onToggleInstructorSection}
            />
          )
        })}
      </div>

      {isExpanded && sectionCount > FOOTER_COLLAPSE_THRESHOLD && (
        <div className="course-footer">{toggleButton(true)}</div>
      )}
    </article>
  )
}
