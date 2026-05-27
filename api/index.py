import os
import re
from collections import defaultdict
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET
from urllib.parse import parse_qs, urlparse, unquote

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import sqlite3 as sql
import requests as r
from requests import Session
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError

from pydantic import BaseModel
from pathlib import Path
import logging

# =================
# ===== SETUP =====
# =================

l = logging.getLogger("course_api")
logging.basicConfig(level=logging.INFO)
DB_PATH = Path.cwd().parent / "main.db"
l.info(f"Using main DB at: {DB_PATH}")

DEFAULT_COURSE_TERM_CODE = "202710"
METADATA_COURSES_URL = "https://courses.rice.edu/courses/!SWKSCAT.info"
SYLLABUS_BASE_URL = "https://esther.rice.edu/selfserve/!bwzkpsyl.v_viewDoc"

# Common acronym/abbreviation mappings to expand search queries
ACRONYM_MAP = {
    "UG": "UNDERGRADUATE",
    "GRAD": "GRADUATE",
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


class Subject(BaseModel):
    code: str
    subject: str


class SyllabusResponse(BaseModel):
    syllabus_url: Optional[str] = None
    message: str


class LoginRequest(BaseModel):
    netid: str
    password: str


CoursesResponse = Dict[str, List[Course]]

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        # "http://localhost:3000",
        "http://localhost:5173",  # local frontend dev
        "https://ricecourses.vercel.app",  # deployed frontend
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

VALID_SUBJECTS = set()
SUBJECT_NAMES = {}


# ======================
# ===== AUTH LOGIC =====
# ======================

AUTH_SESSIONS: Dict[str, Session] = {}


def _require_client_id(client_id: Optional[str]) -> str:
    if not client_id:
        raise HTTPException(status_code=400, detail="Missing X-Client-Id header")
    return client_id


def _store_client_session(client_id: str, session: Session) -> None:
    AUTH_SESSIONS[client_id] = session


def _clear_client_auth(client_id: str) -> None:
    AUTH_SESSIONS.pop(client_id, None)


async def _ensure_authenticated_session(client_id: str) -> Session:
    session = AUTH_SESSIONS.get(client_id)
    if session is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return session


@app.post("/api/auth")
async def login(
    req: LoginRequest,
    x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id"),
):
    """Receives credentials from React and triggers headless Duo push."""
    try:
        client_id = _require_client_id(x_client_id)
        # DO NOT log the password here, even for debugging!
        l.info(f"[AUTH] Received login request for NetID: {req.netid}")

        # Call the updated headless Playwright function from earlier
        session = await _authenticate_with_duo(req.netid, req.password)
        _store_client_session(client_id, session)

        return {"success": True, "message": "Successfully authenticated with ESTHER"}
    except HTTPException as e:
        l.error(f"[AUTH ERROR] {str(e)}")
        raise
    except Exception as e:
        l.error(f"[AUTH ERROR] {str(e)}")
        raise HTTPException(
            status_code=401,
            detail="Authentication failed. Did you approve the Duo push?",
        )


@app.get("/api/auth/status")
async def auth_status(
    x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id")
):
    client_id = _require_client_id(x_client_id)
    return {"authenticated": client_id in AUTH_SESSIONS}


def _is_pdf_response(content: bytes) -> bool:
    return content.startswith(b"%PDF")


# TODO: eventually remove for same reason
def _looks_like_direct_link_block(content: bytes) -> bool:
    sample = content[:4000].lower()
    return (
        b"direct link" in sample
        or b"direct-link" in sample
        or b"access denied" in sample
        or b"not authorized" in sample
    )


def _fetch_syllabus_pdf_with_session(session: Session, term_code: str, crn: str):
    params = {
        "term": term_code,
        "type": "SYLLABUS",
        "crn": crn,
    }

    return session.get(SYLLABUS_BASE_URL, params=params, timeout=15, stream=True)


# TODO: remove eventually
def _bootstrap_selfserve_context(session: Session) -> None:
    """Warm key self-serve pages that commonly establish routing/session context."""
    session.get("https://esther.rice.edu/selfserve/", timeout=15)
    session.get("https://esther.rice.edu/selfserve/swkscmt.main", timeout=15)


def get_db():
    # uri needed to enable read-only mode, check_same_thread=False allows connection across multiple threads
    conn = sql.connect(
        f"file:{DB_PATH}?mode=ro", timeout=15.0, uri=True, check_same_thread=False
    )
    conn.row_factory = sql.Row
    try:
        yield conn
    finally:
        conn.close()


def _clean_query(q: str) -> str:
    """Utility function to clean and standardize the search query"""
    q = q.strip().upper()  # Normalize whitespace
    q = re.sub(r"\.", "", q) 
    q = re.sub(r",", "", q) 
    # Expand common acronyms
    for (
        acronym,
        full,
    ) in ACRONYM_MAP.items():  # TODO: maybe remove this, doesn't add much value
        if q == acronym:
            return full
    return q


def _row_to_course(row: sql.Row) -> Course:
    row_dict = dict(row)
    term_value = row_dict.get("term", "")
    term_code = term_value.split("courses_", 1)[1]

    data = {
        "term": term_code,
        "crn": row_dict.get("crn"),
        "crs": row_dict.get("crs"),
        "title": row_dict.get("title"),
        "instructors": row_dict.get("instructors") or "TBA",
        "meeting_times": row_dict.get("meeting_times"),
        "credits": row_dict.get("credits"),
        "course_page": row_dict.get("course_page"),
    }
    return Course(**data)


def _group_courses(rows: List[sql.Row]) -> CoursesResponse:
    grouped: Dict[str, List[Course]] = defaultdict(list)
    for row in rows:
        course = _row_to_course(row)
        course_code = course.crs or f"{course.term}-{course.crn}"
        grouped[course_code].append(course)
    return dict(grouped)


def _convert_to_fts_query(q: str) -> str:
    """Convert a cleaned query into an FTS5 query string based on its format"""
    # CASE 1: CRN (5 Digits)
    if len(q) == 5 and q.isdigit():
        return f"crn : {q}"

    # CASE 2: Course Code (e.g. COMP 140 or COMP140)
    elif re.match(r"^[A-Z]{4}\s*\d{3}$", q):
        match = re.search(r"([A-Z]{4})\s*(\d{3})", q)
        dpt, num = match.group(1), match.group(2)
        return f'crs : "{dpt} {num}"'

    # CASE 3: Subject/Dept Only (match against VALID_SUBJECTS)
    elif len(q) == 4 and q.isalpha() and q in VALID_SUBJECTS:
        return f"crs : {q}"

    # CASE 4: Course Number Only (140)
    elif len(q) == 3 and q.isdigit():
        return f"crs : {q}"

    # CASE 5: general fuzzy search
    else:
        # Replace hyphens with spaces to handle hyphenated names/terms
        q_normalized = q.replace("-", " ")
        words = q_normalized.split()
        if not words:
            return ""
        # Standard multi-word prefix search across all columns
        return " AND ".join([f"{w}*" for w in words])


import re
from fastapi import Depends, HTTPException
import sqlite3 as sql

# Assuming other necessary imports/constants are present


@app.get("/api/courses/", response_model=CoursesResponse)
def search_courses(
    q: str,
    term_code: str = DEFAULT_COURSE_TERM_CODE,
    term_start: Optional[str] = None,
    term_end: Optional[str] = None,
    top_n_results: int = 50,
    offset: int = 0,
    db: sql.Connection = Depends(get_db),
) -> CoursesResponse:
    try:
        q = _clean_query(q)
        fts_query = _convert_to_fts_query(q)
        if not fts_query:
            return {}

        # 1. Prepare base WHERE clause and parameters
        where_clause = "WHERE global_search MATCH ?"
        base_params = [fts_query]

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
        if re.match(r"^[A-Z]{4}\s*\d{3}$", q):
            # Exact searches: use the unified best_search_rank so ranks aren't split
            secondary_sort = "best_search_rank ASC"
            cte_params = []

        elif q in VALID_SUBJECTS:
            # Subject-only searches: recency tier -> true numerical order
            secondary_sort = "CAST(SUBSTR(crs, ?) AS INTEGER) ASC"
            cte_params = [len(q) + 2]

        else:
            # General keyword searches: use unified best_search_rank
            secondary_sort = "best_search_rank ASC"
            cte_params = []

        # 3. Build the universal CTE query
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
                               {secondary_sort}
                       ) as course_rank
                FROM CourseStats
            )
            -- 4. Select and paginate based on the unique course rank
            SELECT * FROM RankedCourses
            WHERE course_rank > ? AND course_rank <= ?
            ORDER BY course_rank ASC, term DESC
        """

        # Parameter binding order strictly follows the '?' placeholders in the query
        params = base_params + cte_params + [offset, offset + top_n_results]

        # 5. Execute and return
        rows = db.cursor().execute(sql_query, tuple(params)).fetchall()
        return _group_courses(rows)

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
        client_id = _require_client_id(x_client_id)
        l.info(
            f"Received syllabus request for term {term_code} and CRN {crn} from client {client_id}"
        )

        # Check if syllabus exists via faster metadata api first (doesn't require auth):
        try:
            metadata_url = (
                f"{METADATA_COURSES_URL}?action=SYLLABUS&term={term_code}&crn={crn}"
            )
            metadata_response = r.get(metadata_url, timeout=15)
            metadata_response.raise_for_status()

            metadata = ET.fromstring(metadata_response.text)
            if metadata.attrib.get("has-syllabus") != "yes":
                return SyllabusResponse(syllabus_url=None, message="No syllabus posted")
        except Exception as e:
            print(f"[ERROR] Could not check syllabus metadata: {str(e)}")
            return SyllabusResponse(syllabus_url=None, message="No syllabus posted")

        try:
            session = await _ensure_authenticated_session(client_id)

            # Fetch pdf with playwright session.
            response = _fetch_syllabus_pdf_with_session(session, term_code, crn)
            response.raise_for_status()

            pdf_content = response.content

            l.info(
                f"Response size: {len(pdf_content)} bytes, content-type: {response.headers.get('content-type', 'unknown')}"
            )

            # Check various failures before returning content.
            if not _is_pdf_response(pdf_content):
                l.info(
                    f"Response doesn't look like a PDF. First 100 bytes: {pdf_content[:100]}"
                )
                # Some responses are direct-link/context failures, not true auth expiry.
                if _looks_like_direct_link_block(pdf_content):
                    l.info(
                        "[DEBUG] Syllabus direct-link block detected. Bootstrapping selfserve context..."
                    )
                    _bootstrap_selfserve_context(session)
                    response = _fetch_syllabus_pdf_with_session(session, term_code, crn)
                    response.raise_for_status()
                    pdf_content = response.content
                    l.info(
                        f"After context bootstrap - Response size: {len(pdf_content)} bytes"
                    )

                # Only re-authenticate when response appears to be login/auth-related.
                if not _is_pdf_response(pdf_content):
                    print("[DEBUG] Auth expiry detected during syllabus fetch.")
                    _clear_client_auth(client_id)
                    raise HTTPException(
                        status_code=401, detail="Authentication required"
                    )

                if not _is_pdf_response(pdf_content):
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


async def _authenticate_with_duo(netid: str, password: str):
    """
    Headless authentication: Server types credentials, user approves on phone.
    """
    async with async_playwright() as p:
        l.info(f"Launching headless browser for user: {netid}...")
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://esther.rice.edu/")

        try:
            l.info("Entering credentials...")
            await page.fill("#username", netid)
            await page.fill("#password", password)
            # await page.click("button[name='eventId_proceed']")
            await page.keyboard.press("Enter")

            # Check if credentials are wrong before waiting on Duo.
            error_messages = [
                "The NetID you entered cannot be identified",
                "The password you entered was incorrect",
            ]
            try:
                await page.wait_for_function(
                    """(errors) => {
                        const text = document.body ? document.body.innerText : "";
                        return errors.some((msg) => text.includes(msg));
                    }""",
                    arg=error_messages,
                    timeout=4000,
                )
                l.error("Invalid credentials provided.")
                raise Exception("Invalid credentials")
            except TimeoutError:
                pass

            # 2. The Duo Push Phase
            # At this point, Rice's system will automatically send a Duo push to the user's phone.
            l.info(
                "Credentials submitted. Waiting for user to approve Duo push on their phone..."
            )

            # We wait up to 60 seconds for them to tap "Approve" on their phone
            try:
                await page.wait_for_selector(
                    "text='Personal Information'", timeout=60000
                )
            except TimeoutError:
                # Re-check for invalid credentials in case the error rendered late.
                content = await page.content()
                if any(msg in content for msg in error_messages):
                    l.error("Invalid credentials provided.")
                    raise Exception("Invalid credentials")
                raise Exception("Duo push timed out")
            l.info("Duo authentication successful!")

        except Exception as e:
            print("[AUTH] Failed! Taking screenshot of the browser state...")
            await page.screenshot(path="debug_duo_error.png", full_page=True)
            await browser.close()
            if str(e) == "Invalid credentials":
                raise HTTPException(status_code=401, detail="Invalid NetID or password")
            if str(e) == "Duo push timed out":
                raise HTTPException(
                    status_code=408,
                    detail="Duo push timed out. Please approve the request and try again.",
                )
            raise HTTPException(
                status_code=401,
                detail="Authentication failed. Did you approve the Duo push?",
            )

        # Extract cookies
        cookies = await context.cookies()
        cookie_dict = {cookie["name"]: cookie["value"] for cookie in cookies}

        await browser.close()

    # Create session after auth and load cookies into it
    session = Session()
    for name, value in cookie_dict.items():
        session.cookies.set(name, value)

    # Prime selfserve cookies
    try:
        _bootstrap_selfserve_context(session)
    except Exception as e:
        print(f"[WARN] Failed to warm selfserve session after Duo auth: {str(e)}")

    return session


def _get_valid_term_codes(session: Session) -> set:
    """Fetch and parse valid term codes from Rice's API using active auth session."""
    try:
        terms_url = "https://esther.rice.edu/selfserve/!swkscmp.ajax?p_data=TERMS"
        response = session.get(terms_url, timeout=15)

        # Parse XML response
        root = ET.fromstring(response.text)
        term_codes = set()

        for term_elem in root.findall(".//TERM"):
            code = term_elem.get("CODE")
            if code:
                term_codes.add(code)

        return term_codes
    except Exception as e:
        print(f"[ERROR] Error fetching term codes: {str(e)}")
        return set()


