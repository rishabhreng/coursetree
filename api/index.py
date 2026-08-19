import json
import re
from typing import List, Optional
from xml.etree import ElementTree as ET

from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import sqlite3 as sql
import requests as r

from fetch_utils import (
    SYLLABI_DIR,
    get_db,
    get_evals_db,
    get_instructor_evals_db,
)

from search_utils import (
    CoursesResponse,
    clean_query,
    convert_to_fts_query,
    group_courses,
    VALID_SUBJECTS,
)

DEFAULT_COURSE_TERM_CODE = "202710"


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # local frontend dev
        "https://ricecourses.vercel.app",  # deployed frontend
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUBJECT_NAMES = {}


@app.get("/api/courses/")
def search_courses(
    q = "",
    distribution_group = None,
    analyzing_diversity = False,
    term_code = DEFAULT_COURSE_TERM_CODE,
    term_start = None,
    term_end = None,
    top_n_results = None,
    offset = 0,
    db: sql.Connection = Depends(get_db),
):
    try:
        q_clean = clean_query(q) if q else ""
        fts_query = convert_to_fts_query(q_clean) if q_clean else ""

        dist_fts_clauses = []
        if analyzing_diversity:
            dist_fts_clauses.append('distribution : "Analyzing Diversity"')

        if distribution_group:
            dist_fts_clauses.append(f'distribution : "{distribution_group}"')

        dist_fts = ""
        if dist_fts_clauses:
            if len(dist_fts_clauses) == 1:
                dist_fts = dist_fts_clauses[0]
            else:
                dist_fts = f"({' AND '.join(dist_fts_clauses)})"

        if fts_query and dist_fts:
            combined_fts = f"({fts_query}) AND {dist_fts}"
        elif fts_query:
            combined_fts = fts_query
        elif dist_fts:
            combined_fts = dist_fts
        else:
            return {}

        # 1. Prepare base WHERE clause and parameters
        where_clause = "WHERE global_search MATCH ?"
        base_params = [combined_fts]

        if term_code and term_code != "all":
            where_clause += " AND term = ?"
            base_params.append(f"courses_{term_code}")
        else:
            start_code = term_start.strip() if term_start else None
            end_code = term_end.strip() if term_end else None
            if start_code == "all":
                start_code = None
            if end_code == "all":
                end_code = None

            if start_code or end_code:
                try:
                    start_int = int(start_code) if start_code else None
                    end_int = int(end_code) if end_code else None
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid term range")

                if start_int and end_int and start_int > end_int:
                    start_int, end_int = end_int, start_int

                if start_int and end_int:
                    where_clause += " AND CAST(REPLACE(term, 'courses_', '') AS INTEGER) BETWEEN ? AND ?"
                    base_params.extend([start_int, end_int])
                elif start_int:
                    where_clause += (
                        " AND CAST(REPLACE(term, 'courses_', '') AS INTEGER) >= ?"
                    )
                    base_params.append(start_int)
                elif end_int:
                    where_clause += (
                        " AND CAST(REPLACE(term, 'courses_', '') AS INTEGER) <= ?"
                    )
                    base_params.append(end_int)

        # 2. Determine secondary sort and specific CTE parameters
        if q_clean and re.match(r"^[A-Z]{2,5}\s*\d{1,4}[A-Z]?$", q_clean):
            # Exact/partial course code searches: use best search relevance
            secondary_sort = "best_search_rank ASC"
            cte_params = []

        elif q_clean and q_clean in VALID_SUBJECTS:
            # Subject-only searches: recency tier -> true numerical order
            secondary_sort = "CAST(SUBSTR(crs, ?) AS INTEGER) ASC"
            cte_params = [len(q_clean) + 2]

        else:
            # General keyword searches: use unified best_search_rank
            secondary_sort = "best_search_rank ASC"
            cte_params = []

        if top_n_results is not None and top_n_results > 0:
            rank_filter = "WHERE course_rank > ? AND course_rank <= ?"
            pagination_params = [offset, offset + top_n_results]
        else:
            rank_filter = "WHERE course_rank > ?"
            pagination_params = [offset]

        sql_query = f"""
            WITH RawFTS AS (
                SELECT *, 
                       bm25(global_search, 0.0, 5.0, 10.0, 0.0, 5.0, 2.0, 0.0, 0.2, 0.0, 0.0, 0.0) as search_rank 
                FROM global_search
                {where_clause}
            ),
            CourseStats AS (
                SELECT *,
                       -- Find the newest term for THIS course
                       MAX(CAST(REPLACE(term, 'courses_', '') AS INTEGER)) OVER (PARTITION BY crs) as course_max_term,
                       -- Find the absolute newest term across ALL matched courses
                       MAX(CAST(REPLACE(term, 'courses_', '') AS INTEGER)) OVER () as global_max_term,
                       -- NEW: Find the best search relevance for the entire course to prevent splitting ranks
                       MIN(search_rank) OVER (PARTITION BY crs) as best_search_rank
                FROM RawFTS
            ),
            RankedCourses AS (
                SELECT *,
                       DENSE_RANK() OVER (
                           ORDER BY 
                               -- Primary Sort: Rolling 2-year tiers
                               ((global_max_term - course_max_term) / 200) ASC, 
                               -- Secondary Sort: Injected based on search type
                               {secondary_sort},
                               crs ASC
                       ) as course_rank
                FROM CourseStats
            )
            -- 4. Select based on the unique course rank
            SELECT * FROM RankedCourses
            {rank_filter}
            ORDER BY course_rank ASC, term DESC
        """

        # Parameter binding order strictly follows the '?' placeholders in the query
        params = base_params + cte_params + pagination_params

        # 5. Execute and return
        rows = db.cursor().execute(sql_query, tuple(params)).fetchall()
        return group_courses(rows)

    except sql.Error as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Unexpected server error: {str(e)}"
        )


