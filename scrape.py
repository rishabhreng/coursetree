from calendar import c
import os

import bs4
import requests as r
from xml.etree import ElementTree as ET
import pandas as pd
from pandas import DataFrame
import sqlite3 as sql
import json
from tqdm import tqdm

META_COURSES_URL = 'https://courses.rice.edu/courses/!SWKSCAT.info'
BASE_COURSES_URL = 'https://courses.rice.edu/'
BASE_GA_URL = 'https://ga.rice.edu'

BASE_DB_DIR = os.path.dirname(os.path.abspath(__file__))

def _export_sql(df: DataFrame, table_name: str, sql_db_path: str):
    try:
        con = sql.connect(sql_db_path)
        df.to_sql(name = table_name, con=con, if_exists='replace', index=False)
    except ValueError as e:
        print(e)
    finally:
        con.commit()
        con.close()

def get_term_codes(sql_db_path=None) -> DataFrame:
    # entries look like Fall Semester 2026 | 202710
    req = r.get(f"{META_COURSES_URL}?action=TERMS", timeout=15) 
    terms = ET.fromstring(req.text).findall('TERM')
    df = []

    for term in terms:
        # ignore quadmesters since they are only for the Glasscock school, not undergraduate
        if not "Quadmester" in term.find('OPT').tail:
            df.append({
                'term': term.find('OPT').tail,
                'code': term.attrib.get('code')
            })

    if sql_db_path:
        _export_sql(DataFrame(df), 'terms', sql_db_path)

    return DataFrame(df)

def get_subject_codes_for_term(term_code: str, sql_db_path=None) -> DataFrame:
    # entries look like "Computer Science | COMP"
    # all subject codes are 4 letters
    req = r.get(f"{META_COURSES_URL}?action=SUBJECTS&term={term_code}", timeout=15) 
    subjects = ET.fromstring(req.text).findall('SUBJECT')
    df = []

    for subject in subjects:
        df.append({
            'subject': subject.find('OPT').tail,
            'code': subject.attrib.get('code')
        })
    
    df = DataFrame(df)

    if sql_db_path:
        _export_sql(df, f'subjects_{term_code}', sql_db_path)

    return df

def get_school_codes_for_term(term_code: str, sql_db_path=None) -> DataFrame:
    # entries look like "School of Engineering and Computing | EN"
    # all school codes are 2 letters
    req = r.get(f"{META_COURSES_URL}?action=SCHOOLS&term={term_code}", timeout=15) 
    schools = ET.fromstring(req.text).findall('SCHOOL')
    df = []

    for school in schools:
        df.append({
            'school': school.find('OPT').tail,
            'code': school.attrib.get('code')
        })

    df = DataFrame(df)
    if sql_db_path:
        _export_sql(df, f'schools_{term_code}', sql_db_path)

    return df

def get_all_courses_for_term(term_code: str, sql_db_path=None) -> DataFrame:
    school_codes = get_school_codes_for_term(term_code)['code']
    all_courses = []

    for school_code in school_codes:
        all_courses.append(_get_all_courses_for_term_and_school_code(term_code, school_code))
    
    df = pd.concat(all_courses, ignore_index=True)
    
    if sql_db_path:
        _export_sql(df, f'courses_{term_code}', sql_db_path)

    return df

def _get_all_courses_for_term_and_school_code(term_code: str, school_code: str) -> DataFrame:
    courses = []
    req = r.get(f"{BASE_COURSES_URL}/courses/courses/!SWKSCAT.cat?p_action=QUERY&p_term={term_code}&p_school={school_code}", timeout=15)
    parser = bs4.BeautifulSoup(req.text, 'html.parser')
    rows = parser.find('tbody').find_all('tr')
    rows[0].find_all('td')

    for row in parser.find('tbody').find_all('tr'):
        cells = row.find_all('td')
        if len(cells) != 7:
            raise ValueError(f"Expected 7 cells per row, got {len(cells)}")
        courses.append({
            'crn': cells[0].text,
            'crs': cells[1].text,
            'title': cells[3].text, #ignore "part of term" entry
            'instructors': json.dumps([instructor.text for instructor in cells[4].find_all('a')]),
            'meeting_times': json.dumps(list(cells[5].find('div', class_='mtg-clas').stripped_strings)), # ignore final exam time
            'credits': cells[6].text,
            'course_page': f"{BASE_COURSES_URL}{cells[0].a['href']}"
        })

    return DataFrame(courses)

# unused
def get_course_syllabus(term_code: str, crn: str):
    req = r.get(f"{META_COURSES_URL}?action=SYLLABUS&term={term_code}&crn={crn}", timeout=15)
    res = ET.fromstring(req.text)
    if res.attrib.get('has-syllabus') != 'yes':
        return None
    return res.attrib.get('doc-url')

# unused
def get_course_description(term_code: str, crn: str) -> str:
    req = r.get(f"{BASE_COURSES_URL}/courses/courses/!SWKSCAT.cat?p_action=COURSE&p_term={term_code}&p_crn={crn}", timeout=15)
    if req.status_code != 200:
        raise ValueError(f"Failed to get course description for {term_code} {crn}")
    parser = bs4.BeautifulSoup(req.text, 'html.parser')
    return parser.find_all('b')[-1].parent.text.split('Description: ')[-1].strip()

# unused
def get_programs() -> DataFrame:
    req = r.get(f"{BASE_GA_URL}/programs-study/", timeout=15)
    parser = bs4.BeautifulSoup(req.text, 'html.parser')
    df = []
    # find ul content with tag "class"="sitemap"
    programs = parser.find('div', class_='sitemap').find_all('li')
    for program in programs:
        df.append({
            'program': program.text,
            'url': program.a['href']})
    return DataFrame(df)

def construct_db():
    term_codes = get_term_codes(sql_db_path=f'{BASE_DB_DIR}/main.db')['code']
    for term_code in tqdm(term_codes):
        get_subject_codes_for_term(term_code, sql_db_path=f'{BASE_DB_DIR}/main.db')
        get_school_codes_for_term(term_code, sql_db_path=f'{BASE_DB_DIR}/main.db')
        get_all_courses_for_term(term_code, sql_db_path=f'{BASE_DB_DIR}/main.db')

def build_fts_index():
    """Build the global_search FTS5 virtual table from all course tables."""
    print("Building global_search FTS5 index...")
    con = sql.connect(f'{BASE_DB_DIR}/main.db')
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
                title,
                instructors,
                meeting_times,
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
            cur.execute(f"""
                INSERT INTO global_search (term, crn, crs, title, instructors, meeting_times, credits, course_page)
                SELECT ?, crn, crs, title, instructors, meeting_times, credits, course_page FROM {table}
            """, (table,))
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

def drop_courses_tables():
    """Utility function to drop all courses tables from the database for reducing db size."""
    con = sql.connect(f'{BASE_DB_DIR}/main.db')
    cur = con.cursor()
    
    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'courses_%'")
        tables = [row[0] for row in cur.fetchall()]
        
        for table in tables:
            cur.execute(f"DROP TABLE IF EXISTS {table}")
            print(f"Dropped table {table}")
        
        con.commit()
        
    except Exception as e:
        print(f"✗ Error dropping courses tables: {e}")
        con.rollback()
        raise
    finally:
        con.close()

if __name__ == "__main__":
    construct_db()
    build_fts_index()
    drop_courses_tables()