def _extract_chart_data(img_src: str, response_count: int = None) -> Optional[Dict]:
    """Extract raw percentage chart data from ObjectPlanet chart servlet URL."""
    try:
        parsed_url = urlparse(img_src)
        params = parse_qs(parsed_url.query)

        # Extract values and labels
        values_str = params.get("sampleValues", [""])[0]
        labels_str = params.get("sampleLabels", [""])[0]
        title = unquote(params.get("chartTitle", [""])[0])

        if not values_str or not labels_str:
            return None

        # These values are already percentages from the URL.
        percentage_values = [int(x) for x in values_str.split(",") if x.isdigit()]

        # Labels are comma-separated with \n for line breaks
        labels = []
        for label in labels_str.split(","):
            # Decode URL encoding and replace \n with space
            decoded = unquote(label).replace("\n", " ").strip()
            if decoded:
                labels.append(decoded)

        if not percentage_values or not labels:
            return None

        return {
            "title": title,
            "values": percentage_values,
            "labels": labels,
            "total": response_count if response_count else sum(percentage_values),
        }
    except Exception as e:
        print(f"[ERROR] Error extracting chart data: {str(e)}")
        return None


def _extract_charts_from_results(results_container) -> List[Dict]:
    charts_data = []
    if not results_container:
        return charts_data

    chart_divs = results_container.find_all("div", class_="chart")
    for chart_div in chart_divs:
        filler = chart_div.find("div", class_="filler")
        response_count = None

        if filler:
            filler_text = filler.get_text()
            responses_match = re.search(r"Responses:\s*(\d+)", filler_text)
            if responses_match:
                response_count = int(responses_match.group(1))

        img = chart_div.find("img")
        if img:
            src = img.get("src", "")
            if "ChartServlet" in src:
                chart_data = _extract_chart_data(src, response_count)
                if chart_data:
                    charts_data.append(chart_data)

    return charts_data


