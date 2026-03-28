import os
import re
from collections import defaultdict
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
import sqlite3 as sql

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve_db_path(filename: str) -> str:
    candidates = [
        os.path.join(BASE_DIR, filename),
        os.path.join(API_DIR, filename),
        os.path.join(os.getcwd(), filename),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


def _validate_sqlite_path(path: str, label: str) -> str:
    if not os.path.exists(path):
        raise RuntimeError(f"{label} database file not found at {path}")

    with open(path, 'rb') as f:
        header = f.read(64)

    if header.startswith(b'SQLite format 3\x00'):
        return path

    # Git LFS pointer files are plain text and often show up in serverless deploys.
    if header.startswith(b'version https://git-lfs.github.com/spec/v1'):
        raise RuntimeError(
            f"{label} at {path} is a Git LFS pointer, not a SQLite file. "
            "Ensure Vercel has access to actual LFS objects or commit this DB directly."
        )

    raise RuntimeError(f"{label} at {path} is not a valid SQLite database file")


DB_PATH = _validate_sqlite_path(_resolve_db_path('courses.db'), 'Courses')
TERMS_DB_PATH = _validate_sqlite_path(_resolve_db_path('terms.db'), 'Terms')
SUBJECTS_DB_PATH = _validate_sqlite_path(_resolve_db_path('subjects.db'), 'Subjects')
print(f"Using courses DB at: {DB_PATH}")
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
    meeting_times: Optional[str] = None
    credits: Optional[str] = None
    course_page: Optional[str] = None


class Term(BaseModel):
    code: str
    term: str


CoursesResponse = Dict[str, List[Course]]

app = FastAPI()

# UPDATE: Added potential production URL
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://your-app-name.vercel.app"],
    allow_methods=["*"],
    allow_headers=["*"],
)

VALID_SUBJECTS = set()
with sql.connect(f"file:{SUBJECTS_DB_PATH}?mode=ro", uri=True) as con:
    cur = con.cursor()

# Find all tables that look like 'subjects_XXXXXX'
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'subjects_%'")
subject_tables = [row[0] for row in cur.fetchall()]

for table in subject_tables:
    # Add every 4-letter code found to our global set
    rows = cur.execute(f"SELECT code FROM {table}").fetchall()
    for r in rows:
        VALID_SUBJECTS.add(r[0].upper())

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
        
def _clean_query(q: str) -> str:
    """Utility function to clean and standardize the search query"""
    q = q.strip().upper()
    # Expand common acronyms
    for acronym, full in ACRONYM_MAP.items():
        if q == acronym:
            return full
    return q

def _make_fts_query(q: str) -> str:
    """Convert a cleaned query into an FTS5 query string based on its format"""
    # CASE 1: CRN (5 Digits)
    if len(q) == 5 and q.isdigit():
        return f"crn : {q}"

    # CASE 2: Course Code (COMP 140 or COMP140)
    elif re.match(r'^[A-Z]{4}\s*\d{3}$', q):
        match = re.search(r'([A-Z]{4})\s*(\d{3})', q)
        dpt, num = match.group(1), match.group(2)
        return f'crs : "{dpt} {num}"'

    # CASE 3: Subject/Dept Only (match against VALID_SUBJECTS)
    elif len(q) == 4 and q.isalpha() and q in VALID_SUBJECTS:
        return f"crs : {q}*"

    # CASE 4: Course Number Only (140)
    elif len(q) == 3 and q.isdigit():
        return f"crs : {q}"

    # CASE 5: general fuzzy search
    else:
        words = q.split()
        if not words: return ""
        # Standard multi-word prefix search across all columns
        return " AND ".join([f"{w}*" for w in words])


def _is_specific_course_query(q: str) -> bool:
    # e.g. MATH354 or MATH 354
    return bool(re.match(r'^[A-Z]{4}\s*\d{3}$', q))


def _term_table_name(term_code: str) -> str:
    return f"courses_{term_code}"


