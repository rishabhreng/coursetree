import os
import sys
import json
import time
import re
import argparse
import getpass
import sqlite3 as sql
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional, Any
from urllib.parse import parse_qs, urlparse, unquote

import logging
from tqdm import tqdm
import requests as r
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup

# Path constants
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EVALS_DB_PATH = os.path.join(BASE_DIR, "..", "data", "evals.db")
EVAL_URL = "https://esther.rice.edu/selfserve/swkscmt.main"
TERMS_URL = "https://esther.rice.edu/selfserve/!swkscmp.ajax?p_data=TERMS"
COURSES_URL = "https://esther.rice.edu/selfserve/!swkscmp.ajax?p_data=COURSES&p_term={term}"
INSTRUCTORS_URL = "https://esther.rice.edu/selfserve/!swkscmp.ajax?p_data=INSTRUCTORS&p_term={term}"


logger = logging.getLogger(__name__)


def extract_chart_data(img_src: str, response_count: int = None) -> Optional[Dict]:
    """Extract raw percentage chart data from ObjectPlanet chart servlet URL."""
    try:
        parsed_url = urlparse(img_src)
        params = parse_qs(parsed_url.query)

        values_str = params.get("sampleValues", [""])[0]
        labels_str = params.get("sampleLabels", [""])[0]
        title = unquote(params.get("chartTitle", [""])[0])

        if not values_str or not labels_str:
            return None

        percentage_values = [int(x) for x in values_str.split(",") if x.isdigit()]

        labels = []
        for label in labels_str.split(","):
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
        logger.warning(f"Failed to parse chart data from URL: {e}")
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


