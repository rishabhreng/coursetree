import re
import sqlite3 as sql
from collections import defaultdict
from typing import Dict, List, Optional

from pydantic import BaseModel

VALID_SUBJECTS = set()


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
    ) in ACRONYM_MAP.items():  # TODO: maybe remove this, doesn't add much value
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
        return " AND ".join([_escape_fts_prefix_token(w) for w in words])
