import os
import re
from collections import defaultdict
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET
import asyncio
import json

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
import sqlite3 as sql
import requests as rq
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

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


DB_PATH = _validate_sqlite_path(_resolve_db_path('courses.db'), 'Courses')
TERMS_DB_PATH = _validate_sqlite_path(_resolve_db_path('terms.db'), 'Terms')
SUBJECTS_DB_PATH = _validate_sqlite_path(_resolve_db_path('subjects.db'), 'Subjects')
print(f"Using courses DB at: {DB_PATH}")
try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None

from pydantic import BaseModel

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


CoursesResponse = Dict[str, List[Course]]

META_COURSES_URL = 'https://courses.rice.edu/courses/!SWKSCAT.info'

app = FastAPI()

# UPDATE: Added potential production URL
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://your-app-name.vercel.app", "localhost"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global storage for cookies from Duo authentication
_stored_cookies = None

VALID_SUBJECTS = set()
SUBJECT_NAMES = {}
with sql.connect(f"file:{SUBJECTS_DB_PATH}?mode=ro", uri=True) as con:
    cur = con.cursor()

    # Find all tables that look like 'subjects_XXXXXX'
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'subjects_%'")
    subject_tables = [row[0] for row in cur.fetchall()]

    for table in subject_tables:
        # Get all columns to understand what data is available
        cur.execute(f"PRAGMA table_info({table})")
        columns = [col[1] for col in cur.fetchall()]
        
        # Fetch both code and subject name
        if 'subject' in columns and 'code' in columns:
            rows = cur.execute(f"SELECT DISTINCT code, subject FROM {table}").fetchall()
            for code, subject in rows:
                code_upper = code.upper()
                VALID_SUBJECTS.add(code_upper)
                SUBJECT_NAMES[code_upper] = subject
        else:
            # Fallback if only code is available
            rows = cur.execute(f"SELECT DISTINCT code FROM {table}").fetchall()
            for r in rows:
                code_upper = r[0].upper()
                VALID_SUBJECTS.add(code_upper)
                if code_upper not in SUBJECT_NAMES:
                    SUBJECT_NAMES[code_upper] = code_upper

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

def _make_fts_query(q: str) -> str:
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


def _is_specific_course_query(q: str) -> bool:
    # e.g. MATH354 or MATH 354
    return bool(re.match(r'^[A-Z]{4}\s*\d{3}$', q))


def _term_table_name(term_code: str) -> str:
    return f"courses_{term_code}"


def _term_code_from_table_name(term_value: str) -> str:
    if term_value.startswith('courses_'):
        return term_value.split('courses_', 1)[1]
    return term_value


def _normalize_course_page(course_page: Optional[str], term_code: str, crn: str) -> str:
    fallback = (
        f"https://courses.rice.edu/courses/courses/!SWKSCAT.cat?"
        f"p_action=COURSE&p_term={term_code}&p_crn={crn}"
    )
    if not course_page:
        return fallback

    normalized = course_page.replace('https://courses.rice.edu//', 'https://courses.rice.edu/')
    normalized = normalized.replace('/admweb/!SWKSCAT.cat?', '/courses/courses/!SWKSCAT.cat?')
    if 'courses.rice.edu' not in normalized:
        return fallback
    return normalized


def _load_course_details(db: sql.Connection, table_name: str, crn: str) -> Dict[str, Optional[str]]:
    # Guard dynamic table name to avoid unsafe SQL interpolation.
    if not re.match(r'^courses_\d{6}$', table_name):
        return {}

    cur = db.cursor()
    row = cur.execute(
        f"SELECT instructors, meeting_times, credits, course_page FROM {table_name} WHERE crn = ? LIMIT 1",
        (crn,),
    ).fetchone()
    return dict(row) if row else {}


def _row_to_course(row: sql.Row, db: sql.Connection) -> Course:
    row_dict = dict(row)
    term_value = row_dict.get('term', '')
    term_code = _term_code_from_table_name(term_value)
    crn = row_dict.get('crn', '')

    details = _load_course_details(db, term_value, crn)
    return Course.model_validate(
        {
            'term': term_code,
            'crn': crn,
            'crs': row_dict.get('crs'),
            'title': row_dict.get('title'),
            'instructors': details.get('instructors') or row_dict.get('instructors') or 'TBA',
            'meeting_times': details.get('meeting_times'),
            'credits': details.get('credits'),
            'course_page': _normalize_course_page(details.get('course_page'), term_code, crn),
        }
    )


def _group_courses(rows: List[sql.Row], db: sql.Connection) -> CoursesResponse:
    grouped: Dict[str, List[Course]] = defaultdict(list)
    for row in rows:
        course = _row_to_course(row, db)
        course_code = course.crs or f"{course.term}-{course.crn}"
        grouped[course_code].append(course)
    return dict(grouped)


def _get_course_syllabus(term_code: str, crn: str) -> Optional[str]:
    req = rq.get(f"{META_COURSES_URL}?action=SYLLABUS&term={term_code}&crn={crn}", timeout=15)
    req.raise_for_status()
    res = ET.fromstring(req.text)
    if res.attrib.get('has-syllabus') != 'yes':
        return None
    return res.attrib.get('doc-url')



