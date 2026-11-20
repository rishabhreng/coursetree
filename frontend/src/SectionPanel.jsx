import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { formatEvalHtml } from './format.js'

const PANEL_TITLES = {
  syllabus: 'Syllabus',
  eval: 'Course evaluation',
  instructor: 'Instructor evaluations',
}

const LOADING_TEXT = {
  syllabus: 'Checking for a syllabus…',
  eval: 'Loading course evaluation…',
  instructor: 'Loading instructor evaluations…',
}

function EvalCharts({ charts, compact = false }) {
  if (!charts || charts.length === 0) return null

  return (
    <div className="charts-section">
      <div className="charts-title">Survey Results</div>
      <div className={`charts-grid ${compact ? 'charts-grid-3x3' : ''}`}>
        {charts.map((chart, idx) => {
          const chartData = chart.labels.map((label, i) => ({
            name: label,
            percent: chart.values[i],
          }))

          return (
            <div key={idx} className="chart-container">
              <div className="chart-title">{chart.title}</div>
              <div className="chart-meta">Total Responses: {chart.total}</div>
              <div className="chart-wrapper">
                <ResponsiveContainer width="100%" height={260}>
                  <BarChart data={chartData} margin={{ top: 10, right: 15, left: -15, bottom: 25 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                    <XAxis
                      dataKey="name"
                      angle={-35}
                      textAnchor="end"
                      height={65}
                      interval={0}
                      tick={{ fill: '#E8E8E8', fontSize: 10, fontWeight: 500 }}
                    />
                    <YAxis tick={{ fill: '#E8E8E8', fontSize: 10 }} />
                    <Tooltip
                      isAnimationActive={false}
                      content={({ active, payload }) => (
                        active && payload?.length
                          ? <div className="custom-chart-tooltip">{`${payload[0].value}%`}</div>
                          : null
                      )}
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

function Comments({ html }) {
  const formatted = formatEvalHtml(html)
  if (!formatted) return null
  return <div className="comments-section" dangerouslySetInnerHTML={{ __html: formatted }} />
}

function SyllabusContent({ state, course }) {
  if (state.fileType === 'pdf') {
    return <iframe src={state.url} className="syllabus-iframe" title={`Syllabus for CRN ${course.crn}`} />
  }
  if (state.fileType === 'txt') {
    return (
      <div className="syllabus-text-viewer">
        <pre>{state.textContent}</pre>
      </div>
    )
  }
  const filename = state.filename || `syllabus_${course.term}_${course.crn}.${state.fileType || 'docx'}`
  return (
    <div className="syllabus-doc-viewer">
      <p className="syllabus-doc-info">
        This syllabus is a <strong>.{state.fileType || 'doc'}</strong> file and can't be previewed here.
      </p>
      <a href={state.url} download={filename} className="syllabus-download-btn">
        Download {filename}
      </a>
    </div>
  )
}

function InstructorEvalContent({ state, panelKey, collapsedInstructorSections, onToggleInstructorSection }) {
  const results = state.results || []
  const isSingleInstructor = results.length === 1

  return (
    <>
      {state.missing_instructors?.length > 0 && (
        <p className="panel-note">No instructor match for: {state.missing_instructors.join(', ')}</p>
      )}
      {results.map((result) => {
        const instructorLabel = result.instructor_name || result.instructor_id || 'Instructor'
        const instructorKey = `${panelKey}-${result.instructor_id || instructorLabel}`
        const isCollapsed = !isSingleInstructor && collapsedInstructorSections.has(instructorKey)
        const hasData = result.html || result.charts?.length > 0

        return (
          <div key={instructorKey} className={isSingleInstructor ? '' : 'instructor-eval-card'}>
            {!isSingleInstructor && (
              <button
                type="button"
                className="collapse-instructor-btn"
                aria-expanded={!isCollapsed}
                onClick={() => onToggleInstructorSection(instructorKey)}
              >
                {isCollapsed ? '▸' : '▾'} {instructorLabel}
              </button>
            )}
            {!isCollapsed && (
              <div className="instructor-eval-sections">
                {hasData ? (
                  <>
                    <EvalCharts charts={result.charts} compact />
                    <Comments html={result.html} />
                  </>
                ) : (
                  <p className="panel-note">{result.message || 'No evaluation data found'}</p>
                )}
              </div>
            )}
          </div>
        )
      })}
    </>
  )
}

export default function SectionPanel({
  type,
  course,
  state,
  panelKey,
  onClose,
  onRetry,
  collapsedInstructorSections,
  onToggleInstructorSection,
}) {
  let title = PANEL_TITLES[type]
  if (type === 'syllabus' && state?.status === 'available' && state.fileType) {
    title += ` (${state.fileType.toUpperCase()})`
  }

  let body
  if (!state || state.status === 'loading') {
    body = <p className="panel-note">{LOADING_TEXT[type]}</p>
  } else if (state.status === 'none') {
    body = <p className="panel-note">{state.message}</p>
  } else if (state.status === 'error') {
    body = (
      <p className="panel-note error">
        {state.message}{' '}
        <button type="button" className="link-btn" onClick={onRetry}>Try again</button>
      </p>
    )
  } else if (type === 'syllabus') {
    body = <SyllabusContent state={state} course={course} />
  } else if (type === 'eval') {
    body = (
      <>
        <EvalCharts charts={state.charts} />
        <Comments html={state.html} />
      </>
    )
  } else {
    body = (
      <InstructorEvalContent
        state={state}
        panelKey={panelKey}
        collapsedInstructorSections={collapsedInstructorSections}
        onToggleInstructorSection={onToggleInstructorSection}
      />
    )
  }

  const isScrollable = type !== 'syllabus' && state?.status === 'available'

  return (
    <div className={`section-panel panel-${type}`}>
      <div className="panel-head">
        <span className="panel-title">{title}</span>
        <button type="button" className="panel-close" onClick={onClose} aria-label={`Close ${PANEL_TITLES[type].toLowerCase()}`}>
          ×
        </button>
      </div>
      <div className={`panel-body ${isScrollable ? 'panel-body-scroll' : ''}`}>{body}</div>
    </div>
  )
}
