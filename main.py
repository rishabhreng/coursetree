from calendar import c
import code
from typing import List

from fastapi import FastAPI
import re
import sqlite3 as sql

from pyproj import CRS

try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None

app = FastAPI()

# Ranking:
# 1) exact course code match (e.g. "COMP 182")
# 2) course number match (e.g. "182")
# 3) fuzzy match on title
# 4) fuzzy match on instructors
def _score_course_row(row: tuple, q: str) -> float:
    # a row is (crn, crs, title, instructors, meeting_times, credits)
    _, crs, title, instructors, *_ = row

    q = q.strip().upper()
    short_code = " ".join(crs.split()[:2]) # e.g., "COMP 182"
    title = title.upper()
    dept = crs.split()[0] # e.g., "COMP"

    # exact matches (exit immediately)
    if q == crs: return 100
    if q == short_code: return 90
    if q == title: return 90

    # match by department code (e.g. "COMP" should match "COMP 182")
    if q == dept:
        return 80
    # allow partial department matching (e.g. "COMP" for "COMPX" or "COMP" for "COMP 182")
    if q.startswith(dept) or dept.startswith(q):
        return 75

    # check if query is just the course number
    course_num = re.search(r'\d{3}', crs).group()
    if q == course_num: return 40

    # check if instructors match exactly
    if q in instructors.upper(): return 30

    # fuzzy search title
    title_fuzz = fuzz.partial_ratio(q, title) * 0.5 
    
    # fuzzy search fallback on everything
    combined = f"{crs} {title} {instructors}".upper()
    global_fuzz = fuzz.token_set_ratio(q, combined) * 0.1

    return max(title_fuzz, global_fuzz)


@app.get("/search/")
def search(q: str, term_code: str = '202620', full_year: bool = False, top_n_results: int = 50) -> List[tuple]:
    term_code = term_code.strip()

    con = sql.connect(f'courses.db')
    cur = con.cursor()

    # matches subject, number, title, or instructors
    like_q = f"%{q}%"
    cur.execute(
        """
        SELECT * FROM courses_202620
        WHERE (crs LIKE ? OR title LIKE ? OR instructors LIKE ?)
        """,
        (like_q, like_q, like_q),
    )

    candidates = cur.fetchall()
    con.commit()
    con.close()

    # Score and sort candidates
    scored = [(row, _score_course_row(row, q)) for row in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Return top-N (convert to dicts if you want JSON keys)
    return [r for r, _ in scored[:top_n_results]]
