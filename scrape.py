from calendar import c

import bs4
import requests as r
from xml.etree import ElementTree as ET
import pandas as pd
from pandas import DataFrame
import sqlite3 as sql

META_COURSES_URL = 'https://courses.rice.edu/courses/!SWKSCAT.info'
BASE_COURSES_URL = 'https://courses.rice.edu/'
BASE_GA_URL = 'https://ga.rice.edu'

def get_term_codes() -> DataFrame:    
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

    return DataFrame(df)

def get_subject_codes_for_term(term_code: str, export_to_sql=False) -> DataFrame:
    req = r.get(f"{META_COURSES_URL}?action=SUBJECTS&term={term_code}", timeout=15) 
    subjects = ET.fromstring(req.text).findall('SUBJECT')
    df = []

    for subject in subjects:
        df.append({
            'subject': subject.find('OPT').tail,
            'code': subject.attrib.get('code')
        })
    
    df = DataFrame(df)

    if export_to_sql:
        try:
            con = sql.connect(f'subjects.db')
            df.to_sql(name = f'subjects_{term_code}', con=con, if_exists='replace', index=False)
        except ValueError as e:
            print(e)
        finally:
            con.commit()
            con.close()

    return df

def get_schools_for_term(term_code: str, export_to_sql=False) -> DataFrame:
    req = r.get(f"{META_COURSES_URL}?action=SCHOOLS&term={term_code}", timeout=15) 
    schools = ET.fromstring(req.text).findall('SCHOOL')
    df = []

    for school in schools:
        df.append({
            'school': school.find('OPT').tail,
            'code': school.attrib.get('code')
        })

    df = DataFrame(df)
    if export_to_sql:
        try:
            con = sql.connect(f'schools.db')
            df.to_sql(f'schools_{term_code}', con, index=False, if_exists='replace')
        except ValueError as e:
            print(e)
        finally:
            con.commit()
            con.close()

    return df

def get_all_courses_for_term(term_code: str, export_to_sql = False) -> DataFrame:
    schools = get_schools_for_term(term_code)
    all_courses = []

    for school_code in schools['code']:
        all_courses.append(get_course_info(term_code, school_code))
    
    df = pd.concat(all_courses, ignore_index=True)
    if export_to_sql:
        try: 
            con = sql.connect(f'courses.db')
            df.to_sql(f'courses_{term_code}', con, index=False, if_exists='replace')
        except ValueError as e:
            print(e)
        finally:
            con.commit()
            con.close()

    return df

def get_course_info(term_code: str, school_code: str) -> DataFrame:
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
            'instructors': cells[4].text,
            'meeting_times': cells[5].find('div').text.strip(), # ignore final exam time
            'credits': cells[6].text,
            'course_page': f"{BASE_COURSES_URL}{cells[0].a['href']}"
        })

    return DataFrame(courses)

def get_course_syllabus(term_code: str, crn: str):
    req = r.get(f"{META_COURSES_URL}?action=SYLLABUS&term={term_code}&crn={crn}", timeout=15)
    res = ET.fromstring(req.text)
    if res.attrib.get('has-syllabus') != 'yes':
        return None
    return res.attrib.get('doc-url')

def get_course_description(term_code: str, crn: str) -> str:
    req = r.get(f"{BASE_COURSES_URL}/courses/courses/!SWKSCAT.cat?p_action=COURSE&p_term={term_code}&p_crn={crn}", timeout=15)
    if req.status_code != 200:
        raise ValueError(f"Failed to get course description for {term_code} {crn}")
    parser = bs4.BeautifulSoup(req.text, 'html.parser')
    return parser.find_all('b')[-1].parent.text.split('Description: ')[-1].strip()

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

def construct_subject_code_db():
    term_codes = get_term_codes()
    for term_code in term_codes['code']:
        get_subject_codes_for_term(term_code, export_to_sql=True)

def construct_school_db():
    term_codes = get_term_codes()
    for term_code in term_codes['code']:
        get_schools_for_term(term_code, export_to_sql=True)

def construct_course_db():
    term_codes = get_term_codes()
    for term_code in term_codes['code']:
        get_all_courses_for_term(term_code, export_to_sql=True)

if __name__ == "__main__":
    # construct_subject_code_db()
    # print("Finished constructing subject code DB")
    # construct_school_db()
    # print("Finished constructing school DB")
    # construct_course_db()
    # print("Finished constructing course DB")
    get_all_courses_for_term('202020', export_to_sql=True)
    get_all_courses_for_term('202120', export_to_sql=True)
    get_all_courses_for_term('202220', export_to_sql=True)
    get_all_courses_for_term('202320', export_to_sql=True)
    get_all_courses_for_term('202420', export_to_sql=True)
    get_all_courses_for_term('202520', export_to_sql=True)
    get_all_courses_for_term('202620', export_to_sql=True)
