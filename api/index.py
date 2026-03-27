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

def _score_course_row(row, q: str) -> float:
    # Handle both tuple and sqlite3.Row
    crs = row['crs']
    title = row['title']
    instructors = row['instructors']

    q = q.strip().upper()
    short_code = " ".join(crs.split()[:2])
    title = title.upper()
    dept = crs.split()[0] if crs else ""

    if q == crs: return 100
    if q == short_code: return 90
    if q == title: return 90
    if q == dept: return 80

    if fuzz is not None:
        title_fuzz = fuzz.partial_ratio(q, title) * 0.5
        combined = f"{crs} {title} {instructors}".upper()
        global_fuzz = fuzz.token_set_ratio(q, combined) * 0.1
        return max(title_fuzz, global_fuzz)
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