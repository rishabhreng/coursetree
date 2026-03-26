import re
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import sqlite3 as sql

try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None

app = FastAPI()

# Allow the frontend to call the API from a different origin during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


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


def _group_by_course_code(scored_rows, term_code):
    grouped = {}
    for row, score in scored_rows:
        crn, crs, title, instructors, meeting_times, credits = row[:6]
        course_page = row[6] if len(row) > 6 else None
        course_code = " ".join(crs.split()[:2]).upper()
        entry = {
            "term": term_code,
            "score": score,
            "crn": crn,
            "crs": crs,
            "title": title,
            "instructors": instructors,
            "meeting_times": meeting_times,
            "credits": credits,
            "course_page": course_page,
        }
        grouped.setdefault(course_code, []).append(entry)

    # Make sure most recent term appears first within each course bucket
    for entries in grouped.values():
        entries.sort(key=lambda x: (int(x["term"]), x["score"]), reverse=True)

    # Also return the course-code map ordered by most recent term overall
    ordered = dict(
        sorted(
            grouped.items(),
            key=lambda kv: max(int(e["term"]) for e in kv[1]) if kv[1] else 0,
            reverse=True,
        )
    )

    return ordered


@app.get("/search/")
def search(q: str, term_code: str = '202620', top_n_results: int = 15) -> dict:
    term_code = term_code.strip()
    table = _term_table(term_code)

    con = sql.connect('courses.db')
    cur = con.cursor()

    like_q = f"%{q}%"
    cur.execute(
        f"""
        SELECT * FROM {table}
        WHERE (crs LIKE ? OR title LIKE ? OR instructors LIKE ?)
        """,
        (like_q, like_q, like_q),
    )

    candidates = cur.fetchall()
    con.close()

    scored = [(row, _score_course_row(row, q)) for row in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)

    top = scored[:top_n_results]
    return _group_by_course_code(top, term_code)


@app.get("/course/{crn}")
def get_course(crn: str, term_code: str = '202620') -> dict:
    table = _term_table(term_code)
    con = sql.connect('courses.db')
    con.row_factory = sql.Row
    cur = con.cursor()

    cur.execute(f"SELECT * FROM {table} WHERE crn = ?", (crn,))
    row = cur.fetchone()
    con.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Course not found")

    return dict(row)

@app.get("/searchall")
def search_all(q: str, top_n_results: int = 15) -> dict:
    # search for a course name across all terms, returning the most recent ones first
    # only return exact matches for course code (i.e. MATH 331)

    con = sql.connect('courses.db')
    cur = con.cursor()

    course_dbs = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'courses_%'").fetchall()
    all_results = []
    grouped = {}
    
    for db in course_dbs:
        table = db[0]
        like_q = f"%{q}%"
        cur.execute(
            f"""
            SELECT * FROM {table}
            WHERE (crs LIKE ? OR title LIKE ? OR instructors LIKE ?)
            """,
            (like_q, like_q, like_q),
        )
        candidates = cur.fetchall()
        term = table.replace("courses_", "")
        scored = [(row, _score_course_row(row, q)) for row in candidates]
        scored.sort(key=lambda x: x[1], reverse=True)

        for row_score in scored[:top_n_results]:
            row, score = row_score
            crs = row[1]
            course_code = " ".join(crs.split()[:2]).upper()
            course_page = row[6] if len(row) > 6 else None
            entry = {
                "term": term,
                "score": score,
                "crn": row[0],
                "crs": row[1],
                "title": row[2],
                "instructors": row[3],
                "meeting_times": row[4],
                "credits": row[5],
                "course_page": course_page,
            }
            if course_code not in grouped:
                grouped[course_code] = []
            grouped[course_code].append(entry)

    # per-course bucket ordering by most recent term, then score
    for entries in grouped.values():
        entries.sort(key=lambda x: (int(x.get("term", 0)), x.get("score", 0)), reverse=True)

    ordered = dict(
        sorted(
            grouped.items(),
            key=lambda kv: max(int(e.get("term", 0)) for e in kv[1]) if kv[1] else 0,
            reverse=True,
        )
    )

    return ordered