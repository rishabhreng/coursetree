import os
import re
from collections import defaultdict
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET
from urllib.parse import parse_qs, urlparse, unquote

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import sqlite3 as sql
from matplotlib.pylab import f
import requests as r
from requests import Session
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from pydantic import BaseModel


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


DB_PATH = _validate_sqlite_path(_resolve_db_path('main.db'), 'Main')
print(f"Using main DB at: {DB_PATH}")

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

META_COURSES_URL = 'https://courses.rice.edu/courses/!SWKSCAT.info'

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global storage for cookies from Duo authentication
_stored_cookies = None
_stored_session = None

VALID_SUBJECTS = set()
SUBJECT_NAMES = {}

@app.post("/api/auth")
async def login_to_esther(req: LoginRequest):
    """Receives credentials from React and triggers the headless Duo push."""
    try:
        # DO NOT log the password here, even for debugging!
        print(f"[AUTH] Received login request for NetID: {req.netid}")
        
        # Call the updated headless Playwright function from earlier
        await _authenticate_with_duo(req.netid, req.password)
        
        return {"success": True, "message": "Successfully authenticated with ESTHER"}
    except Exception as e:
        print(f"[AUTH ERROR] {str(e)}")
        raise HTTPException(status_code=401, detail="Authentication failed. Did you approve the Duo push?")
    
def _clear_stored_auth() -> None:
    global _stored_cookies, _stored_session
    _stored_cookies = None
    _stored_session = None


def _sync_stored_cookies_from_session() -> None:
    global _stored_cookies, _stored_session
    if _stored_session is not None:
        _stored_cookies = _stored_session.cookies.get_dict()


def _is_pdf_response(content: bytes) -> bool:
    return content.startswith(b'%PDF')


def _looks_like_auth_expired(content: bytes) -> bool:
    sample = content[:2000].lower()
    return (
        b'cas' in sample
        or b'netid' in sample
        or b'duo' in sample
        or b'sign in' in sample
        or b'personal information' in sample
    )


def _looks_like_direct_link_block(content: bytes) -> bool:
    sample = content[:4000].lower()
    return (
        b'direct link' in sample
        or b'direct-link' in sample
        or b'access denied' in sample
        or b'not authorized' in sample
    )


def _fetch_syllabus_pdf_with_session(session: Session, term_code: str, crn: str):
    url = "https://esther.rice.edu/selfserve/!bwzkpsyl.v_viewDoc"
    params = {
        'term': term_code,
        'type': 'SYLLABUS',
        'crn': crn,
    }
    headers = {
        'Referer': 'https://esther.rice.edu/selfserve/swkscmt.main',
        'Accept': 'application/pdf,application/octet-stream;q=0.9,*/*;q=0.8',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }
    return session.get(url, params=params, headers=headers, timeout=15, stream=True)


def _bootstrap_selfserve_context(session: Session) -> None:
    """Warm key self-serve pages that commonly establish routing/session context."""
    session.get("https://esther.rice.edu/selfserve/", timeout=15)
    session.get("https://esther.rice.edu/selfserve/swkscmt.main", timeout=15)


async def _ensure_authenticated_session() -> Session:
    """Return an authenticated shared session, creating one via Duo when needed."""
    global _stored_cookies, _stored_session

    if _stored_cookies is None or _stored_session is None:
        _stored_cookies = await _authenticate_with_duo()

    return _stored_session


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

def _convert_to_fts_query(q: str) -> str:
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
        # Replace hyphens with spaces to handle hyphenated names/terms
        q_normalized = q.replace('-', ' ')
        words = q_normalized.split()
        if not words: return ""
        # Standard multi-word prefix search across all columns
        return " AND ".join([f"{w}*" for w in words])