@app.get("/api/terms")
def get_terms(db = Depends(get_db)):
    return db.cursor().execute("SELECT code, term FROM terms ORDER BY code DESC").fetchall()

def find_local_syllabus_file(term_code: str, crn: str):
    """Finds downloaded syllabus file on disk for a given term and crn."""
    term_dir = SYLLABI_DIR / term_code
    if not term_dir.is_dir():
        return None
    matches = term_dir.glob(f"{crn}.*")
    for match in matches:
        if match.is_file():
            return match
    return None

@app.get("/api/syllabus")
async def get_syllabus(
    crn: str,
    term: str,
):
    """
    Serve the downloaded syllabus file (PDF, DOCX, DOC, TXT) with correct MIME type.
    """
    local_file = find_local_syllabus_file(term.replace("courses_", ""), crn)
    if not local_file:
        raise HTTPException(status_code=404, detail="Syllabus not found for this term")

    ext = local_file.suffix.lower()
    media_types = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".rtf": "application/rtf",
        ".txt": "text/plain; charset=utf-8",
        ".html": "text/html; charset=utf-8",
    }
    media_type = media_types.get(ext, "application/octet-stream")

    return FileResponse(
        path=str(local_file),
        media_type=media_type,
        filename=local_file.name,
    )


@app.get("/api/evaluate")
def get_evaluation(crn: str, term: str, subject: str, course_code: str, db = Depends(get_evals_db)):
    term = term.replace("courses_", "")

    cur = db.cursor()
    cur.execute(
        "SELECT html, charts_json, subject FROM evaluations WHERE term = ? AND crn = ?",
        (term, crn),
    )
    row = cur.fetchone()
    
    # Fallback to course_code matching if CRN not found directly (e.g. for cross-listed courses)
    if not row and course_code:
        cur.execute(
            "SELECT html, charts_json, subject FROM evaluations WHERE term = ? AND course_code = ? LIMIT 1",
            (term, course_code),
        )
        row = cur.fetchone()

    if not row:
        return {
            "success": False,
            "message": "No evaluation data found",
            "term": term,
            "crn": crn,
            "subject": (subject or "").upper(),
        }

    charts_data = json.loads(row["charts_json"]) if row["charts_json"] else []
    return {
        "success": True,
        "html": row["html"],
        "charts": charts_data,
        "term": term,
        "crn": crn,
        "subject": (row["subject"] or subject or "").upper(),
    }

@app.get("/api/instructor-evaluate")
def get_instructor_evaluation(
    term: str,
    crn: str,
    db: sql.Connection = Depends(get_instructor_evals_db)
):
    """
    Fetch instructor evaluations. Returns cached data if available.
    """
    term = term.replace("courses_", "")

    cur = db.cursor()
    cur.execute(
        "SELECT instructor_name, instructor_id, html, charts_json FROM instructor_evaluations WHERE term = ? AND crn = ?",
        (term, crn)
    )
    rows = cur.fetchall()
    
    results = []
    for row in rows:
        charts_data = json.loads(row["charts_json"]) if row["charts_json"] else []
        results.append({
            "instructor_name": row["instructor_name"],
            "instructor_id": row["instructor_id"],
            "html": row["html"],
            "charts": charts_data
        })

    if not results:
        return {
            "success": False,
            "term": term,
            "crn": crn,
            "results": [],
            "missing_instructors": [],
            "message": "No instructor evaluations found for this course",
        }

    return {
        "success": True,
        "term": term,
        "crn": crn,
        "results": results,
        "missing_instructors": [],
        "message": "Instructor evaluations loaded"
    }
