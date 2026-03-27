import os
import re
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import sqlite3 as sql

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# the database exists under backend/courses.db in this repo layout
DB_PATH = os.path.join(BASE_DIR, 'courses.db')
DB_PATH = os.path.abspath(DB_PATH)

try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None

from pydantic import BaseModel

DEFAULT_COURSE_TERM_CODE = '202710'  # Fall 2026

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    conn = sql.connect(DB_PATH)
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


def _score_course_row(row: tuple, q: str) -> float:
    # a row is (crn, crs, title, instructors, meeting_times, credits)
    _, crs, title, instructors, *_ = row

    q = q.strip().upper()
    short_code = " ".join(crs.split()[:2])
    title = title.upper()
    dept = crs.split()[0] if crs else ""

    if q == crs:
        return 100
    if q == short_code:
        return 90
    if q == title:
        return 90

    if q == dept:
        return 80
    if q.startswith(dept) or dept.startswith(q):
        return 75

    m = re.search(r"\d{3}", crs)
    if m and q == m.group():
        return 40

    if q in instructors.upper():
        return 30

    if fuzz is not None:
        title_fuzz = fuzz.partial_ratio(q, title) * 0.5
        combined = f"{crs} {title} {instructors}".upper()
        global_fuzz = fuzz.token_set_ratio(q, combined) * 0.1
        return max(title_fuzz, global_fuzz)

    return 0


def _group_by_course_code(scored_rows, term_code) -> Dict[str, List[Course]]:
    grouped: Dict[str, List[Course]] = {}
    for row, score in scored_rows:
        crn, crs, title, instructors, meeting_times, credits = row[:6]
        course_page = row[6] if len(row) > 6 else None
        course_code = " ".join(crs.split()[:2]).upper()
        course_obj = Course(
            term=term_code,
            crn=crn,
            crs=crs,
            title=title,
            instructors=instructors,
            meeting_times=meeting_times or "",
            credits=credits or "",
            course_page=course_page,
            score=score,
        )
        grouped.setdefault(course_code, []).append(course_obj)

    # Make sure most recent term appears first within each course bucket
    for entries in grouped.values():
        entries.sort(key=lambda x: (int(x.term), x.score or 0), reverse=True)

    # Also return the course-code map ordered by most recent term overall
    ordered = dict(
        sorted(
            grouped.items(),
            key=lambda kv: max(int(e.term) for e in kv[1]) if kv[1] else 0,
            reverse=True,
        )
    )

    return ordered


# fuzzy search for courses by title, department, course code, or instructor
@app.get("/api/courses/", response_model=Dict[str, List[Course]])
def search_courses(q: str, term_code: str = DEFAULT_COURSE_TERM_CODE, top_n_results: int = 15) -> Dict[str, List[Course]]:
    term_code = term_code.strip()
    table = _term_table(term_code)

    with sql.connect(DB_PATH) as con:
        cur = con.cursor()

    like_q = f"%{q}%"
    cur.execute(
        f"""
        SELECT * FROM {table}
        WHERE (crs LIKE ? OR title LIKE ? OR instructors LIKE ?)
        """,
        (like_q, like_q, like_q),
    )

    courses = cur.fetchall()

    # score and group results
    scored = [(row, _score_course_row(row, q)) for row in courses]
    scored.sort(key=lambda x: x[1], reverse=True)

    top = scored[:top_n_results]
    return _group_by_course_code(top, term_code)

@app.get("/api/courses/all", response_model=Dict[str, List[Course]])
def search_all_courses(q: str, top_n_results: int = 15) -> Dict[str, List[Course]]:
    # search across all terms and merge by course code
    with sql.connect(DB_PATH) as con:
        cur = con.cursor()
    course_dbs: List[str] = [row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'courses_%'").fetchall()]

    merged: Dict[str, List[Course]] = {}
    for table_name in course_dbs:
        term = table_name.replace("courses_", "")
        try:
            term_results = search_courses(q=q, term_code=term, top_n_results=top_n_results)
        except HTTPException:
            continue

        for course_code, course_list in term_results.items():
            merged.setdefault(course_code, []).extend(course_list)

    for course_list in merged.values():
        course_list.sort(key=lambda c: (int(c.term), c.score or 0), reverse=True)

    ordered = dict(
        sorted(
            merged.items(),
            key=lambda kv: max(int(c.term) for c in kv[1]) if kv[1] else 0,
            reverse=True,
        )
    )

    return ordered

