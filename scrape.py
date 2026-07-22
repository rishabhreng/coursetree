from calendar import c
import os
import time

import bs4
import requests as r
from xml.etree import ElementTree as ET
import pandas as pd
from pandas import DataFrame
import sqlite3 as sql
import json
from tqdm import tqdm

META_COURSES_URL = "https://courses.rice.edu/courses/!SWKSCAT.info"
BASE_COURSES_URL = "https://courses.rice.edu/admweb/!SWKSECX.main"
COURSE_CONSTRUCTOR_URL = "https://courses.rice.edu/courses/courses/!SWKSCAT.cat?p_action=COURSE"
BASE_GA_URL = "https://ga.rice.edu"

BASE_DB_DIR = os.path.dirname(os.path.abspath(__file__))


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
            print(
                f"Request failed ({attempt}/{attempts}) for {url}: {e}. "
                f"Retrying in 10s..."
            )
            time.sleep(10)

    raise last_exc


def _export_sql(df: DataFrame, table_name: str, sql_db_path: str):
    try:
        con = sql.connect(sql_db_path)
        df.to_sql(name=table_name, con=con, if_exists="replace", index=False)
    except ValueError as e:
        print(e)
    finally:
        con.commit()
        con.close()


def get_term_codes(sql_db_path=None) -> DataFrame:
    # entries look like Fall Semester 2026 | 202710
    terms = _request_xml(f"{META_COURSES_URL}?action=TERMS", timeout=15).findall("TERM")
    df = []

    for term in terms:
        # ignore quadmesters since they are only for the Glasscock school, not undergraduate
        if "Quadmester" not in term.find("OPT").tail:
            df.append({"term": term.find("OPT").tail, "code": term.attrib.get("code")})

    if sql_db_path:
        _export_sql(DataFrame(df), "terms", sql_db_path)

    return DataFrame(df)


def get_subject_codes_for_term(term_code: str, sql_db_path=None) -> DataFrame:
    # entries look like "Computer Science | COMP"
    # all subject codes are 4 letters
    subjects = _request_xml(
        f"{META_COURSES_URL}?action=SUBJECTS&term={term_code}", timeout=15
    ).findall("SUBJECT")
    df = []

    for subject in subjects:
        df.append({"subject": subject.find("OPT").tail, "code": subject.attrib.get("code")})

    df = DataFrame(df)

    if sql_db_path:
        _export_sql(df, f"subjects_{term_code}", sql_db_path)

    return df


def get_school_codes_for_term(term_code: str, sql_db_path=None) -> DataFrame:
    # entries look like "School of Engineering and Computing | EN"
    # all school codes are 2 letters
    schools = _request_xml(
        f"{META_COURSES_URL}?action=SCHOOLS&term={term_code}", timeout=15
    ).findall("SCHOOL")
    df = []

    for school in schools:
        df.append({"school": school.find("OPT").tail, "code": school.attrib.get("code")})

    df = DataFrame(df)
    if sql_db_path:
        _export_sql(df, f"schools_{term_code}", sql_db_path)

    return df


def _convert_time_to_human_readable(time_str: str) -> str:
    # time string in format "HHMM" in 24-hour time
    hh = int(time_str[:2])
    mm = time_str[2:]
    am_pm = "AM" if hh < 12 else "PM"
    hh = hh % 12
    hh = 12 if hh == 0 else hh
    return f"{hh}:{mm} {am_pm}"


def get_all_courses_for_term(term_code: str, sql_db_path=None) -> DataFrame:
    courses = _request_xml(f"{BASE_COURSES_URL}?term={term_code}", timeout=60).findall("course")

    df = []

    for course in courses:
        crn = course.find("crn").text
        subject = course.find("subject").text
        course_number = course.find("course-number").text
        crs = f"{subject} {course_number}"
        title = course.find("title").text

        try:
            instructors = [instructor.strip() for instructor in course.find("instructor").text.split(";")]
        except Exception:
            instructors = []

        try:
            meeting_days = [day.strip() for day in course.find("meeting-days").text.split(", ")]
            _start_time = [
                _convert_time_to_human_readable(time.strip())
                for time in course.find("start-time").text.split(", ")
            ]
            _end_time = [
                _convert_time_to_human_readable(time.strip())
                for time in course.find("end-time").text.split(", ")
            ]

            assert len(meeting_days) == len(_start_time) == len(_end_time), (
                f"Meeting days, start times, and end times must have the same length for course {crn}"
            )

            if len(meeting_days) == 1:
                meeting_days[0] = f"{_start_time[0]}-{_end_time[0]} {meeting_days[0]}"
            else:
                for i in range(len(meeting_days)):
                    meeting_days[i] = f"{_start_time[i]}-{_end_time[i]} {meeting_days[i]}"
        except Exception:
            meeting_days = []

        try:
            distribution = course.find("distribution-group").text
        except Exception:
            distribution = ""
        section = course.find("section").text
        try:
            prerequisites = course.find("pre-requisites").text
        except Exception:
            prerequisites = ""
        credits = course.find("credit-hours").text
        course_page = f"{COURSE_CONSTRUCTOR_URL}&p_crn={crn}&p_term={term_code}"

        df.append(
            {
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
            }
        )

    df = DataFrame(df)
    if sql_db_path:
        _export_sql(df, f"courses_{term_code}", sql_db_path)

    return df

