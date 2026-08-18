import json
import re
from pathlib import Path
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
import sqlite3 as sql
import requests as r
from bs4 import BeautifulSoup

from fetch_utils import (
    METADATA_COURSES_URL,
    SYLLABI_DIR,
    get_db,
    get_evals_db,
    get_instructor_evals_db,
)

from search_utils import (
    CoursesResponse,
    Subject,
    SyllabusResponse,
    Term,
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


@app.get("/api/courses/", response_model=CoursesResponse)
def search_courses(
    q: Optional[str] = "",
    distribution_group: Optional[str] = None,
    analyzing_diversity: Optional[bool] = False,
    distributions: Optional[List[str]] = Query(None),
    term_code: str = DEFAULT_COURSE_TERM_CODE,
    term_start: Optional[str] = None,
    term_end: Optional[str] = None,
    top_n_results: Optional[int] = None,
    offset: int = 0,
    db: sql.Connection = Depends(get_db),
) -> CoursesResponse:
    try:
        # Backward compatibility with distributions parameter list
        if distributions:
            for d in distributions:
                for item in d.split(","):
                    item_str = item.strip()
                    if item_str == "Analyzing Diversity Course":
                        analyzing_diversity = True
                    elif item_str in ["Distribution Group I", "Distribution Group II", "Distribution Group III"]:
                        distribution_group = item_str

        q_clean = clean_query(q) if q else ""
        fts_query = convert_to_fts_query(q_clean) if q_clean else ""

        dist_fts_clauses = []
        if analyzing_diversity:
            dist_fts_clauses.append('distribution : "Analyzing Diversity"')

        if distribution_group and distribution_group not in ["all", "none", ""]:
            if distribution_group == "Distribution Group I":
                dist_fts_clauses.append('distribution : "Distribution Group I"')
            elif distribution_group == "Distribution Group II":
                dist_fts_clauses.append('distribution : "Distribution Group II"')
            elif distribution_group == "Distribution Group III":
                dist_fts_clauses.append('distribution : "Distribution Group III"')
            else:
                escaped = distribution_group.replace('"', '""')
                dist_fts_clauses.append(f'distribution : "{escaped}"')

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
        if q_clean and re.match(r"^[A-Z]{4}\s*\d{3}$", q_clean):
            # Exact searches: use the unified best_search_rank so ranks aren't split
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
                       bm25(global_search) as search_rank 
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


@app.get("/api/terms", response_model=List[Term])
def get_terms(db: sql.Connection = Depends(get_db)) -> List[Term]:
    """Get all available terms from the database."""
    try:
        rows = (
            db.cursor()
            .execute("SELECT code, term FROM terms ORDER BY code DESC")
            .fetchall()
        )
        return [Term(code=row["code"], term=row["term"]) for row in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch terms: {str(e)}")

def find_local_syllabus_file(term: str, crn: str) -> Optional[Path]:
    """Finds downloaded syllabus file on disk for a given term and crn."""
    clean_term = term.replace("courses_", "")
    term_dir = SYLLABI_DIR / clean_term
    if not term_dir.is_dir():
        return None
    # Check pattern {crs}_{crn}.* and {crn}.*
    matches = list(term_dir.glob(f"*_{crn}.*")) + list(term_dir.glob(f"{crn}.*"))
    for match in matches:
        if match.is_file() and match.stat().st_size > 0:
            return match
    return None


@app.get("/api/syllabus", response_model=SyllabusResponse)
async def get_syllabus(
    crn: str,
    term: Optional[str] = Query(None),
    term_code: Optional[str] = Query(None),
) -> SyllabusResponse:
    """
    Check if syllabus exists for a course from downloaded syllabi archive or live metadata.
    """
    clean_term = (term or term_code or "").replace("courses_", "")
    if not clean_term:
        raise HTTPException(status_code=400, detail="Missing term parameter")

    local_file = find_local_syllabus_file(clean_term, crn)
    if local_file:
        ext = local_file.suffix.lower().lstrip(".")
        return SyllabusResponse(
            syllabus_url=f"/api/syllabus/file?term={clean_term}&crn={crn}",
            file_type=ext,
            filename=local_file.name,
            message="Syllabus available",
        )

    try:
        metadata_url = (
            f"{METADATA_COURSES_URL}?action=SYLLABUS&term={clean_term}&crn={crn}"
        )
        metadata_response = r.get(metadata_url, timeout=15)
        metadata_response.raise_for_status()

        metadata = ET.fromstring(metadata_response.text)
        if metadata.attrib.get("has-syllabus") == "yes":
            return SyllabusResponse(
                syllabus_url=f"http://esther.rice.edu/selfserve/!bwzkpsyl.v_viewDoc?term={clean_term}&crn={crn}&type=SYLLABUS",
                file_type="external",
                filename=None,
                message="Syllabus posted on ESTHER",
            )
        return SyllabusResponse(syllabus_url=None, file_type=None, filename=None, message="No syllabus posted for this term")
    except Exception as e:
        print(f"[ERROR] Could not check syllabus metadata: {str(e)}")
        return SyllabusResponse(syllabus_url=None, file_type=None, filename=None, message="Error checking for syllabus")


@app.get("/api/syllabus/file")
async def get_syllabus_file(
    crn: str,
    term: Optional[str] = Query(None),
    term_code: Optional[str] = Query(None),
):
    """
    Serve the downloaded syllabus file (PDF, DOCX, DOC, TXT) with correct MIME type.
    """
    clean_term = (term or term_code or "").replace("courses_", "")
    if not clean_term:
        raise HTTPException(status_code=400, detail="Missing term parameter")

    local_file = find_local_syllabus_file(clean_term, crn)
    if not local_file:
        raise HTTPException(status_code=404, detail="Syllabus file not found")

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
def get_evaluation(
    crn: str,
    term: Optional[str] = Query(None),
    term_code: Optional[str] = Query(None),
    subject: Optional[str] = Query(None),
    course_code: Optional[str] = Query(None),
    db: sql.Connection = Depends(get_evals_db),
):
    """
    Get cached course evaluation data from database.
    """
    clean_term = (term or term_code or "").replace("courses_", "")
    if not clean_term:
        return {"success": False, "message": "Missing term parameter"}

    try:
        cur = db.cursor()
        cur.execute(
            "SELECT html, charts_json, subject FROM evaluations WHERE term = ? AND crn = ?",
            (clean_term, crn),
        )
        row = cur.fetchone()
        
        # Fallback to course_code matching if CRN not found directly (e.g. for cross-listed courses)
        if not row and course_code:
            cur.execute(
                "SELECT html, charts_json, subject FROM evaluations WHERE term = ? AND course_code = ? LIMIT 1",
                (clean_term, course_code),
            )
            row = cur.fetchone()

        if not row:
            return {
                "success": False,
                "message": "No evaluation data found",
                "term": clean_term,
                "crn": crn,
                "subject": (subject or "").upper(),
            }

        charts_data = json.loads(row["charts_json"]) if row["charts_json"] else []
        return {
            "success": True,
            "html": row["html"],
            "charts": charts_data,
            "term": clean_term,
            "crn": crn,
            "subject": (row["subject"] or subject or "").upper(),
        }

    except Exception as e:
        print(f"Error in evaluation endpoint: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch evaluation: {str(e)}"
        )


@app.get("/api/instructor-evaluate")
def get_instructor_evaluation(
    term: Optional[str] = Query(None),
    term_code: Optional[str] = Query(None),
    crn: Optional[str] = Query(None),
    instructor_names: Optional[str] = Query(None),
    db: sql.Connection = Depends(get_db),
    evals_db: sql.Connection = Depends(get_instructor_evals_db)
):
    """
    Fetch instructor evaluations. Returns cached data if available.
    """
    clean_term = (term or term_code or "").replace("courses_", "")
    if not clean_term or not crn:
        return {"success": False, "message": "Missing term or crn"}

    try:
        cur_evals = evals_db.cursor()
        cur_evals.execute(
            "SELECT instructor_name, instructor_id, html, charts_json FROM instructor_evaluations WHERE term = ? AND crn = ?",
            (clean_term, crn)
        )
        rows = cur_evals.fetchall()
        
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
                "term": clean_term,
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

    except Exception as e:
        print(f"Error in instructor evaluation endpoint: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch instructor evaluations: {str(e)}"
        )

