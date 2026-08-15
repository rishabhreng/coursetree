import re
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

from fastapi import FastAPI, HTTPException, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import sqlite3 as sql
import requests as r
from bs4 import BeautifulSoup

from auth_utils import (
    require_client_id,
    store_client_session,
    clear_client_auth,
    ensure_authenticated_session,
    AUTH_SESSIONS,
    authenticate_with_duo,
)

from fetch_utils import (
    METADATA_COURSES_URL,
    find_eval_header,
    parse_eval_header_text,
    get_db,
    get_valid_term_codes,
    fetch_syllabus_pdf_with_session,
    is_pdf_response,
    extract_charts_from_results,
    split_instructor_names,
    get_instructor_ids,
)

from search_utils import (
    CoursesResponse,
    LoginRequest,
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


@app.post("/api/auth")
async def login(
    req: LoginRequest,
    x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id"),
):
    """Receives credentials from React and triggers headless Duo push."""
    try:
        client_id = require_client_id(x_client_id)

        session = await authenticate_with_duo(req.netid, req.password)
        store_client_session(client_id, session)

        return {"success": True, "message": "Successfully authenticated with ESTHER"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[AUTH] Unexpected login error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Authentication failed due to a server error.",
        )


@app.get("/api/auth/status")
async def auth_status(
    x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id")
):
    client_id = require_client_id(x_client_id)
    return {"authenticated": client_id in AUTH_SESSIONS}


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


@app.get("/api/subjects", response_model=List[Subject])
def get_subjects(db: sql.Connection = Depends(get_db)) -> List[Subject]:
    """Get all available subject codes with their full subject names."""
    try:
        cur = db.cursor()

        # Find all tables that look like 'subjects_XXXXXX'
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'subjects_%'"
        )
        subject_tables = [row[0] for row in cur.fetchall()]

        for table in subject_tables:
            rows = cur.execute(f"SELECT DISTINCT code, subject FROM {table}").fetchall()
            for code, subject in rows:
                code_upper = code.upper()
                VALID_SUBJECTS.add(code_upper)
                SUBJECT_NAMES[code_upper] = subject

        subjects = [
            Subject(code=code, subject=SUBJECT_NAMES.get(code, code))
            for code in sorted(VALID_SUBJECTS)
        ]
        return subjects
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch subjects: {str(e)}"
        )


@app.get("/api/syllabus", response_model=SyllabusResponse)
async def get_syllabus(
    term_code: str,
    crn: str,
    x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id"),
) -> SyllabusResponse:
    """
    Check if syllabus exists for a course.
    If it does, query PDF endpoint to fetch it via authenticated session.
    """
    try:
        client_id = require_client_id(x_client_id)

        # Check if syllabus exists via faster metadata api first (doesn't require auth):
        try:
            metadata_url = (
                f"{METADATA_COURSES_URL}?action=SYLLABUS&term={term_code}&crn={crn}"
            )
            metadata_response = r.get(metadata_url, timeout=15)
            metadata_response.raise_for_status()

            metadata = ET.fromstring(metadata_response.text)
            if metadata.attrib.get("has-syllabus") != "yes":
                return SyllabusResponse(syllabus_url=None, message="No syllabus posted for this term")
        except Exception as e:
            print(f"[ERROR] Could not check syllabus metadata: {str(e)}")
            return SyllabusResponse(syllabus_url=None, message="No syllabus posted for this term")

        try:
            session = await ensure_authenticated_session(client_id)

            # Fetch pdf with playwright session.
            response = fetch_syllabus_pdf_with_session(session, term_code, crn)
            response.raise_for_status()

            pdf_content = response.content

            # Check various failures before returning content.
            if not is_pdf_response(pdf_content):
                # Only re-authenticate when response appears to be login/auth-related.
                if not is_pdf_response(pdf_content):
                    print("[DEBUG] Auth expiry detected during syllabus fetch.")
                    clear_client_auth(client_id)
                    raise HTTPException(
                        status_code=401, detail="Authentication required"
                    )

                if not is_pdf_response(pdf_content):
                    raise HTTPException(
                        status_code=502,
                        detail="Failed to retrieve a valid syllabus PDF",
                    )

            # Return the PDF content directly
            return StreamingResponse(
                iter([pdf_content]),
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f"inline; filename=syllabus_{term_code}_{crn}.pdf"
                },
            )
        except HTTPException:
            raise
        except Exception as e:
            print(f"[ERROR] Error fetching syllabus PDF: {str(e)}")
            raise HTTPException(
                status_code=502, detail=f"Failed to fetch syllabus PDF: {str(e)}"
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Failed to check syllabus: {str(e)}"
        )


