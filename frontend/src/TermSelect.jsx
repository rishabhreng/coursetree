import { useEffect, useMemo, useRef, useState } from 'react'

export default function TermSelect({ id, value, onChange, options, placeholder = 'Select term' }) {
  const [isOpen, setIsOpen] = useState(false)
  const [openUpward, setOpenUpward] = useState(false)
  const [filterText, setFilterText] = useState('')
  const [highlightedIndex, setHighlightedIndex] = useState(0)
  const dropdownRef = useRef(null)
  const optionsListRef = useRef(null)
  const inputRef = useRef(null)
  const listboxId = `${id}-listbox`
  const optionId = (idx) => `${id}-option-${idx}`

  const selectedOption = options.find((opt) => opt.code === value)
  const currentLabel = selectedOption ? selectedOption.term : (value === 'all' ? 'All terms' : value)

  // Opens the list with an empty filter, flipping upward when there's no room below.
  const openList = () => {
    if (dropdownRef.current) {
      const rect = dropdownRef.current.getBoundingClientRect()
      setOpenUpward(window.innerHeight - rect.bottom < 260 && rect.top > 260)
    }
    setIsOpen(true)
    setFilterText('')
    setHighlightedIndex(0)
  }

  const updateFilter = (text) => {
    setFilterText(text)
    setHighlightedIndex(0)
    if (!isOpen) setIsOpen(true)
  }

  const filteredOptions = useMemo(() => {
    if (!isOpen || !filterText.trim()) return options
    const query = filterText.toLowerCase().trim()
    const tokens = query.split(/\s+/).filter(Boolean)

    const matches = options.filter((opt) => {
      const combined = `${opt.term.toLowerCase()} ${opt.code.toLowerCase()}`
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
    if (isOpen && optionsListRef.current) {
      const highlightedEl = optionsListRef.current.children[highlightedIndex]
      highlightedEl?.scrollIntoView({ block: 'nearest' })
    }
  }, [highlightedIndex, isOpen])

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const handleSelect = (optionCode) => {
    onChange(optionCode)
    setIsOpen(false)
    inputRef.current?.blur()
  }

  const handleKeyDown = (e) => {
    if (!isOpen) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter') {
        openList()
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
      inputRef.current?.blur()
    }
  }

  const showOptions = isOpen && filteredOptions.length > 0

  return (
    <div className="term-select-container" ref={dropdownRef}>
      <div className="term-select-input-wrap">
        <input
          id={id}
          ref={inputRef}
          type="text"
          role="combobox"
          aria-expanded={isOpen}
          aria-controls={listboxId}
          aria-autocomplete="list"
          aria-activedescendant={showOptions ? optionId(highlightedIndex) : undefined}
          className="term-select-input"
          value={isOpen ? filterText : currentLabel}
          placeholder={placeholder}
          onFocus={(e) => {
            openList()
            e.target.select()
          }}
          onChange={(e) => updateFilter(e.target.value)}
          onKeyDown={handleKeyDown}
          autoComplete="off"
        />
        <span className={`term-select-arrow ${isOpen ? 'open' : ''}`} aria-hidden="true">▾</span>
      </div>

      {isOpen && (
        <div
          id={listboxId}
          role="listbox"
          className={`term-select-dropdown ${openUpward ? 'open-upward' : ''}`}
          ref={optionsListRef}
        >
          {filteredOptions.length > 0 ? (
            filteredOptions.map((opt, idx) => (
              <div
                key={opt.code}
                id={optionId(idx)}
                role="option"
                aria-selected={opt.code === value}
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
