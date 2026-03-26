import re
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import sqlite3 as sql

try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None

app = FastAPI()
app.mount('/static', StaticFiles(directory='static'), name='static')

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


@app.get("/debug/")
def debug_ui():
    # if you have Vite running at 5173 during local development, redirect there.
    # Otherwise, keep using the /search endpoints through your API client.
    return RedirectResponse(url="http://127.0.0.1:5173/")

@app.get("/debug-html", response_class=HTMLResponse)
def debug_ui() -> str:
    return """<!DOCTYPE html>
<html lang=\"en\">
<head>
<meta charset=\"UTF-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
<title>Course Search Debug UI</title>
<style>
:root { --bg:#0a1224; --panel:#122040; --text:#f4f8ff; --muted:#9ab5d2; --primary:#68a7ff; }
* { box-sizing:border-box; }
body { margin:0; font-family:Inter, Arial, sans-serif; color:var(--text); background:radial-gradient(circle at 20% 0%, #2c4d8a 0%, var(--bg) 70%); }
.container { max-width:1000px; margin:1.8rem auto; padding:1rem; }
h1 { margin:0 0 0.25rem; }
p.desc { margin:0 0 1rem; color:var(--muted); }
.form { display:grid; grid-template-columns:1fr 1fr 1fr auto auto; gap:0.8rem; margin-bottom:1rem; }
.form input, .form button { border:1px solid rgba(255,255,255,0.1); border-radius:10px; padding:0.7rem; font-size:0.95rem; background:rgba(22,36,64,0.7); color:var(--text); }
.form button { font-weight:600; background:var(--primary); color:white; }
.card { background:rgba(12,23,43,.8); border:1px solid rgba(255,255,255,0.08); border-radius:14px; padding:0.8rem; margin-bottom:10px; }
.details > summary { list-style:none; cursor:pointer; font-weight:700; padding:0.8rem; border-radius:10px; color:#ecf5ff; background:rgba(0,0,0,.15); }
.details .section-list { padding:0.8rem; display:grid; grid-template-columns:1fr; gap:0.6rem; }
.section-card { background:rgba(255,255,255,.05); border:1px solid rgba(255,255,255,.1); border-radius:10px; padding:0.7rem; }
.section-card h4 { margin:0 0 0.2rem; font-size:1rem; color:#d2e8ff; }
.section-card p { margin:0.2rem 0; color:var(--muted); font-size:0.88rem; }
.pill { display:inline-block; background:rgba(84,150,255,0.22); padding:2px 8px; border-radius:999px; margin-right:4px; font-size:0.78rem; }
.link-btn { display:inline-block; margin-top:6px; color:#a8d0ff; text-decoration:none; }
</style>
</head>
<body>
<div class=\"container\">
  <h1>Course Search Debug (TS Modern)</h1>
  <p class=\"desc\">Search courses in your database and explore results by course code.</p>
  <div class=\"form\">
    <input id=\"q\" type=\"text\" placeholder=\"Query e.g. COMP 182\" value=\"COMP 182\" />
    <input id=\"term\" type=\"text\" placeholder=\"Term code e.g. 202710\" value=\"202710\" />
    <input id=\"top\" type=\"number\" min=\"1\" value=\"20\" />
    <button id=\"search\" type=\"button\">Search</button>
    <button id=\"searchAll\" type=\"button\">Search All</button>
  </div>
  <div id=\"result\"></div>
</div>
<script type=\"module\" src=\"/static/debug.ts\"></script>
</body>
</html>"""

@app.get("/search/")
def search(q: str, term_code: str = '202710', top_n_results: int = 15) -> dict:
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
def get_course(crn: str, term_code: str = '202710') -> dict:
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