// Parses a list field from the API. Values arrive either as a JSON array
// string ('["A", "B"]') or as a comma/semicolon separated string.
export const parseList = (value) => {
  if (!value || value === 'TBA' || value === '[]') return ['TBA']
  const trimmed = String(value).trim()
  if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
    try {
      const parsed = JSON.parse(trimmed)
      if (Array.isArray(parsed)) {
        const items = parsed.map((item) => String(item).trim()).filter(Boolean)
        return items.length > 0 ? items : ['TBA']
      }
    } catch {
      // fall through to delimiter parsing
    }
  }
  const items = trimmed.split(/,|;\s*/).map((s) => s.trim()).filter(Boolean)
  return items.length > 0 ? items : ['TBA']
}

export const isTba = (value) => String(value || '').trim().toUpperCase() === 'TBA'

export const getInstructorNames = (course) => parseList(course.instructors).filter((name) => name && !isTba(name))

export const sectionKey = (course) => `${course.term}-${course.crn}`

export const formatCredits = (credits) => {
  if (!credits) return null
  const singular = parseInt(credits, 10) === 1 && !String(credits).includes(' ')
  return `${credits} ${singular ? 'credit' : 'credits'}`
}

export const coursePageUrl = (course) =>
  `https://courses.rice.edu/courses/courses/!SWKSCAT.cat?p_action=COURSE&p_term=${course.term}&p_crn=${course.crn}`

const escapeHtml = (text) => text
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')
  .replace(/'/g, '&#039;')

// Extracts student comments from the evaluation page HTML and renders them
// as escaped comment cards.
export const formatEvalHtml = (html) => {
  if (!html) return ''

  try {
    const parser = new DOMParser()
    const doc = parser.parseFromString(html, 'text/html')

    // Remove header elements, chart containers, images, fillers, and navigation links
    doc.querySelectorAll('.results-header, .charts, .chart, img, .filler, a[class*="submit-crn"]').forEach((el) => el.remove())
    doc.querySelectorAll('a').forEach((a) => {
      if (/report a concern/i.test(a.textContent) || /go to course eval/i.test(a.textContent)) {
        a.remove()
      }
    })
    doc.querySelectorAll('div, p, span, td, tr').forEach((el) => {
      if (/Class Mean\s*-\s*Average score/i.test(el.textContent) || /Rice Mean\s*-\s*Average score/i.test(el.textContent)) {
        el.remove()
      }
    })

    const totalMatch = doc.body.textContent.match(/Total Comments:\s*\d+/i)
    const totalText = totalMatch ? totalMatch[0] : ''

    const cmtElements = doc.querySelectorAll('.cmt')
    const dateRegex = /\d{2}\/\d{2}\/\d{4}\s+\d{1,2}:\d{2}\s*(?:[AP]\.?M\.?)/i
    const items = []

    if (cmtElements.length > 0) {
      cmtElements.forEach((cmt) => {
        const dateMatch = cmt.textContent.match(dateRegex)
        const dateStr = dateMatch ? dateMatch[0] : ''

        const clone = cmt.cloneNode(true)
        clone.querySelectorAll('a').forEach((a) => a.remove())
        clone.querySelectorAll('br').forEach((br) => br.replaceWith('\n'))
        clone.querySelectorAll('p, div').forEach((el) => el.append('\n'))

        let text = clone.textContent || ''
        if (dateStr) {
          text = text.replace(dateRegex, '')
        }
        text = text
          .replace(/report a concern/gi, '')
          .split('\n')
          .map((s) => s.trim())
          .filter(Boolean)
          .join('\n')
          .trim()

        if (text) {
          items.push({ text, date: dateStr })
        }
      })
    } else {
      // Fallback line-by-line parsing on remaining text
      const tempDiv = doc.createElement('div')
      tempDiv.innerHTML = doc.body.innerHTML
        .replace(/<br\s*\/?>/gi, '\n')
        .replace(/<\/(p|div|tr|td|li|h\d)>/gi, '\n')

      const rawText = tempDiv.textContent || ''
      const lines = rawText.split('\n').map((s) => s.trim()).filter(Boolean)
      const dateRegexGlobal = /\d{2}\/\d{2}\/\d{4}\s+\d{1,2}:\d{2}\s*(?:[AP]\.?M\.?)/gi

      let currentParts = []
      lines.forEach((line) => {
        if (/student comments/i.test(line) || /total comments/i.test(line)) return
        if (/^term:\s*/i.test(line) || /^course\(s\):\s*/i.test(line) || /^instructor\(s\):\s*/i.test(line) || /^enrolled:\s*/i.test(line) || /go to course eval/i.test(line)) return

        const dateMatch = line.match(dateRegexGlobal)
        if (dateMatch) {
          const remaining = line.replace(dateRegexGlobal, '').trim()
          if (remaining) {
            currentParts.push(remaining)
          }
          const fullCommentText = currentParts.join('\n').trim()
          if (fullCommentText) {
            items.push({ text: fullCommentText, date: dateMatch[0] })
          }
          currentParts = []
        } else {
          currentParts.push(line)
        }
      })
    }

    if (items.length > 0) {
      let out = '<div class="comments-container"><div class="comments-header">'
      out += '<h4 class="comments-title">Student Comments</h4>'
      if (totalText) {
        out += `<span class="comments-count">${escapeHtml(totalText)}</span>`
      }
      out += '</div><div class="comments-list">'
      items.forEach((item) => {
        out += '<div class="comment-card">'
        out += `<div class="comment-text">${escapeHtml(item.text).replace(/\n/g, '<br/>')}</div>`
        out += `<div class="comment-date">${escapeHtml(item.date)}</div>`
        out += '</div>'
      })
      out += '</div></div>'
      return out
    }
  } catch (err) {
    console.error('Error formatting comments:', err)
  }

  return ''
}

// ---- Prerequisites ----

const PREREQ_TOKEN_RE = /[A-Z]{3,4} \d{3}[A-Z]?|\(|\)|\bAND\b|\bOR\b/g

// Parses Rice prerequisite expressions such as "(MATH 101 OR MATH 105) AND COMP 140"
// into a tree: { type: 'course', code } | { type: 'and' | 'or', items: [...] }.
// AND binds tighter than OR. Returns null for empty or unrecognized text.
export const parsePrereqs = (text) => {
  const source = String(text || '').trim()
  if (!source) return null
  const tokens = source.match(PREREQ_TOKEN_RE) || []
  // Refuse anything containing text we don't understand.
  if (tokens.join('').replace(/\s/g, '') !== source.replace(/\s/g, '')) return null

  let pos = 0
  const combine = (type, items) => {
    const flat = items.flatMap((item) => (item.type === type ? item.items : [item]))
    return flat.length === 1 ? flat[0] : { type, items: flat }
  }
  const parseAtom = () => {
    const token = tokens[pos++]
    if (token === '(') {
      const node = parseOr()
      if (tokens[pos++] !== ')') throw new Error('unbalanced')
      return node
    }
    if (token && /\d/.test(token)) return { type: 'course', code: token }
    throw new Error('unexpected token')
  }
  const parseAnd = () => {
    const items = [parseAtom()]
    while (tokens[pos] === 'AND') { pos++; items.push(parseAtom()) }
    return combine('and', items)
  }
  function parseOr() {
    const items = [parseAnd()]
    while (tokens[pos] === 'OR') { pos++; items.push(parseAnd()) }
    return combine('or', items)
  }

  try {
    const tree = parseOr()
    return pos === tokens.length ? tree : null
  } catch {
    return null
  }
}