@app.get("/api/courses/", response_model=CoursesResponse)
def search_courses(
    q: str, 
    term_code: str = DEFAULT_COURSE_TERM_CODE, 
    top_n_results: int = 50,
    offset: int = 0,
    db: sql.Connection = Depends(get_db)
) -> CoursesResponse:
    try:
        q = _clean_query(q)
        print(f"Cleaned query: '{q}'")
        fts_query = _make_fts_query(q)
        print(f"Generated FTS query: '{fts_query}'")

        if not fts_query:
            return {}

        # --- EXECUTION ---
        sql_query = "SELECT * FROM global_search WHERE global_search MATCH ?"
        params = [fts_query]

        if term_code != "all":
            sql_query += " AND term = ?"
            params.append(_term_table_name(term_code))

        # For exact course-code searches, prioritize newest term first.
        if _is_specific_course_query(q):
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
        print(f"Final SQL Query: '{sql_query}' with params {params}")
        cur = db.cursor()
        rows = cur.execute(sql_query, tuple(params)).fetchall()
        return _group_courses(rows, db)
    except sql.Error as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected server error: {str(e)}")


@app.get("/api/terms", response_model=List[Term])
def get_terms() -> List[Term]:
    """Get all available terms from the terms database."""
    try:
        terms_conn = sql.connect(f"file:{TERMS_DB_PATH}?mode=ro", uri=True, check_same_thread=False)
        terms_conn.row_factory = sql.Row
        cur = terms_conn.cursor()
        cur.execute("SELECT code, term FROM terms ORDER BY code DESC")
        rows = cur.fetchall()
        terms_conn.close()
        return [Term(code=row['code'], term=row['term']) for row in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch terms: {str(e)}")


@app.get("/api/subjects", response_model=List[Subject])
def get_subjects() -> List[Subject]:
    """Get all available subject codes with their full subject names."""
    try:
        subjects = [
            Subject(code=code, subject=SUBJECT_NAMES.get(code, code))
            for code in sorted(VALID_SUBJECTS)
        ]
        return subjects
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch subjects: {str(e)}")


@app.get("/api/syllabus", response_model=SyllabusResponse)
def get_syllabus(term_code: str, crn: str) -> SyllabusResponse:
    """Get syllabus link for a course instance, if available."""
    if not re.match(r'^\d{6}$', term_code):
        raise HTTPException(status_code=400, detail="term_code must be a 6-digit code")
    if not re.match(r'^\d{5}$', crn):
        raise HTTPException(status_code=400, detail="crn must be a 5-digit value")

    try:
        syllabus_url = _get_course_syllabus(term_code, crn)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch syllabus data: {str(e)}")

    if syllabus_url:
        return SyllabusResponse(syllabus_url=syllabus_url, message="Syllabus available")

    return SyllabusResponse(syllabus_url=None, message="No syllabus posted")


async def _authenticate_with_duo():
    """Authenticate once with Duo using Playwright and return cookies."""
    async with async_playwright() as p:
        print("Launching browser for Duo authentication...")
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://esther.rice.edu/")

        # Wait for user to complete Duo authentication
        await page.wait_for_selector("text='Personal Information'", timeout=0)
        print("✅ Duo authentication successful!")

        # Extract cookies
        cookies = await context.cookies()
        cookie_dict = {cookie['name']: cookie['value'] for cookie in cookies}

        await browser.close()
        return cookie_dict


def _get_valid_term_codes(cookies_dict: dict) -> set:
    """Fetch and parse valid term codes from Rice's API."""
    try:
        session = rq.Session()
        for name, value in cookies_dict.items():
            session.cookies.set(name, value)
        
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
        print(f"Error fetching term codes: {str(e)}")
        return set()


@app.get("/api/evaluate")
async def get_evaluation(term: str, crn: str, subject: str):
    """
    Get course evaluation data. First call triggers Duo auth.
    Subsequent calls reuse stored cookies.
    
    Returns HTML of the results-container div.
    """
    global _stored_cookies

    if not re.match(r'^\d{6}$', term):
        raise HTTPException(status_code=400, detail="term must be a 6-digit code")
    if not re.match(r'^\d{5}$', crn):
        raise HTTPException(status_code=400, detail="crn must be a 5-digit value")
    if not subject or not re.match(r'^[A-Z]{4}$', subject.upper()):
        raise HTTPException(status_code=400, detail="subject must be 4-letter code")
    
    try:
        # Authenticate if not already done
        if _stored_cookies is None:
            _stored_cookies = await _authenticate_with_duo()

        # Check if term is valid
        valid_terms = _get_valid_term_codes(_stored_cookies)
        if term not in valid_terms:
            return {
                "success": False,
                "message": "No evaluation data found",
                "term": term,
                "crn": crn,
                "subject": subject.upper()
            }

        # Query the evaluation website
        session = rq.Session()
        for name, value in _stored_cookies.items():
            session.cookies.set(name, value)

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
            # Session expired, clear cookies and re-authenticate
            _stored_cookies = None
            _stored_cookies = await _authenticate_with_duo()
            
            # Retry with new cookies
            session.cookies.clear()
            for name, value in _stored_cookies.items():
                session.cookies.set(name, value)
            
            # Get new as_fid
            get_response = session.get(url, timeout=15)
            soup = BeautifulSoup(get_response.text, 'html.parser')
            form = soup.find('form')
            if form:
                as_fid_input = form.find('input', {'name': 'as_fid'})
                if as_fid_input:
                    payload["as_fid"] = as_fid_input.get('value', '')
            
            response = session.post(url, headers=headers, data=payload, timeout=15)

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
            return {
                "success": True,
                "html": str(results_container),
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