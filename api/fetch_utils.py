from pathlib import Path

from requests import Session
import sqlite3 as sql
from urllib.parse import parse_qs, urlparse, unquote
import re
from typing import Dict, List, Optional

from xml.etree import ElementTree as ET

SYLLABUS_BASE_URL = "https://esther.rice.edu/selfserve/!bwzkpsyl.v_viewDoc"
DB_PATH = Path.cwd().parent / "main.db"
METADATA_COURSES_URL = "https://courses.rice.edu/courses/!SWKSCAT.info"


def is_pdf_response(content: bytes) -> bool:
    return content.startswith(b"%PDF")


def fetch_syllabus_pdf_with_session(session: Session, term_code: str, crn: str):
    params = {
        "term": term_code,
        "type": "SYLLABUS",
        "crn": crn,
    }

    return session.get(SYLLABUS_BASE_URL, params=params, timeout=15, stream=True)


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


def get_valid_term_codes(session: Session) -> set:
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


def extract_chart_data(img_src: str, response_count: int = None) -> Optional[Dict]:
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


def extract_charts_from_results(results_container) -> List[Dict]:
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
                chart_data = extract_chart_data(src, response_count)
                if chart_data:
                    charts_data.append(chart_data)

    return charts_data


def find_eval_header(container):
    for prev in container.previous_elements:
        if not hasattr(prev, "get_text"):
            continue
        if getattr(prev, "name", None) not in {"table", "div"}:
            continue
        text = prev.get_text(" ", strip=True)
        if "Course(s):" in text and "Term:" in text:
            return prev
    return None


def extract_label_value(text: str, label: str) -> Optional[str]:
    pattern = rf"{re.escape(label)}\s*(.+?)(?=\s+(?:Term:|Course\(s\):|Enrolled:|Instructor\(s\):)|$)"
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).strip() if match else None


def parse_eval_header_text(text: str) -> Dict[str, Optional[str]]:
    term = extract_label_value(text, "Term:")
    course = extract_label_value(text, "Course(s):")
    enrolled = extract_label_value(text, "Enrolled:")
    instructors = extract_label_value(text, "Instructor(s):")

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


def normalize_instructor_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", name.strip().replace(".", ""))
    if not cleaned:
        return ""

    if "," in cleaned:
        last_name, given_names = cleaned.split(",", 1)
        last_name = last_name.strip()
        given_tokens = [token for token in given_names.split() if len(token) > 1]
        given_name = given_tokens[0] if given_tokens else ""
    else:
        tokens = cleaned.split()
        if len(tokens) == 1:
            return tokens[0].lower()
        last_name = tokens[-1].strip()
        given_tokens = [token for token in tokens[:-1] if len(token) > 1]
        given_name = given_tokens[0] if given_tokens else tokens[0]

    canonical = f"{last_name} {given_name}".strip()
    return re.sub(r"\s+", " ", canonical).lower()


def split_instructor_names(raw: str) -> List[str]:
    if not raw:
        return []
    if "|" in raw:
        parts = raw.split("|")
    else:
        parts = [raw]
    return [name.strip() for name in parts]


def resolve_instructor_ids_by_name(
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

    normalized = {normalize_instructor_name(name) for name in names if name}
    rows = cur.execute(f"SELECT name, id FROM {table}").fetchall()

    mapping = {}
    for row in rows:
        key = normalize_instructor_name(row["name"])
        if key in normalized:
            mapping[key] = row["id"]

    return mapping


def lookup_instructor_name(
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
