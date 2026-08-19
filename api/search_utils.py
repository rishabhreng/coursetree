import re
import sqlite3 as sql
from collections import defaultdict
from typing import Dict, List, Optional

from pydantic import BaseModel

VALID_SUBJECTS = set()


def load_valid_subjects(db_path=None) -> set:
    """Load all valid subject department codes from database tables and course codes."""
    if db_path is None:
        try:
            from fetch_utils import DB_PATH
            db_path = DB_PATH
        except Exception:
            return set()

    subjects = set()
    if not hasattr(db_path, "exists") or not db_path.exists():
        return subjects

    try:
        conn = sql.connect(f"file:{db_path}?mode=ro", timeout=5.0, uri=True)
        cur = conn.cursor()
        # 1. Check subjects_* tables if any exist
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'subjects_%'")
        tables = [row[0] for row in cur.fetchall()]
        for table in tables:
            try:
                cur.execute(f"SELECT DISTINCT code FROM {table}")
                for row in cur.fetchall():
                    if row[0]:
                        subjects.add(str(row[0]).strip().upper())
            except Exception:
                pass

        # 2. Extract all distinct department codes from global_search table
        try:
            cur.execute("SELECT DISTINCT crs FROM global_search WHERE crs IS NOT NULL")
            for row in cur.fetchall():
                crs_val = row[0]
                if crs_val and " " in crs_val:
                    subj = crs_val.split(" ")[0].strip().upper()
                    if subj and (2 <= len(subj) <= 6) and subj.isalpha():
                        subjects.add(subj)
        except Exception:
            pass
        conn.close()
    except Exception as e:
        print(f"[WARN] Could not load valid subjects: {e}")

    return subjects


# Initialize valid subjects at startup
try:
    VALID_SUBJECTS.update(load_valid_subjects())
except Exception:
    pass


class Course(BaseModel):
    term: str
    crn: str
    crs: str
    title: str
    instructors: str
    meeting_times: Optional[str] = None
    credits: Optional[str] = None
    course_page: Optional[str] = None
    distribution: Optional[str] = None


class Term(BaseModel):
    code: str
    term: str


class Subject(BaseModel):
    code: str
    subject: str


class SyllabusResponse(BaseModel):
    syllabus_url: Optional[str] = None
    file_type: Optional[str] = None
    filename: Optional[str] = None
    message: str


class LoginRequest(BaseModel):
    netid: str
    password: str


CoursesResponse = Dict[str, List[Course]]

# Common acronym/abbreviation mappings to expand search queries
ACRONYM_MAP = {
    "UG": "UNDERGRADUATE",
    "GRAD": "GRADUATE",
}


def _escape_fts_prefix_token(token: str) -> str:
    token = token.replace('"', '""')
    return f'"{token}"*'


def clean_query(q: str) -> str:
    """Utility function to clean and standardize the search query"""
    q = q.strip().upper()  # Normalize whitespace
    q = re.sub(r"\.", "", q)
    q = re.sub(r",", "", q)
    # Expand common acronyms
    for (
        acronym,
        full,
    ) in ACRONYM_MAP.items():
        if q == acronym:
            return full
    return q


def row_to_course(row: sql.Row) -> Course:
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
        "distribution": row_dict.get("distribution"),
    }
    return Course(**data)


def group_courses(rows: List[sql.Row]) -> CoursesResponse:
    grouped: Dict[str, List[Course]] = defaultdict(list)
    for row in rows:
        course = row_to_course(row)
        course_code = course.crs or f"{course.term}-{course.crn}"
        grouped[course_code].append(course)
    return dict(grouped)


def convert_to_fts_query(q: str) -> str:
    """Convert a cleaned query into an FTS5 query string based on its format"""
    # Lazy refresh VALID_SUBJECTS if it was empty at startup
    if not VALID_SUBJECTS:
        VALID_SUBJECTS.update(load_valid_subjects())

    # CASE 1: CRN (5 Digits)
    if len(q) == 5 and q.isdigit():
        return f"crn : {q}"

    # CASE 2: Course Code (e.g. COMP 140, COMP140, ELEC 220, ELEC220)
    elif re.match(r"^[A-Z]{2,5}\s*\d{1,4}[A-Z]?$", q):
        match = re.search(r"([A-Z]{2,5})\s*(\d{1,4}[A-Z]?)", q)
        dpt, num = match.group(1), match.group(2)
        return f'crs : "{dpt} {num}"*'

    # CASE 3: Subject/Dept Only (match strictly against VALID_SUBJECTS)
    elif q.isalpha() and q in VALID_SUBJECTS:
        return f"crs : {q}"

    # CASE 4: Course Number Only (140)
    elif len(q) == 3 and q.isdigit():
        return f"crs : {q}"

    # CASE 5: General fuzzy/keyword search
    else:
        # Replace hyphens with spaces to handle hyphenated names/terms
        q_normalized = q.replace("-", " ")
        words = q_normalized.split()
        if not words:
            return ""
        # Standard multi-word prefix search across all columns
        return " AND ".join([_escape_fts_prefix_token(w) for w in words])
