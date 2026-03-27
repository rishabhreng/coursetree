import os
import re
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
import sqlite3 as sql

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'courses.db')
print(f"Checking DB at: {DB_PATH}")
try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None

from pydantic import BaseModel

DEFAULT_COURSE_TERM_CODE = '202710'

# Common acronym/abbreviation mappings to expand search queries
ACRONYM_MAP = {
    'UG': 'UNDERGRADUATE',
    'GRAD': 'GRADUATE',
}

class Course(BaseModel):
    term: str
    crn: str
    crs: str
    title: str
    instructors: str
    meeting_times: str
    credits: str
    course_page: Optional[str] = None
    score: Optional[float] = None

class Term(BaseModel):
    code: str
    term: str

app = FastAPI()

# UPDATE: Added potential production URL
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://your-app-name.vercel.app"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- DATABASE DEPENDENCY ---
def get_db():
    # In Vercel, we open in read-only mode to be safe/efficient
    # check_same_thread=False is required for SQLite + FastAPI
    conn = sql.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sql.Row
    try:
        yield conn
    finally:
        conn.close()

def _term_table(term_code: str) -> str:
    term_code = term_code.strip()
    if not re.fullmatch(r"\d{6}", term_code):
        raise ValueError("term_code must be 6 digits")
    return f"courses_{term_code}"

def _expand_acronyms(text: str) -> str:
    """Expand common course-related acronyms in search text"""
    text_upper = text.upper()
    for acronym, expansion in ACRONYM_MAP.items():
        # Replace acronyms when they appear as whole words
        text_upper = re.sub(r'\b' + acronym + r'\b', expansion, text_upper)
    return text_upper

def _match_title_with_gaps(query: str, title: str) -> float:
    """
    Match query against title allowing words to be skipped.
    E.g., "honors algebra" matches "honors linear algebra"
    Returns score 0-100
    """
    query_upper = query.upper().strip()
    title_upper = title.upper().strip()
    
    # Exact match
    if query_upper == title_upper:
        return 100
    
    # Substring match
    if query_upper in title_upper:
        return 90
    
    # Token set ratio (handles word reordering)
    token_score = fuzz.token_set_ratio(query_upper, title_upper)
    if token_score > 85:
        return token_score
    
    # Word-by-word matching with gap tolerance
    query_words = query_upper.split()
    title_words = title_upper.split()
    
    if len(query_words) == 0:
        return 0
    
    # Try to match query words sequentially in title, allowing gaps
    matched_words = 0
    title_idx = 0
    
    for query_word in query_words:
        found = False
        while title_idx < len(title_words):
            # Use fuzzy ratio for word matching (allows typos)
            word_match = fuzz.ratio(query_word, title_words[title_idx])
            if word_match >= 80:  # 80% match is good enough for a word
                matched_words += 1
                title_idx += 1
                found = True
                break
            title_idx += 1
        
        if not found:
            # Word not found - try partial match
            best_partial = 0
            for i in range(len(title_words)):
                partial = fuzz.partial_ratio(query_word, title_words[i])
                if partial > best_partial:
                    best_partial = partial
            
            if best_partial > 70:
                return best_partial * 0.8  # Partial match is less confident
            # Word not found at all - lower the score
            if matched_words == 0:
                return 0  # First word didn't match
    
    # Calculate score based on word match ratio
    if len(query_words) > 0:
        word_match_ratio = matched_words / len(query_words)
        # Give bonus if all words matched
        if word_match_ratio == 1.0:
            return 85 + (title_idx / len(title_words)) * 10  # Bonus if matches span most of title
        else:
            return word_match_ratio * 70
    
    return token_score
    """Expand common course-related acronyms in search text"""
    text_upper = text.upper()
    for acronym, expansion in ACRONYM_MAP.items():
        # Replace acronyms when they appear as whole words
        text_upper = re.sub(r'\b' + acronym + r'\b', expansion, text_upper)
    return text_upper