def _row_to_course(row: sql.Row) -> Course:
    row_dict = dict(row)
    term_value = row_dict.get('term', '')
    term_code = term_value.split('courses_', 1)[1]

    data = {
            'term': term_code,
            'crn': row_dict.get('crn'),
            'crs': row_dict.get('crs'),
            'title': row_dict.get('title'),
            'instructors': row_dict.get('instructors') or 'TBA',
            'meeting_times': row_dict.get('meeting_times'),
            'credits': row_dict.get('credits'),
            'course_page': row_dict.get('course_page')
        }
    return Course(**data)

def _group_courses(rows: List[sql.Row]) -> CoursesResponse:
    grouped: Dict[str, List[Course]] = defaultdict(list)
    for row in rows:
        course = _row_to_course(row)
        course_code = course.crs or f"{course.term}-{course.crn}"
        grouped[course_code].append(course)
    return dict(grouped)

@app.get("/api/courses/", response_model=CoursesResponse)
def search_courses(
    q: str, 
    term_code: str = DEFAULT_COURSE_TERM_CODE, 
    top_n_results: int = 50,
    offset: int = 0,
    weight_recency: bool = False,
    db: sql.Connection = Depends(get_db)
) -> CoursesResponse:
    try:
        q = _clean_query(q)
        fts_query = _convert_to_fts_query(q)

        if not fts_query:
            return {}

        sql_query = "SELECT * FROM global_search WHERE global_search MATCH ?"
        params = [fts_query]

        if term_code != "all":
            sql_query += " AND term = ?"
            params.append(f"courses_{term_code}")

        # For exact course-code searches, prioritize newest term first.
        if re.match(r'^[A-Z]{4}\s*\d{3}$', q):
            sql_query += (
                " ORDER BY CAST(REPLACE(term, 'courses_', '') AS INTEGER) DESC, "
                "bm25(global_search) ASC LIMIT ? OFFSET ?"
            )
        else:
            # When searching all terms with weight_recency, prioritize recency first
            # Otherwise, prioritize search relevance (BM25) first
            if term_code == "all" and weight_recency:
                sql_query += (
                    " ORDER BY CAST(REPLACE(term, 'courses_', '') AS INTEGER) DESC, "
                    "bm25(global_search) ASC LIMIT ? OFFSET ?"
                )
            else:
                sql_query += (
                    " ORDER BY bm25(global_search) ASC, "
                    "CAST(REPLACE(term, 'courses_', '') AS INTEGER) DESC LIMIT ? OFFSET ?"
                )
        params.append(top_n_results)
        params.append(offset)
        
        cur = db.cursor()
        rows = cur.execute(sql_query, tuple(params)).fetchall()
        return _group_courses(rows)
    except sql.Error as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected server error: {str(e)}")