# unused
def get_programs() -> DataFrame:
    req = r.get(f"{BASE_GA_URL}/programs-study/", timeout=15)
    parser = bs4.BeautifulSoup(req.text, "html.parser")
    df = []
    # find ul content with tag "class"="sitemap"
    programs = parser.find("div", class_="sitemap").find_all("li")
    for program in programs:
        df.append({"program": program.text, "url": program.a["href"]})
    return DataFrame(df)


def get_instructors_for_term(term_code: str, sql_db_path=None) -> DataFrame:
    instructors = _request_xml(
        f"https://esther.rice.edu/selfserve/!swkscmp.ajax?p_data=INSTRUCTORS&p_term={term_code}",
        timeout=15,
    ).findall("INSTRUCTOR")
    df = []

    for instructor in instructors:
        df.append(
            {
                "name": instructor.attrib.get("NAME", ""),
                "id": instructor.attrib.get("WEBID", "0"),
            }
        )

    df = DataFrame(df)

    if sql_db_path and not df.empty:
        _export_sql(df, f"instructors_{term_code}", sql_db_path)

    return df


def construct_db(sql_db_path: str):
    term_codes = get_term_codes(sql_db_path=sql_db_path)["code"]
    for term_code in tqdm(term_codes):
        get_subject_codes_for_term(term_code, sql_db_path=sql_db_path)
        get_school_codes_for_term(term_code, sql_db_path=sql_db_path)
        get_instructors_for_term(term_code, sql_db_path=sql_db_path)
        get_all_courses_for_term(term_code, sql_db_path=sql_db_path)


def build_fts_index(sql_db_path: str):
    """Build the global_search FTS5 virtual table from all course tables."""
    print("Building global_search FTS5 index...")
    con = sql.connect(sql_db_path)
    cur = con.cursor()

    try:
        # Drop existing FTS table if it exists
        cur.execute("DROP TABLE IF EXISTS global_search")
        print("Dropped existing global_search table")

        # Create FTS5 virtual table
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
                course_page
            )
        """)

        print("Created global_search FTS5 table")

        # Get all course term tables
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'courses_%'"
        )
        tables = [row[0] for row in cur.fetchall()]
        print(f"Found {len(tables)} course term table(s)")

        # Populate FTS table from all course tables
        total_inserted = 0
        for table in sorted(tables):
            cur.execute(
                f"""
                INSERT INTO global_search (term, crn, crs, section, title, instructors, meeting_times, prerequisites, distribution, credits, course_page)
                SELECT ?, crn, crs, section, title, instructors, meeting_times, prerequisites, distribution, credits, course_page FROM {table}
            """,
                (table,),
            )
            rows_inserted = cur.rowcount
            total_inserted += rows_inserted
            print(f"  Inserted {rows_inserted} rows from {table}")

        con.commit()
        print(f"✓ Successfully built global_search FTS index with {total_inserted} total rows")

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
    final_db = os.path.join(BASE_DB_DIR, "main.db")
    temp_db = os.path.join(BASE_DB_DIR, "main.db.tmp")

    if os.path.exists(temp_db):
        os.remove(temp_db)

    try:
        construct_db(temp_db)
        build_fts_index(temp_db)
        drop_courses_tables(temp_db)
        os.replace(temp_db, final_db)
        print(f"Swapped updated database into {final_db}")
    except Exception:
        if os.path.exists(temp_db):
            os.remove(temp_db)
        raise