def _match_instructor_name(query: str, instructor_str: str) -> float:
    """
    Smart instructor name matching that handles:
    - Query: "Joe Young" or "Young, Joe" 
    - Database: "Young, Joseph" or other variations
    Returns a score 0-100 based on match quality
    """
    if not instructor_str or not query:
        return 0
    
    query_upper = query.upper().strip()
    instructor_upper = instructor_str.upper().strip()
    
    # Direct exact match
    if query_upper == instructor_upper:
        return 100
    
    # Direct substring match
    if query_upper in instructor_upper or instructor_upper in query_upper:
        return 85
    
    # Try token set ratio on full strings first (handles reordered names)
    full_match_score = fuzz.token_set_ratio(query_upper, instructor_upper)
    if full_match_score > 80:
        return full_match_score
    
    # Try partial ratio for substrings
    partial_match_score = fuzz.partial_ratio(query_upper, instructor_upper)
    if partial_match_score > 85:
        return partial_match_score
    
    # Parse query: expect "FirstName LastName" or "LastName, FirstName"
    query_parts = query_upper.split(',')
    if len(query_parts) == 2:
        # Query is in "LastName, FirstName" format
        query_last = query_parts[0].strip()
        query_first_words = query_parts[1].strip().split()
    else:
        # Query is in "FirstName LastName" format
        words = query_upper.split()
        if len(words) >= 2:
            query_first_words = words[:-1]  # All but last word
            query_last = words[-1]     # Last word
        else:
            query_first_words = []
            query_last = words[0] if words else ""
    
    # Parse database entry: expect "LastName, FirstName" format
    db_parts = instructor_upper.split(',')
    if len(db_parts) == 2:
        db_last = db_parts[0].strip()
        db_first_parts = db_parts[1].strip().split()
    else:
        # Fallback if not in standard format - try direct token matching
        return full_match_score
    
    # Get first names (might be multiple due to middle names)
    db_first = db_first_parts[0] if db_first_parts else ""
    query_first_str = ' '.join(query_first_words) if query_first_words else ""
    
    # Score: match last name + first name (allowing for nicknames / abbreviations)
    last_name_score = fuzz.ratio(query_last, db_last) if query_last else 0
    first_name_score = fuzz.ratio(query_first_str, db_first) if query_first_str and db_first else 0
    
    # Both parts should match reasonably for a good score
    if last_name_score >= 80:
        # Last name matches well
        if query_first_str and db_first:
            if first_name_score >= 60:
                # First name matches (allowing for nicknames like Joe≈Joseph)
                return (last_name_score * 0.7 + first_name_score * 0.3)
            else:
                # First name doesn't match well - still give credit for last name
                return last_name_score * 0.7
        elif not query_first_str:
            # Query only has last name - high match
            return last_name_score
        else:
            # Query has first name but DB first name missing - partial match
            return last_name_score * 0.6
    
    return full_match_score

def _score_course_row(row, q: str) -> float:
    # Handle both tuple and sqlite3.Row
    crs = row['crs']
    title = row['title']
    instructors = row['instructors']
    crn = row['crn']

    q = q.strip().upper()
    
    # Check if query is a 5-digit CRN - these get top priority (100+)
    if re.fullmatch(r'\d{5}', q):
        if q == crn:
            return 100
        return 0  # Non-matching CRN queries shouldn't match other courses
    
    # Expand acronyms in both query and course data for better matching
    q_expanded = _expand_acronyms(q)
    
    short_code = " ".join(crs.split()[:2])
    title_upper = title.upper()
    title_expanded = _expand_acronyms(title)
    dept = crs.split()[0] if crs else ""

    # Exact matches have highest priority
    if q == crs: 
        return 99
    if q == short_code: 
        return 95
    if q == title_upper: 
        return 90
    
    # Exact department match is very high priority (e.g., "MATH" -> MATH courses)
    if q == dept: 
        return 92
    
    # Department prefix match (e.g., "MA" -> MATH, "ST" -> STAT)
    if dept and len(q) >= 2 and dept.startswith(q):
        return 85
    
    # Course code prefix match (e.g., user types "MATH 2" looking for MATH 2xx)
    if short_code.startswith(q):
        return 80

    # Fuzzy matching with multiple strategies
    if fuzz is not None:
        scores = {}
        
        # Strategy 1: Course code fuzzy match (codes are important)
        crs_score = fuzz.token_set_ratio(q_expanded, crs)
        scores['crs'] = crs_score * 60 / 100  # Scale to 0-60
        
        # Strategy 2: Title match with word gap tolerance
        title_score = _match_title_with_gaps(q_expanded, title_expanded)
        scores['title'] = title_score * 30 / 100  # Scale to 0-30
        
        # Strategy 3: Instructor name match (now properly weighted)
        instructor_score = 0
        if instructors:
            instructor_score = _match_instructor_name(q_expanded, instructors)
            scores['instructor'] = instructor_score * 25 / 100  # Scale to 0-25
        
        # Return the best combination
        # Prefer the highest single score, but allow combinations
        return max(
            scores.get('crs', 0),
            scores.get('title', 0) + scores.get('instructor', 0) * 0.3,
            scores.get('instructor', 0)
        )
    return 0

