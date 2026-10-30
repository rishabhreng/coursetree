import argparse
import json
import os
import sqlite3 as sql
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional
from xml.etree import ElementTree as ET

from pandas import DataFrame
import requests as r
from tqdm import tqdm

META_COURSES_URL = "https://courses.rice.edu/courses/!SWKSCAT.info"
BASE_COURSES_URL = "https://courses.rice.edu/admweb/!SWKSECX.main"
COURSE_CONSTRUCTOR_URL = "https://courses.rice.edu/courses/courses/!SWKSCAT.cat?p_action=COURSE"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "data"))
BASE_DB_DIR = DATA_DIR if os.path.exists(DATA_DIR) else BASE_DIR

_db_lock = threading.Lock()


def _request_xml(url: str, timeout: int = 15, attempts: int = 5):
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            req = r.get(url, timeout=timeout)
            req.raise_for_status()
            return ET.fromstring(req.text)
        except (r.RequestException, ET.ParseError) as e:
            last_exc = e
            if attempt == attempts:
                raise
            print(f"Request failed ({attempt}/{attempts}) for {url}: {e}. Retrying in 10s...")
            time.sleep(10)

    raise last_exc


def _export_sql(df: DataFrame, table_name: str, sql_db_path: str):
    with _db_lock:
        con = sql.connect(sql_db_path, timeout=60.0)
        try:
            df.to_sql(name=table_name, con=con, if_exists="replace", index=False)
            con.commit()
        finally:
            con.close()


def get_term_codes(sql_db_path=None) -> DataFrame:
    terms = _request_xml(f"{META_COURSES_URL}?action=TERMS", timeout=15).findall("TERM")
    df = [
        {"term": term.find("OPT").tail, "code": term.attrib.get("code")}
        for term in terms
        if "Quadmester" not in (term.find("OPT").tail or "")
    ]
    res_df = DataFrame(df)
    if sql_db_path:
        _export_sql(res_df, "terms", sql_db_path)
    return res_df


def get_subject_codes_for_term(term_code: str, sql_db_path=None) -> DataFrame:
    subjects = _request_xml(
        f"{META_COURSES_URL}?action=SUBJECTS&term={term_code}", timeout=15
    ).findall("SUBJECT")
    df = DataFrame([
        {"subject": s.find("OPT").tail, "code": s.attrib.get("code")}
        for s in subjects
    ])
    if sql_db_path:
        _export_sql(df, f"subjects_{term_code}", sql_db_path)
    return df


def get_school_codes_for_term(term_code: str, sql_db_path=None) -> DataFrame:
    schools = _request_xml(
        f"{META_COURSES_URL}?action=SCHOOLS&term={term_code}", timeout=15
    ).findall("SCHOOL")
    df = DataFrame([
        {"school": s.find("OPT").tail, "code": s.attrib.get("code")}
        for s in schools
    ])
    if sql_db_path:
        _export_sql(df, f"schools_{term_code}", sql_db_path)
    return df


def get_instructors_for_term(term_code: str, sql_db_path=None) -> DataFrame:
    instructors = _request_xml(
        f"https://esther.rice.edu/selfserve/!swkscmp.ajax?p_data=INSTRUCTORS&p_term={term_code}",
        timeout=15,
    ).findall("INSTRUCTOR")
    df = DataFrame([
        {"name": i.attrib.get("NAME", ""), "id": i.attrib.get("WEBID", "0")}
        for i in instructors
    ])
    if sql_db_path and not df.empty:
        _export_sql(df, f"instructors_{term_code}", sql_db_path)
    return df


def _convert_time_to_human_readable(time_str: str) -> Optional[str]:
    time_str = time_str.strip()
    if len(time_str) != 4 or not time_str.isdigit():
        return None

    hh = int(time_str[:2])
    mm = int(time_str[2:])
    if hh > 23 or mm > 59:
        return None

    am_pm = "AM" if hh < 12 else "PM"
    hh = hh % 12 or 12
    return f"{hh}:{mm:02d} {am_pm}"


