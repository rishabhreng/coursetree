import { Fragment, useLayoutEffect, useRef, useState } from 'react'
import { parsePrereqs } from './format.js'

function CourseChip({ code, onCourseClick }) {
  return (
    <button
      type="button"
      className="course-chip"
      title={`Search ${code}`}
      onClick={() => onCourseClick(code)}
    >
      {code}
    </button>
  )
}

// Requirement rendering.
//
// A requirement expression is laid out one requirement per line when that
// reads better than a single inline run:
//   - AND of groups  -> one line per group, later lines prefixed "and"
//   - OR of paths    -> one line per path,  later lines prefixed "or"
// Within a line, 3+ alternatives read "one of A B C"; nested groups are boxed.

const isCourse = (node) => node.type === 'course'

const joinWith = (items, word, renderItem) => items.map((item, idx) => (
  <Fragment key={idx}>
    {idx > 0 && <span className="req-sep">{word}</span>}
    {renderItem(item)}
  </Fragment>
))

// Renders a node inline. `nested` is true when it sits inside another group
// on the same line, in which case groups get a box so their scope is clear.
function InlineRequirement({ node, nested = false, onCourseClick }) {
  if (isCourse(node)) {
    return <CourseChip code={node.code} onCourseClick={onCourseClick} />
  }

  const renderChild = (child) => (
    <InlineRequirement node={child} nested onCourseClick={onCourseClick} />
  )

  let content
  if (node.type === 'or' && node.items.length > 2 && node.items.every(isCourse)) {
    content = (
      <>
        <span className="req-sep">one of</span>
        {node.items.map((child, idx) => <Fragment key={idx}>{renderChild(child)}</Fragment>)}
      </>
    )
  } else {
    content = joinWith(node.items, node.type, renderChild)
  }

  return nested
    ? <span className={`req-group req-group-${node.type}`}>{content}</span>
    : <span className="req-inline">{content}</span>
}

const fitsOnOneLine = (node) => (
  isCourse(node)
  || node.items.every(isCourse) && (node.type === 'or' || node.items.length <= 4)
)

// Splits a root AND/OR into lines. Groups get a line each; standalone courses
// are gathered onto one shared line (placed where the first one appeared), so
// "A AND B OR C OR D" reads "A and B / or one of C D" rather than a line per course.
const toLines = (tree) => {
  const courses = tree.items.filter(isCourse)
  const lines = []
  let coursesPlaced = false
  for (const item of tree.items) {
    if (!isCourse(item)) {
      lines.push(item)
    } else if (!coursesPlaced) {
      lines.push(courses.length === 1 ? courses[0] : { type: tree.type, items: courses })
      coursesPlaced = true
    }
  }
  return lines
}

function Requirements({ tree, onCourseClick }) {
  if (fitsOnOneLine(tree)) {
    return <InlineRequirement node={tree} onCourseClick={onCourseClick} />
  }

  return (
    <div className="req-lines">
      {toLines(tree).map((item, idx) => (
        <div key={idx} className="req-line">
          <span className="req-connector">{idx > 0 ? tree.type : ''}</span>
          <InlineRequirement node={item} onCourseClick={onCourseClick} />
        </div>
      ))}
    </div>
  )
}

function Description({ text }) {
  const [expanded, setExpanded] = useState(false)
  const [overflows, setOverflows] = useState(false)
  const ref = useRef(null)

  // Only offer "More" when the clamped text is actually cut off.
  useLayoutEffect(() => {
    const el = ref.current
    if (!el || expanded) return undefined
    const measure = () => setOverflows(el.scrollHeight > el.clientHeight + 1)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [text, expanded])

  return (
    <div className="course-desc-wrap">
      <p ref={ref} className={`course-desc ${expanded ? '' : 'is-clamped'}`}>{text}</p>
      {(overflows || expanded) && (
        <button type="button" className="link-btn desc-toggle" onClick={() => setExpanded((v) => !v)}>
          {expanded ? 'Less' : 'More'}
        </button>
      )}
    </div>
  )
}

export default function CourseInfo({ description, prerequisites, onCourseClick }) {
  const tree = parsePrereqs(prerequisites)
  const hasPrereqs = Boolean(prerequisites?.trim())

  if (!description && !hasPrereqs) return null

  return (
    <div className="course-info">
      {description && <Description text={description} />}

      {hasPrereqs && (
        <dl className="course-reqs">
          <div className="req-row">
            <dt>Requires</dt>
            <dd>
              {tree ? (
                <Requirements tree={tree} onCourseClick={onCourseClick} />
              ) : (
                <span className="req-raw">{prerequisites}</span>
              )}
            </dd>
          </div>
        </dl>
      )}
    </div>
  )
}