def _find_eval_header(container):
    for prev in container.previous_elements:
        if not hasattr(prev, "get_text"):
            continue
        if getattr(prev, "name", None) not in {"table", "div"}:
            continue
        text = prev.get_text(" ", strip=True)
        if "Course(s):" in text and "Term:" in text:
            return prev
    return None


def _extract_label_value(text: str, label: str) -> Optional[str]:
    pattern = rf"{re.escape(label)}\s*(.+?)(?=\s+(?:Term:|Course\(s\):|Enrolled:|Instructor\(s\):)|$)"
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _parse_eval_header_text(text: str) -> Dict[str, Optional[str]]:
    term = _extract_label_value(text, "Term:")
    course = _extract_label_value(text, "Course(s):")
    enrolled = _extract_label_value(text, "Enrolled:")
    instructors = _extract_label_value(text, "Instructor(s):")

    crn = None
    course_code = None
    section = None

    if course:
        crn_match = re.search(r"\((\d{4,6})\)", course)
        if crn_match:
            crn = crn_match.group(1)

        code_match = re.search(r"\b([A-Z]{2,5})\s*(\d{3})\s*(\d{3})?\b", course)
        if code_match:
            course_code = f"{code_match.group(1)} {code_match.group(2)}"
            if code_match.group(3):
                section = code_match.group(3)

    return {
        "term_label": term,
        "course": course,
        "enrolled": enrolled,
        "instructors": instructors,
        "crn": crn,
        "course_code": course_code,
        "section": section,
    }