def get_all_courses_for_term(term_code: str, sql_db_path=None) -> DataFrame:
    courses = _request_xml(f"{BASE_COURSES_URL}?term={term_code}", timeout=60).findall("course")
    df = []

    for course in courses:
        crn = course.findtext("crn", "").strip()
        subject = course.findtext("subject", "").strip()
        course_number = course.findtext("course-number", "").strip()
        crs = f"{subject} {course_number}".strip()
        title = course.findtext("title", "").strip()
        section = course.findtext("section", "").strip()
        credits = course.findtext("credit-hours", "").strip()
        distribution = course.findtext("distribution-group", "").strip()
        prerequisites = course.findtext("pre-requisites", "").strip()
        description = course.findtext("description", "").strip()
        permission = course.findtext("catalog-inst-permission", "").strip()
        course_page = f"{COURSE_CONSTRUCTOR_URL}&p_crn={crn}&p_term={term_code}"

        inst_str = course.findtext("instructor", "").strip()
        instructors = [i.strip() for i in inst_str.split(";") if i.strip()] if inst_str else []

        days_str = course.findtext("meeting-days", "").strip()
        start_str = course.findtext("start-time", "").strip()
        end_str = course.findtext("end-time", "").strip()
        meeting_days = []
        if days_str and start_str and end_str:
            days = [d.strip() for d in days_str.split(",")]
            starts = [t.strip() for t in start_str.split(",")]
            ends = [t.strip() for t in end_str.split(",")]
            for index, (day, start, end) in enumerate(zip(days, starts, ends)):
                start_time = _convert_time_to_human_readable(start)
                end_time = _convert_time_to_human_readable(end)
                if not day or start_time is None or end_time is None:
                    print(
                        f"Skipping malformed meeting time for {crs} "
                        f"(term {term_code}, entry {index + 1})"
                    )
                    continue
                meeting_days.append(f"{start_time}-{end_time} {day}")

        df.append({
            "crn": crn,
            "crs": crs,
            "section": section,
            "title": title,
            "instructors": json.dumps(instructors),
            "meeting_times": json.dumps(meeting_days),
            "prerequisites": prerequisites,
            "distribution": distribution,
            "credits": credits,
            "course_page": course_page,
            "description": description,
            "permission": permission,
        })

    df = DataFrame(df)
    if sql_db_path:
        _export_sql(df, f"courses_{term_code}", sql_db_path)
    return df


def scrape_term(term_code: str, sql_db_path: str):
    get_subject_codes_for_term(term_code, sql_db_path=sql_db_path)
    get_school_codes_for_term(term_code, sql_db_path=sql_db_path)
    get_instructors_for_term(term_code, sql_db_path=sql_db_path)
    get_all_courses_for_term(term_code, sql_db_path=sql_db_path)


def construct_db(
    sql_db_path: str,
    term_codes_to_scrape: Optional[List[str]] = None,
    max_workers: int = 4,
    force: bool = False,
):
    if term_codes_to_scrape is None:
        term_codes = list(get_term_codes(sql_db_path=sql_db_path)["code"])
    else:
        term_codes = term_codes_to_scrape

    existing_terms = set()
    if os.path.exists(sql_db_path) and not force:
        con = sql.connect(sql_db_path)
        cur = con.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'courses_%'"
        )
        existing_terms = {row[0].replace("courses_", "") for row in cur.fetchall()}

        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = 'global_search'"
        )
        if cur.fetchone():
            cur.execute("SELECT DISTINCT term FROM global_search")
            existing_terms.update(
                {row[0].replace("courses_", "") for row in cur.fetchall() if row[0]}
            )
        con.close()

    pending_terms = [t for t in term_codes if t not in existing_terms]
    if not pending_terms:
        print("All requested terms already exist in the database.")
        return

    print(f"Scraping {len(pending_terms)} term(s) with {max_workers} worker(s)...")
    if max_workers <= 1:
        for term_code in tqdm(pending_terms, desc="Scraping terms"):
            scrape_term(term_code, sql_db_path=sql_db_path)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(scrape_term, term_code, sql_db_path): term_code
                for term_code in pending_terms
            }
            with tqdm(total=len(pending_terms), desc="Scraping terms") as pbar:
                for future in as_completed(futures):
                    future.result()
                    pbar.update(1)