@app.get("/api/evaluate")
async def get_evaluation(
    term: str,
    crn: str,
    subject: str,
    x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id"),
):
    """
    Get course evaluation data. First call triggers Duo auth.
    Subsequent calls reuse stored session.

    Returns HTML of the results-container div.
    """
    try:
        client_id = require_client_id(x_client_id)

        session = await ensure_authenticated_session(client_id)

        # Check if term is valid
        valid_terms = get_valid_term_codes(session)
        if not valid_terms:
            raise HTTPException(status_code=401, detail="Authentication required")
        if term not in valid_terms:
            return {
                "success": False,
                "message": "No evaluation data found",
                "term": term,
                "crn": crn,
                "subject": subject.upper(),
            }

        # Use the stored session (which maintains cookies)
        url = "https://esther.rice.edu/selfserve/swkscmt.main"

        # First, GET the page to extract the as_fid token
        get_response = session.get(url, timeout=15)
        soup = BeautifulSoup(get_response.text, "html.parser")

        # Extract as_fid from form or page
        as_fid = None
        form = soup.find("form")
        if form:
            as_fid_input = form.find("input", {"name": "as_fid"})
            if as_fid_input:
                as_fid = as_fid_input.get("value", "")

        if not as_fid:
            # Try to extract from page source
            import re as regex_module

            match = regex_module.search(
                r'as_fid["\']?\s*[:=]\s*["\']?([a-f0-9]{40})', get_response.text
            )
            if match:
                as_fid = match.group(1)

        print(f"[DEBUG] as_fid: {as_fid}")

        payload = {
            "p_commentid": "",
            "p_confirm": "1",
            "p_term": term,
            "p_type": "Course",
            "p_crn": crn,
        }

        if as_fid:
            payload["as_fid"] = as_fid

        print(f"[DEBUG] Posting payload: {payload}")
        response = session.post(url, data=payload, timeout=15)
        print(f"[DEBUG] Response status: {response.status_code}")
        print(f"[DEBUG] Response length: {len(response.text)}")
        print(f"[DEBUG] Response preview: {response.text[:500]}")

        # Check if session is valid
        if "Course and Instructor Evaluation Display" not in response.text:
            print("[DEBUG] Session appears invalid.")
            clear_client_auth(client_id)
            raise HTTPException(status_code=401, detail="Authentication required")

        # Parse and extract results-container div
        soup = BeautifulSoup(response.text, "html.parser")
        results_container = soup.find("div", class_="results-container")

        charts_data = extract_charts_from_results(results_container)
        return {
            "success": True,
            "html": str(results_container),
            "charts": charts_data,
            "term": term,
            "crn": crn,
            "subject": subject.upper(),
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in evaluation endpoint: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch evaluation: {str(e)}"
        )


@app.get("/api/instructor-evaluate")
async def get_instructor_evaluation(
    term: str,
    crn: Optional[str] = None,
    instructor_names: Optional[str] = None,
    db: sql.Connection = Depends(get_db),
    x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id"),
):
    """
    Fetch instructor evaluations for a term and (optionally) filter by course CRN.
    Supports instructor IDs directly or instructor names resolved via the DB.
    """
    try:
        client_id = require_client_id(x_client_id)

        session = await ensure_authenticated_session(client_id)

        valid_terms = get_valid_term_codes(session)
        if not valid_terms:
            raise HTTPException(status_code=401, detail="Authentication required")
        if term not in valid_terms:
            return {
                "success": False,
                "message": "No evaluation data found",
                "term": term,
                "crn": crn,
            }

        requested_names = []
        if instructor_names:
            requested_names.extend(split_instructor_names(instructor_names))
        
        unique_ids = get_instructor_ids(term, requested_names, db)

        if not unique_ids:
            if requested_names:
                return {
                    "success": False,
                    "message": "No matching instructors found",
                    "term": term,
                    "crn": crn,
                    "results": [],
                    "missing_instructors": requested_names,
                }
            raise HTTPException(
                status_code=400,
                detail="Missing instructor_id(s) or instructor_name(s).",
            )
        url = "https://esther.rice.edu/selfserve/swkscmt.main"

        results = []

        for name, id in unique_ids.items():
            print(f"[DEBUG] Fetching evaluation for {name} ({id})")
            get_response = session.get(url, timeout=15)
            soup = BeautifulSoup(get_response.text, "html.parser")

            as_fid = None
            form = soup.find("form")
            if form:
                as_fid_input = form.find("input", {"name": "as_fid"})
                if as_fid_input:
                    as_fid = as_fid_input.get("value", "")

            if not as_fid:
                match = re.search(
                    r'as_fid["\']?\s*[:=]\s*["\']?([a-f0-9]{40})',
                    get_response.text,
                )
                if match:
                    as_fid = match.group(1)

            payload = {
                "p_commentid": "",
                "p_confirm": "1",
                "p_term": term,
                "p_type": "Instructor",
                "p_instr": id,
            }
            if as_fid:
                payload["as_fid"] = as_fid
            

            response = session.post(url, data=payload, timeout=15)

            if (
                "bmenu.P_MainMnu" not in response.text
                and "Personal Information" not in response.text
            ):
                clear_client_auth(client_id)
                raise HTTPException(status_code=401, detail="Authentication required")

            page = BeautifulSoup(response.text, "html.parser")
            sections = []
            for container in page.find_all("div", class_="results-container"):
                header = find_eval_header(container)
                header_text = header.get_text(" ", strip=True) if header else ""
                meta = parse_eval_header_text(header_text)
                charts = extract_charts_from_results(container)
                section = {
                    **meta,
                    "html": str(container),
                    "charts": charts,
                }
                print(section)
                sections.append(section)

            if crn:
                sections = [
                    section for section in sections
                    if section.get("crn") == crn or crn in (section.get("all_crns") or [])
                ]
            
            print(sections)

            results.append(
                {
                    "instructor_id": id,
                    "instructor_name": name,
                    "sections": sections,
                    "success": bool(sections),
                    "message": (
                        "No evaluation data found"
                        if not sections
                        else "Evaluation data found"
                    ),
                }
            )
        has_evals = any(result.get("success") for result in results)
        return {
            "success": has_evals,
            "term": term,
            "crn": crn,
            "results": results,
            "message": "Evaluation data found" if has_evals else "No evaluation data found",
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in instructor evaluation endpoint: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch instructor evaluation: {str(e)}"
        )