def init_db(db_path: str):
    """Ensure evaluations table exists."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        conn = sql.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS evaluations (
                term TEXT,
                crn TEXT,
                subject TEXT,
                course_code TEXT,
                title TEXT,
                html TEXT,
                charts_json TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (term, crn)
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_evaluations_term_crn ON evaluations(term, crn);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_evaluations_course_code ON evaluations(course_code);")
        conn.commit()
        conn.close()
    except sql.Error as e:
        logger.critical(f"Failed to initialize database at {db_path}: {e}")
        raise


def authenticate_playwright(headless: Optional[bool] = None, netid: Optional[str] = None, password: Optional[str] = None) -> r.Session:
    """Launches Playwright once to log into ESTHER and returns a requests.Session with active cookies."""
    from playwright.sync_api import sync_playwright

    # Auto-detect if headless is needed when no graphical DISPLAY is available
    if headless is None:
        headless = os.environ.get("DISPLAY") is None and sys.platform.startswith("linux")

    session = r.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })

    if headless and (not netid or not password):
        print("\n--- ESTHER Headless Login ---")
        if not netid:
            netid = input("Enter Rice NetID: ").strip()
        if not password:
            password = getpass.getpass("Enter Rice Password: ").strip()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        print("Navigating to ESTHER login page...")
        page.goto("https://esther.rice.edu/")

        if headless and netid and password:
            print("Submitting login credentials...")
            page.fill("#username", netid)
            page.fill("#password", password)
            page.keyboard.press("Enter")
            print("📲 Credentials submitted! Please approve the Duo Push notification on your phone (waiting up to 60s)...")

        print("Waiting for login & Duo approval...")

        # Poll until authenticated cookies (e.g. SESSID) or ESTHER menu elements appear
        authenticated = False
        start_wait = time.time()

        while time.time() - start_wait < 90:
            current_url = page.url
            cookies = context.cookies()
            cookie_names = {c["name"] for c in cookies if "rice.edu" in c.get("domain", "")}

            has_session_cookie = "SESSID" in cookie_names or "IDMSESSID" in cookie_names
            is_esther_page = "esther.rice.edu" in current_url and ("P_GenMnu" in current_url or "P_MainMnu" in current_url or "swkscmt" in current_url or "twbkwbis" in current_url)

            try:
                content = page.content()
                has_login_text = "Personal Information" in content or "Student Services" in content or "Course and Instructor Evaluation" in content
            except Exception:
                has_login_text = False

            if (has_session_cookie and is_esther_page) or has_login_text:
                authenticated = True
                break

            time.sleep(1)

        if not authenticated:
            browser.close()
            raise TimeoutError("Authentication timed out waiting for Duo push or ESTHER login.")

        print("Navigating to Evaluation portal to establish selfserve session...")
        try:
            page.goto("https://esther.rice.edu/selfserve/swkscmt.main", timeout=20000)
            page.wait_for_timeout(1500)
        except Exception:
            pass

        print("✓ Successfully authenticated with ESTHER!")

        # Transfer all cookies to requests.Session
        for cookie in context.cookies():
            session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain"),
                path=cookie.get("path", "/"),
            )

        browser.close()

    # Configure connection pooling adapter for high-concurrency requests
    adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100, max_retries=3)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    # Warm up and verify session
    print("Verifying ESTHER evaluation session...")
    test_res = session.get(EVAL_URL, timeout=15)
    if "Course and Instructor Evaluation" not in test_res.text and "swkscmt" not in test_res.text and "Personal Information" not in test_res.text:
        print(f"[!] Warning: ESTHER response does not seem authenticated (status {test_res.status_code})")
    else:
        print("✓ ESTHER evaluation session verified and ready!")

    return session


def get_as_fid(session: r.Session) -> Optional[str]:
    """Extract as_fid CSRF/form token from ESTHER evaluation page."""
    try:
        get_res = session.get(EVAL_URL, timeout=15)
        soup = BeautifulSoup(get_res.text, "html.parser")
        as_fid = None
        form = soup.find("form")
        if form:
            as_fid_input = form.find("input", {"name": "as_fid"})
            if as_fid_input and as_fid_input.get("value"):
                as_fid = as_fid_input.get("value")
        if not as_fid:
            match = re.search(r'as_fid["\']?\s*[:=]\s*["\']?([a-f0-9]{40})', get_res.text)
            if match:
                as_fid = match.group(1)
        return as_fid
    except Exception as e:
        print(f"[!] Warning fetching as_fid: {e}")
        return None


def scrape_course_eval(session: r.Session, term: str, crn: str, subject: str = "") -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    payload = {
        "p_commentid": "",
        "p_confirm": "1",
        "p_term": term,
        "p_type": "Course",
        "p_crn": crn
    }
  
    try:
        res = session.post(EVAL_URL, data=payload, timeout=15)
        if res.status_code != 200:
            return None, f"HTTP {res.status_code}"

        if "Course and Instructor Evaluation Display" not in res.text:
            if "User session has expired" in res.text or "Sign On" in res.text:
                return None, "ESTHER session expired"
            return None, "No evaluation record on ESTHER"

        soup = BeautifulSoup(res.text, "html.parser")
        results_container = soup.find("div", class_="results-container")
        if not results_container:
            return None, "Missing results-container"

        charts_data = extract_charts_from_results(results_container)

        return {
            "html": str(results_container),
            "charts_json": json.dumps(charts_data),
        }, None
    except Exception as e:
        return None, f"Request error: {str(e)}"

def fetch_eval_terms(term_code: Optional[str] = None) -> List[str]:
    """Fetch available evaluation term codes from the public ESTHER TERMS endpoint.
    
    If term_code is provided, returns only that term (if it exists in the response).
    Otherwise returns all available term codes.
    """
    if term_code:
        return [term_code]

    try:
        res = r.get(TERMS_URL, timeout=15)
        res.raise_for_status()
        root = ET.fromstring(res.text)
        terms = [term_el.get("CODE") for term_el in root.findall("TERM") if term_el.get("CODE")]
        logger.info(f"Fetched {len(terms)} terms from ESTHER TERMS endpoint")
        return sorted(terms)
    except r.RequestException as e:
        logger.error(f"HTTP error fetching terms from ESTHER: {e}")
        return []
    except ET.ParseError as e:
        logger.error(f"XML parse error on TERMS response: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching terms from ESTHER: {e}")
        return []


def get_courses_to_scrape(evals_db_path: str, term_code: Optional[str] = None, skip_existing: bool = True) -> List[Dict]:
    """Fetch courses with available evaluations from the public ESTHER AJAX endpoints.
    
    Instead of querying main.db, this fetches the COURSES XML for each term from
    https://esther.rice.edu/selfserve/!swkscmp.ajax?p_data=COURSES&p_term=[termcode]
    which only lists courses that actually have evaluations.
    """
    terms = fetch_eval_terms(term_code)
    if not terms:
        print("No terms found to scrape.")
        return []

    # Load existing (term, crn) pairs to skip
    existing = set()
    if skip_existing and os.path.exists(evals_db_path):
        conn_evals = sql.connect(evals_db_path)
        cur_evals = conn_evals.cursor()
        try:
            cur_evals.execute("SELECT term, crn FROM evaluations")
            existing = set(cur_evals.fetchall())
        except sql.OperationalError:
            pass
        finally:
            conn_evals.close()

    courses = []
    print(f"Fetching course lists for {len(terms)} term(s) from ESTHER...")
    for term in tqdm(terms, desc="Fetching terms", unit="term"):
        try:
            res = r.get(COURSES_URL.format(term=term), timeout=15)
            res.raise_for_status()
            root = ET.fromstring(res.text)
        except r.RequestException as e:
            logger.error(f"HTTP error fetching courses for term {term}: {e}")
            continue
        except ET.ParseError as e:
            logger.error(f"XML parse error on COURSES response for term {term}: {e}")
            continue
        except Exception as e:
            logger.error(f"Unexpected error fetching courses for term {term}: {e}")
            continue

        for course_el in root.findall("COURSE"):
            crn = course_el.get("CRN", "")
            subj = course_el.get("SUBJ", "")
            numb = course_el.get("NUMB", "")
            title = course_el.get("TITLE", "")

            if not crn:
                continue

            if skip_existing and (term, crn) in existing:
                continue

            crs = f"{subj} {numb}" if subj and numb else subj or numb
            courses.append({
                "term": term,
                "crn": crn,
                "crs": crs,
                "subject": subj,
                "title": title,
            })

    return courses


def main():
    parser = argparse.ArgumentParser(description="CourseTree ESTHER Evaluation Scraper")
    parser.add_argument("--term", type=str, help="Specific term code to scrape (e.g. 202510). Default scrapes all.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of courses to scrape for testing.")
    parser.add_argument("--delay", type=float, default=0.0, help="Delay between requests in seconds (default: 0.0s).")
    parser.add_argument("--no-skip", action="store_true", help="Do not skip already scraped courses.")
    parser.add_argument("--netid", type=str, default=None, help="Rice NetID (optional, prompts if in headless mode).")
    parser.add_argument("--password", type=str, default=None, help="Rice Password (optional, prompts if in headless mode).")
    args = parser.parse_args()

    LOG_PATH = os.path.join(BASE_DIR, "scrape_evals.log")
    logging.basicConfig(
        filename=LOG_PATH,
        filemode='a',
        format='%(asctime)s - %(levelname)s - %(message)s',
        level=logging.INFO,
        force=True
    )
    # Also log to stderr for visibility
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))
    logging.getLogger().addHandler(console_handler)

    logger.info("=== Starting scrape_evals run ===")

    init_db(EVALS_DB_PATH)

    courses = get_courses_to_scrape(EVALS_DB_PATH, term_code=args.term, skip_existing=not args.no_skip)
    if args.limit:
        courses = courses[:args.limit]

    print(f"Found {len(courses)} courses to scrape evaluations for.")
    logger.info(f"Found {len(courses)} courses to scrape evaluations for (term={args.term}, skip_existing={not args.no_skip})")
    if not courses:
        print("Nothing to scrape! All courses already in database.")
        return

    # Authenticate
    try:
        session = authenticate_playwright(headless=True, netid=args.netid, password=args.password)
    except Exception as e:
        logger.critical(f"Authentication failed: {e}")
        print(f"[!] Authentication failed: {e}")
        return

    # Connect to DB for inserting
    conn = sql.connect(EVALS_DB_PATH)
    cur = conn.cursor()

    success_count = 0
    empty_count = 0
    error_count = 0
    session_expired = False
    start_time = time.time()
    total_courses = len(courses)

    print(f"\nStarting sequential scrape of {total_courses} courses...")
    try:
        for c in tqdm(courses, desc="Scraping Evals", unit="course"):
            if session_expired:
                logger.critical("ESTHER session expired mid-scrape. Stopping.")
                print("\n[!] ESTHER session expired. Stopping scrape. Progress saved.")
                break

            if args.delay > 0:
                time.sleep(args.delay)

            try:
                eval_data, reason = scrape_course_eval(session, c["term"], c["crn"], c["subject"])
            except Exception as e:
                error_count += 1
                logger.error(f"Unhandled error scraping {c['crs']} (CRN {c['crn']}, term {c['term']}): {e}")
                continue

            if eval_data:
                try:
                    cur.execute("""
                        INSERT OR REPLACE INTO evaluations (term, crn, subject, course_code, title, html, charts_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        c["term"],
                        c["crn"],
                        c["subject"],
                        c["crs"],
                        c["title"],
                        eval_data["html"],
                        eval_data["charts_json"]
                    ))
                    conn.commit()
                    success_count += 1
                    logger.info(f"  [+] Saved course eval: {c['crs']} ({c['title']}) | Term: {c['term']} | CRN: {c['crn']}")
                except sql.Error as e:
                    error_count += 1
                    logger.error(f"DB insert error for {c['crs']} (CRN {c['crn']}, term {c['term']}): {e}")
            else:
                if reason and "session expired" in reason.lower():
                    session_expired = True
                empty_count += 1
                logger.info(f"  [-] No course eval: {c['crs']} ({c['title']}) | Term: {c['term']} | CRN: {c['crn']} [{reason}]")

    except KeyboardInterrupt:
        print("\nScraping paused by user. Progress saved.")
        logger.info("Scraping interrupted by user (KeyboardInterrupt)")
    except Exception as e:
        logger.critical(f"Fatal error in scrape loop: {e}", exc_info=True)
        print(f"\n[!] Fatal error: {e}")
    finally:
        conn.commit()
        conn.close()

    elapsed = time.time() - start_time
    summary = (
        f"\n=======================================================\n"
        f"✓ Scraping completed/paused in {elapsed:.1f}s\n"
        f"  Total processed: {success_count + empty_count + error_count}\n"
        f"  Evaluations saved: {success_count}\n"
        f"  Courses without evaluations: {empty_count}\n"
        f"  Errors: {error_count}\n"
        f"  Saved to: {EVALS_DB_PATH}\n"
        f"  Log: {LOG_PATH}\n"
        f"======================================================="
    )
    print(summary)
    logger.info(summary)


if __name__ == "__main__":
    main()