def build_fts_index(sql_db_path: str):
    """Build or update the global_search FTS5 virtual table from all new courses tables."""
    print("Building / updating global_search FTS5 index...")
    con = sql.connect(sql_db_path)
    cur = con.cursor()

    try:
        fts_columns = [
            "term",
            "crn",
            "crs",
            "section",
            "title",
            "instructors",
            "meeting_times",
            "prerequisites",
            "distribution",
            "credits",
            "course_page",
            "description",
            "permission",
        ]
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = 'global_search'"
        )
        table_exists = cur.fetchone() is not None

        if table_exists:
            cur.execute("PRAGMA table_info(global_search)")
            existing_columns = [row[1] for row in cur.fetchall()]
            if existing_columns != fts_columns:
                cur.execute("DROP TABLE global_search")
                table_exists = False
                print("Recreated global_search FTS5 table to update its schema")

        if not table_exists:
            cur.execute("""
                CREATE VIRTUAL TABLE global_search USING fts5(
                    term,
                    crn,
                    crs,
                    section,
                    title,
                    instructors,
                    meeting_times,
                    prerequisites,
                    distribution,
                    credits,
                    course_page,
                    description,
                    permission
                )
            """)
            print("Created global_search FTS5 table")

        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'courses_%'"
        )
        tables = [row[0] for row in cur.fetchall()]
        print(f"Found {len(tables)} course term table(s) to index")

        total_inserted = 0
        for table in sorted(tables):
            cur.execute(f"PRAGMA table_info({table})")
            columns = [row[1] for row in cur.fetchall()]
            if "course_page" in columns:
                course_page_select = "course_page"
            else:
                term_code = table.replace("courses_", "")
                course_page_select = f"'{COURSE_CONSTRUCTOR_URL}&p_crn=' || crn || '&p_term={term_code}'"

            desc_select = "description" if "description" in columns else "''"
            perm_select = "permission" if "permission" in columns else "''"

            if table_exists:
                cur.execute("DELETE FROM global_search WHERE term = ?", (table,))

            cur.execute(
                f"""
                INSERT INTO global_search (term, crn, crs, section, title, instructors, meeting_times, prerequisites, distribution, credits, course_page, description, permission)
                SELECT ?, crn, crs, section, title, instructors, meeting_times, prerequisites, distribution, credits, {course_page_select}, {desc_select}, {perm_select} FROM {table}
            """,
                (table,),
            )
            rows_inserted = cur.rowcount
            total_inserted += rows_inserted
            print(f"  Indexed {rows_inserted} rows from {table}")

        con.commit()
        print(f"✓ Successfully indexed {total_inserted} total rows into global_search")

    except Exception as e:
        print(f"✗ Error building FTS index: {e}")
        con.rollback()
        raise
    finally:
        con.close()


def drop_courses_tables(sql_db_path: str):
    """Utility function to drop all courses tables from the database for reducing db size."""
    con = sql.connect(sql_db_path)
    cur = con.cursor()

    try:
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'courses_%'"
        )
        tables = [row[0] for row in cur.fetchall()]

        for table in tables:
            cur.execute(f"DROP TABLE IF EXISTS {table}")
            print(f"Dropped table {table}")

        con.commit()

    except Exception as e:
        print(f"Error dropping courses tables: {e}")
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Rice course catalog into main.db")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Scrape all terms from scratch (replaces existing main.db)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of concurrent worker threads (default: 4)",
    )
    parser.add_argument(
        "--terms",
        type=str,
        default=None,
        help="Specific comma-separated term codes to scrape (e.g. 202410,202420)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Path to main.db (defaults to data/main.db)",
    )
    args = parser.parse_args()

    final_db = os.path.abspath(args.db) if args.db else os.path.join(BASE_DB_DIR, "main.db")
    temp_db = os.path.join(BASE_DB_DIR, "main.db.tmp")
    terms_list = [t.strip() for t in args.terms.split(",") if t.strip()] if args.terms else None

    if args.full:
        if os.path.exists(temp_db):
            os.remove(temp_db)
        target_db = temp_db
    else:
        target_db = final_db

    try:
        construct_db(
            target_db,
            term_codes_to_scrape=terms_list,
            max_workers=args.workers,
            force=args.full,
        )
        build_fts_index(target_db)
        drop_courses_tables(target_db)
        if args.full:
            os.replace(temp_db, final_db)
            print(f"Swapped fresh database into {final_db}")
        else:
            print(f"Updated database at {final_db}")
    except Exception:
        if args.full and os.path.exists(temp_db):
            print(f"Preserving {temp_db} to allow resuming without losing scraped data.")
        raise