def _normalize_instructor_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip()).lower()


def _split_instructor_names(raw: str) -> List[str]:
    if not raw:
        return []
    if "|" in raw:
        parts = raw.split("|")
    else:
        parts = [raw]
    # eliminate middle initial for matching
    return [re.sub(r"\b [A-Z]\.", " ", name).strip() for name in parts]


def _resolve_instructor_ids_by_name(
    term_code: str, names: List[str], db: sql.Connection
) -> Dict[str, str]:
    if not names:
        return {}

    table = f"instructors_{term_code}"
    cur = db.cursor()
    exists = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone()
    if not exists:
        return {}

    normalized = [_normalize_instructor_name(name) for name in names]
    placeholders = ",".join(["?"] * len(normalized))
    rows = cur.execute(
        f"SELECT name, id FROM {table} WHERE lower(name) IN ({placeholders})",
        tuple(normalized),
    ).fetchall()

    mapping = {}
    for row in rows:
        mapping[_normalize_instructor_name(row["name"])] = row["id"]

    return mapping


def _lookup_instructor_name(
    term_code: str, instructor_id: str, db: sql.Connection
) -> Optional[str]:
    table = f"instructors_{term_code}"
    cur = db.cursor()
    exists = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone()
    if not exists:
        return None

    row = cur.execute(
        f"SELECT name FROM {table} WHERE id = ?",
        (instructor_id,),
    ).fetchone()
    return row["name"] if row else None


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
        client_id = _require_client_id(x_client_id)

        session = await _ensure_authenticated_session(client_id)

        # Check if term is valid
        valid_terms = _get_valid_term_codes(session)
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
            _clear_client_auth(client_id)
            raise HTTPException(status_code=401, detail="Authentication required")

        # Parse and extract results-container div
        soup = BeautifulSoup(response.text, "html.parser")
        results_container = soup.find("div", class_="results-container")

        charts_data = _extract_charts_from_results(results_container)
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
        # client_id = _require_client_id(x_client_id)

        # session = await _ensure_authenticated_session(client_id)

        # valid_terms = _get_valid_term_codes(session)
        # if not valid_terms:
        #     raise HTTPException(status_code=401, detail="Authentication required")
        # if term not in valid_terms:
        #     return {
        #         "success": False,
        #         "message": "No evaluation data found",
        #         "term": term,
        #         "crn": crn,
        #     }

        requested_ids = []
        requested_names = []

        if instructor_names:
            requested_names.extend(_split_instructor_names(instructor_names))
        resolved_name_map = _resolve_instructor_ids_by_name(term, requested_names, db)
        missing_names = [
            name
            for name in requested_names
            if _normalize_instructor_name(name) not in resolved_name_map
        ]
        for name in requested_names:
            normalized = _normalize_instructor_name(name)
            if normalized in resolved_name_map:
                requested_ids.append(resolved_name_map[normalized])

        # Deduplicate while preserving order
        seen = set()
        unique_ids = []
        for instr_id in requested_ids:
            if instr_id not in seen:
                seen.add(instr_id)
                unique_ids.append(instr_id)

        print(
            f"[DEBUG] Resolved instructor IDs: {requested_ids}, missing names: {requested_names}"
        )

        if not unique_ids:
            if requested_names:
                return {
                    "success": False,
                    "message": "No matching instructors found",
                    "term": term,
                    "crn": crn,
                    "results": [],
                    "missing_instructors": missing_names or requested_names,
                }
            raise HTTPException(
                status_code=400,
                detail="Missing instructor_id(s) or instructor_name(s).",
            )
        url = "https://esther.rice.edu/selfserve/swkscmt.main"

        results = []

        for instr_id in unique_ids:
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
                "p_instr": instr_id,
            }
            if as_fid:
                payload["as_fid"] = as_fid

            response = session.post(url, data=payload, timeout=15)

            if (
                "bmenu.P_MainMnu" not in response.text
                and "Personal Information" not in response.text
            ):
                _clear_client_auth(client_id)
                raise HTTPException(status_code=401, detail="Authentication required")

            page = BeautifulSoup(response.text, "html.parser")
            sections = []
            for container in page.find_all("div", class_="results-container"):
                header = _find_eval_header(container)
                header_text = header.get_text(" ", strip=True) if header else ""
                meta = _parse_eval_header_text(header_text)
                charts = _extract_charts_from_results(container)
                section = {
                    **meta,
                    "html": str(container),
                    "charts": charts,
                }
                sections.append(section)

            if crn:
                sections = [
                    section for section in sections if section.get("crn") == crn
                ]

            instructor_label = _lookup_instructor_name(term, instr_id, db)
            results.append(
                {
                    "instructor_id": instr_id,
                    "instructor_name": instructor_label,
                    "sections": sections,
                    "success": bool(sections),
                    "message": (
                        "No evaluation data found"
                        if not sections
                        else "Evaluation data found"
                    ),
                }
            )

        return {
            "success": any(result.get("success") for result in results),
            "term": term,
            "crn": crn,
            "results": results,
            "missing_instructors": missing_names,
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in instructor evaluation endpoint: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch instructor evaluation: {str(e)}"
        )