def _group_by_course_code(scored_rows, term_code) -> Dict[str, List[Course]]:
    grouped: Dict[str, List[Course]] = {}
    for row, score in scored_rows:
        course_code = " ".join(row['crs'].split()[:2]).upper()
        course_obj = Course(
            term=term_code,
            crn=row['crn'],
            crs=row['crs'],
            title=row['title'],
            instructors=row['instructors'],
            meeting_times=row['meeting_times'] or "",
            credits=row['credits'] or "",
            course_page=row['course_page'] if 'course_page' in row.keys() else None,
            score=score,
        )
        grouped.setdefault(course_code, []).append(course_obj)

    for entries in grouped.values():
        entries.sort(key=lambda x: (int(x.term), x.score or 0), reverse=True)

    return dict(sorted(grouped.items(), key=lambda kv: max(int(e.term) for e in kv[1]), reverse=True))

@app.get("/api/courses/", response_model=Dict[str, List[Course]])
def search_courses(
    q: str, 
    term_code: str = DEFAULT_COURSE_TERM_CODE, 
    top_n_results: int = 15,
    db: sql.Connection = Depends(get_db)
) -> Dict[str, List[Course]]:
    try:
        table = _term_table(term_code)
        cur = db.cursor()
        like_q = f"%{q}%"
        cur.execute(
            f"SELECT * FROM {table} WHERE (crs LIKE ? OR title LIKE ? OR instructors LIKE ?)",
            (like_q, like_q, like_q),
        )
        courses = cur.fetchall()
        scored = [(row, _score_course_row(row, q)) for row in courses]
        scored.sort(key=lambda x: x[1], reverse=True)
        return _group_by_course_code(scored[:top_n_results], term_code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/courses/all", response_model=Dict[str, List[Course]])
def search_all_courses(
    q: str, 
    top_n_results: int = 15,
    db: sql.Connection = Depends(get_db)
) -> Dict[str, List[Course]]:
    cur = db.cursor()
    course_tables = [row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'courses_%'").fetchall()]

    merged: Dict[str, List[Course]] = {}
    for table_name in course_tables:
        term = table_name.replace("courses_", "")
        # Reusing the search logic directly to keep things DRY
        like_q = f"%{q}%"
        cur.execute(f"SELECT * FROM {table_name} WHERE (crs LIKE ? OR title LIKE ? OR instructors LIKE ?)", (like_q, like_q, like_q))
        
        scored = [(row, _score_course_row(row, q)) for row in cur.fetchall()]
        term_results = _group_by_course_code(scored[:top_n_results], term)
        
        for code, courses in term_results.items():
            merged.setdefault(code, []).extend(courses)

    # Sort final merged results
    for code in merged:
        merged[code].sort(key=lambda c: (int(c.term), c.score or 0), reverse=True)
    
    return dict(sorted(merged.items(), key=lambda kv: max(int(c.term) for c in kv[1]), reverse=True))

@app.get("/api/terms", response_model=List[Term])
def get_terms(db: sql.Connection = Depends(get_db)) -> List[Term]:
    """Get all available terms from the terms database"""
    TERMS_DB_PATH = os.path.join(BASE_DIR, 'terms.db')
    try:
        terms_conn = sql.connect(f"file:{TERMS_DB_PATH}?mode=ro", uri=True, check_same_thread=False)
        terms_conn.row_factory = sql.Row
        cur = terms_conn.cursor()
        cur.execute("SELECT code, term FROM terms ORDER BY code DESC")
        rows = cur.fetchall()
        terms_conn.close()
        return [Term(code=row['code'], term=row['term']) for row in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch terms: {str(e)}")