def _term_code_from_table_name(term_value: str) -> str:
    if term_value.startswith('courses_'):
        return term_value.split('courses_', 1)[1]
    return term_value


def _normalize_course_page(course_page: Optional[str], term_code: str, crn: str) -> str:
    fallback = (
        f"https://courses.rice.edu/courses/courses/!SWKSCAT.cat?"
        f"p_action=COURSE&p_term={term_code}&p_crn={crn}"
    )
    if not course_page:
        return fallback

    normalized = course_page.replace('https://courses.rice.edu//', 'https://courses.rice.edu/')
    normalized = normalized.replace('/admweb/!SWKSCAT.cat?', '/courses/courses/!SWKSCAT.cat?')
    if 'courses.rice.edu' not in normalized:
        return fallback
    return normalized


def _load_course_details(db: sql.Connection, table_name: str, crn: str) -> Dict[str, Optional[str]]:
    # Guard dynamic table name to avoid unsafe SQL interpolation.
    if not re.match(r'^courses_\d{6}$', table_name):
        return {}

    cur = db.cursor()
    row = cur.execute(
        f"SELECT instructors, meeting_times, credits, course_page FROM {table_name} WHERE crn = ? LIMIT 1",
        (crn,),
    ).fetchone()
    return dict(row) if row else {}


def _row_to_course(row: sql.Row, db: sql.Connection) -> Course:
    row_dict = dict(row)
    term_value = row_dict.get('term', '')
    term_code = _term_code_from_table_name(term_value)
    crn = row_dict.get('crn', '')

    details = _load_course_details(db, term_value, crn)
    return Course.model_validate(
        {
            'term': term_code,
            'crn': crn,
            'crs': row_dict.get('crs'),
            'title': row_dict.get('title'),
            'instructors': details.get('instructors') or row_dict.get('instructors') or 'TBA',
            'meeting_times': details.get('meeting_times'),
            'credits': details.get('credits'),
            'course_page': _normalize_course_page(details.get('course_page'), term_code, crn),
        }
    )


def _group_courses(rows: List[sql.Row], db: sql.Connection) -> CoursesResponse:
    grouped: Dict[str, List[Course]] = defaultdict(list)
    for row in rows:
        course = _row_to_course(row, db)
        course_code = course.crs or f"{course.term}-{course.crn}"
        grouped[course_code].append(course)
    return dict(grouped)



@app.get("/api/courses/", response_model=CoursesResponse)
def search_courses(
    q: str, 
    term_code: str = DEFAULT_COURSE_TERM_CODE, 
    top_n_results: int = 50,
    db: sql.Connection = Depends(get_db)
) -> CoursesResponse:
    try:
        q = _clean_query(q)
        print(f"Cleaned query: '{q}'")
        fts_query = _make_fts_query(q)
        print(f"Generated FTS query: '{fts_query}'")

        if not fts_query:
            return {}

        # --- EXECUTION ---
        sql_query = "SELECT * FROM global_search WHERE global_search MATCH ?"
        params = [fts_query]

        if term_code != "all":
            sql_query += " AND term = ?"
            params.append(_term_table_name(term_code))

        # For exact course-code searches, prioritize newest term first.
        if _is_specific_course_query(q):
            sql_query += (
                " ORDER BY CAST(REPLACE(term, 'courses_', '') AS INTEGER) DESC, "
                "bm25(global_search) ASC LIMIT ?"
            )
        else:
            sql_query += (
                " ORDER BY bm25(global_search) ASC, "
                "CAST(REPLACE(term, 'courses_', '') AS INTEGER) DESC LIMIT ?"
            )
        params.append(top_n_results)
        print(f"Final SQL Query: '{sql_query}' with params {params}")
        cur = db.cursor()
        rows = cur.execute(sql_query, tuple(params)).fetchall()
        return _group_courses(rows, db)
    except sql.Error as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected server error: {str(e)}")


@app.get("/api/terms", response_model=List[Term])
def get_terms() -> List[Term]:
    """Get all available terms from the terms database."""
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