@app.get("/api/terms", response_model=List[Term])
def get_terms(db: sql.Connection = Depends(get_db)) -> List[Term]:
    """Get all available terms from the database."""
    try:
        cur = db.cursor()
        rows = cur.execute("SELECT code, term FROM terms ORDER BY code DESC").fetchall()
        return [Term(code=row['code'], term=row['term']) for row in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch terms: {str(e)}")


@app.get("/api/subjects", response_model=List[Subject])
def get_subjects(db: sql.Connection = Depends(get_db)) -> List[Subject]:
    """Get all available subject codes with their full subject names."""
    try:
        cur = db.cursor()

        # Find all tables that look like 'subjects_XXXXXX'
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'subjects_%'")
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
        raise HTTPException(status_code=500, detail=f"Failed to fetch subjects: {str(e)}")

@app.get("/api/syllabus", response_model=SyllabusResponse)
async def get_syllabus(term_code: str, crn: str) -> SyllabusResponse:
    """
    Check if syllabus exists for a course.
    If it does, query PDF endpoint to fetch it via authenticated session.
    """
    global _stored_cookies, _stored_session

    try:
        # Check if syllabus exists via faster metadata api first:  
        try:
            metadata_url = f"{META_COURSES_URL}?action=SYLLABUS&term={term_code}&crn={crn}"
            metadata_response = r.get(metadata_url, timeout=15)
            metadata_response.raise_for_status()
            
            metadata = ET.fromstring(metadata_response.text)
            if metadata.attrib.get('has-syllabus') != 'yes':
                return SyllabusResponse(syllabus_url=None, message="No syllabus posted")
        except Exception as e:
            print(f"[ERROR] Could not check syllabus metadata: {str(e)}")
            return SyllabusResponse(syllabus_url=None, message="No syllabus posted")
        
        try:
            # Authenticate if not already done (shared with evaluations)
            session = await _ensure_authenticated_session()
            
            # Use the stored session which has Duo authentication cookies
            print(f'Cookies: {_stored_cookies}')
            print(f'Session cookies: {_stored_session.cookies.get_dict()}')
            
            # Fetch the PDF from authenticated endpoint
            response = _fetch_syllabus_pdf_with_session(session, term_code, crn)
            response.raise_for_status()
            _sync_stored_cookies_from_session()
            
            # Get the full PDF content for local file write
            pdf_content = response.content
            
            # Debug: Check content size and type
            print(f"[DEBUG] Response size: {len(pdf_content)} bytes, content-type: {response.headers.get('content-type', 'unknown')}")
            
            # Check if response is actually a PDF.
            if not _is_pdf_response(pdf_content):
                print(f"[DEBUG] Response doesn't look like a PDF. First 100 bytes: {pdf_content[:100]}")
                # Some responses are direct-link/context failures, not true auth expiry.
                if _looks_like_direct_link_block(pdf_content):
                    print("[DEBUG] Syllabus direct-link block detected. Bootstrapping selfserve context...")
                    _bootstrap_selfserve_context(session)
                    response = _fetch_syllabus_pdf_with_session(session, term_code, crn)
                    response.raise_for_status()
                    pdf_content = response.content
                    _sync_stored_cookies_from_session()
                    print(f"[DEBUG] After context bootstrap - Response size: {len(pdf_content)} bytes")

                # Only re-authenticate when response appears to be login/auth-related.
                if not _is_pdf_response(pdf_content) and _looks_like_auth_expired(pdf_content):
                    print("[DEBUG] Auth expiry detected during syllabus fetch. Re-authenticating...")
                    _clear_stored_auth()
                    session = await _ensure_authenticated_session()
                    _bootstrap_selfserve_context(session)
                    response = _fetch_syllabus_pdf_with_session(session, term_code, crn)
                    response.raise_for_status()
                    pdf_content = response.content
                    _sync_stored_cookies_from_session()
                    print(f"[DEBUG] After re-auth - Response size: {len(pdf_content)} bytes")

                if not _is_pdf_response(pdf_content):
                    raise HTTPException(status_code=502, detail="Failed to retrieve a valid syllabus PDF")
            
            # Return the PDF content directly
            return StreamingResponse(
                iter([pdf_content]),
                media_type="application/pdf",
                headers={"Content-Disposition": f"inline; filename=syllabus_{term_code}_{crn}.pdf"}
            )
        except Exception as e:
            print(f"[ERROR] Error fetching syllabus PDF: {str(e)}")
            raise HTTPException(status_code=502, detail=f"Failed to fetch syllabus PDF: {str(e)}")

    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to check syllabus: {str(e)}")

async def _authenticate_with_duo(netid: str, password: str):
    """
    Headless authentication: Server types credentials, user approves on phone.
    """
    global _stored_session
    
    async with async_playwright() as p:
        print(f"[AUTH] Launching headless browser for user: {netid}...")
        # MUST be True on Oracle Cloud
        browser = await p.chromium.launch(headless=True) 
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://esther.rice.edu/")

        # 1. Fill in the Rice CAS Login (Update selectors based on Rice's actual login page)
        try:
            print("[AUTH] Entering credentials...")
            # Example selectors - you will need to inspect the Rice login page to get the exact IDs
            await page.fill("input[name='username']", netid)
            await page.fill("input[name='password']", password)
            await page.click("button[name='submit']")
            
            # 2. The Duo Push Phase
            # At this point, Rice's system will automatically send a Duo push to the user's phone.
            print("[AUTH] Credentials submitted. Waiting for user to approve Duo push on their phone...")
            
            # We wait up to 60 seconds for them to tap "Approve" on their phone
            await page.wait_for_selector("text='Personal Information'", timeout=60000)
            print("✅ Duo authentication successful!")
            
        except Exception as e:
            await browser.close()
            raise Exception("Authentication failed. Did you approve the Duo push?")

        # Extract cookies
        cookies = await context.cookies()
        cookie_dict = {cookie['name']: cookie['value'] for cookie in cookies}

        await browser.close()
    
    # Create session after auth and load cookies into it
    _stored_session = Session()
    for name, value in cookie_dict.items():
        _stored_session.cookies.set(name, value)

    # Prime selfserve cookies
    try:
        _bootstrap_selfserve_context(_stored_session)
    except Exception as e:
        print(f"[WARN] Failed to warm selfserve session after Duo auth: {str(e)}")

    _sync_stored_cookies_from_session()
    
    return _stored_cookies


def _get_valid_term_codes(session: Session) -> set:
    """Fetch and parse valid term codes from Rice's API using active auth session."""
    try:
        terms_url = "https://esther.rice.edu/selfserve/!swkscmp.ajax?p_data=TERMS"
        response = session.get(terms_url, timeout=15)
        
        # Parse XML response
        root = ET.fromstring(response.text)
        term_codes = set()
        
        for term_elem in root.findall('.//TERM'):
            code = term_elem.get('CODE')
            if code:
                term_codes.add(code)
        
        return term_codes
    except Exception as e:
        print(f"[ERROR] Error fetching term codes: {str(e)}")
        return set()


def _extract_chart_data(img_src: str, response_count: int = None) -> Optional[Dict]:
    """Extract chart data from ObjectPlanet chart servlet URL and convert percentages to counts."""
    try:
        parsed_url = urlparse(img_src)
        params = parse_qs(parsed_url.query)
        
        # Extract values and labels
        values_str = params.get('sampleValues', [''])[0]
        labels_str = params.get('sampleLabels', [''])[0]
        title = unquote(params.get('chartTitle', [''])[0])
        
        if not values_str or not labels_str:
            return None
        
        # These are percentages from the URL
        percentage_values = [int(x) for x in values_str.split(',') if x.isdigit()]
        
        # Convert percentages to actual counts if response_count is provided
        if response_count and response_count > 0:
            actual_values = [round(pct * response_count / 100) for pct in percentage_values]
        else:
            actual_values = percentage_values
        
        # Labels are comma-separated with \n for line breaks
        labels = []
        for label in labels_str.split(','):
            # Decode URL encoding and replace \n with space
            decoded = unquote(label).replace('\n', ' ').strip()
            if decoded:
                labels.append(decoded)
        
        if not actual_values or not labels:
            return None
        
        return {
            "title": title,
            "values": actual_values,
            "labels": labels,
            "total": response_count if response_count else sum(actual_values)
        }
    except Exception as e:
        print(f"[ERROR] Error extracting chart data: {str(e)}")
        return None


@app.get("/api/evaluate")
async def get_evaluation(term: str, crn: str, subject: str):
    """
    Get course evaluation data. First call triggers Duo auth.
    Subsequent calls reuse stored session.
    
    Returns HTML of the results-container div.
    """
    global _stored_cookies, _stored_session

    try:
        # Authenticate if not already done
        session = await _ensure_authenticated_session()

        # Check if term is valid
        valid_terms = _get_valid_term_codes(session)
        if term not in valid_terms:
            return {
                "success": False,
                "message": "No evaluation data found",
                "term": term,
                "crn": crn,
                "subject": subject.upper()
            }

        # Use the stored session (which maintains cookies)
        _sync_stored_cookies_from_session()

        url = "https://esther.rice.edu/selfserve/swkscmt.main"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        # First, GET the page to extract the as_fid token
        get_response = session.get(url, timeout=15)
        soup = BeautifulSoup(get_response.text, 'html.parser')
        
        # Extract as_fid from form or page
        as_fid = None
        form = soup.find('form')
        if form:
            as_fid_input = form.find('input', {'name': 'as_fid'})
            if as_fid_input:
                as_fid = as_fid_input.get('value', '')
        
        if not as_fid:
            # Try to extract from page source
            import re as regex_module
            match = regex_module.search(r'as_fid["\']?\s*[:=]\s*["\']?([a-f0-9]{40})', get_response.text)
            if match:
                as_fid = match.group(1)
        
        print(f"[DEBUG] as_fid: {as_fid}")

        payload = {
            "p_commentid": "",
            "p_confirm": "1",
            "p_term": term,
            "p_type": "Course",
            "p_crn": crn
        }
        
        if as_fid:
            payload["as_fid"] = as_fid

        print(f"[DEBUG] Posting payload: {payload}")
        response = session.post(url, headers=headers, data=payload, timeout=15)
        print(f"[DEBUG] Response status: {response.status_code}")
        print(f"[DEBUG] Response length: {len(response.text)}")
        print(f"[DEBUG] Response preview: {response.text[:500]}")

        # Check if session is valid
        if "bmenu.P_MainMnu" not in response.text and "Personal Information" not in response.text:
            print("[DEBUG] Session appears invalid, re-authenticating...")
            # Session expired, clear and re-authenticate
            _clear_stored_auth()
            session = await _ensure_authenticated_session()
            
            # Get new as_fid
            get_response = session.get(url, timeout=15)
            soup = BeautifulSoup(get_response.text, 'html.parser')
            form = soup.find('form')
            if form:
                as_fid_input = form.find('input', {'name': 'as_fid'})
                if as_fid_input:
                    payload["as_fid"] = as_fid_input.get('value', '')
            
            response = session.post(url, headers=headers, data=payload, timeout=15)
            _sync_stored_cookies_from_session()

        # Parse and extract results-container div
        soup = BeautifulSoup(response.text, 'html.parser')
        results_container = soup.find('div', class_='results-container')
        
        print(f"[DEBUG] Found results-container: {results_container is not None}")
        if not results_container:
            # Try alternative class names
            print(f"[DEBUG] Searching for alternative containers...")
            for div in soup.find_all('div'):
                classes = div.get('class', [])
                if 'result' in str(classes).lower() or 'eval' in str(classes).lower():
                    print(f"[DEBUG] Found potential container with classes: {classes}")

        if results_container:
            # Extract chart data from image URLs with their response counts
            charts_data = []
            
            # Find all chart divs
            chart_divs = results_container.find_all('div', class_='chart')
            
            for chart_div in chart_divs:
                # Extract response count from the filler div
                filler = chart_div.find('div', class_='filler')
                response_count = None
                
                if filler:
                    # Find the div containing "Responses: XX"
                    filler_text = filler.get_text()
                    responses_match = re.search(r'Responses:\s*(\d+)', filler_text)
                    if responses_match:
                        response_count = int(responses_match.group(1))
                
                # Find the image with chart servlet URL
                img = chart_div.find('img')
                if img:
                    src = img.get('src', '')
                    if 'ChartServlet' in src:
                        chart_data = _extract_chart_data(src, response_count)
                        if chart_data:
                            charts_data.append(chart_data)
            
            return {
                "success": True,
                "html": str(results_container),
                "charts": charts_data,
                "term": term,
                "crn": crn,
                "subject": subject.upper()
            }
        else:
            return {
                "success": False,
                "message": "No evaluation data found",
                "term": term,
                "crn": crn,
                "subject": subject.upper()
            }

    except Exception as e:
        print(f"Error in evaluation endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch evaluation: {str(e)}")