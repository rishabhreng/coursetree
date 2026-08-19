import os
import sys
import json
import time
import re
import argparse
import getpass
import sqlite3 as sql
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional, Any, Tuple
from urllib.parse import parse_qs, urlparse, unquote

import logging
from tqdm import tqdm
import requests as r
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup

# Path constants
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTRUCTOR_EVALS_DB_PATH = os.path.join(BASE_DIR, "..", "data", "instructor_evals.db")
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
    """Ensure instructor evaluations table exists."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        conn = sql.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS instructor_evaluations (
                term TEXT,
                crn TEXT,
                subject TEXT,
                course_code TEXT,
                title TEXT,
                instructor_id TEXT,
                instructor_name TEXT,
                html TEXT,
                charts_json TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (term, crn, instructor_id)
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_instructor_evals_term_crn ON instructor_evaluations(term, crn);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_instructor_evals_course_code ON instructor_evaluations(course_code);")
        conn.commit()
        conn.close()
    except sql.Error as e:
        logger.critical(f"Failed to initialize database at {db_path}: {e}")
        raise


def authenticate_playwright(headless: Optional[bool] = None, netid: Optional[str] = None, password: Optional[str] = None) -> r.Session:
    """Launches Playwright once to log into ESTHER and returns a requests.Session with active cookies."""
    from playwright.sync_api import sync_playwright

    session = r.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })

    if not netid or not password:
        print("\n--- ESTHER Headless Login ---")
        if not netid:
            netid = input("Enter Rice NetID: ").strip()
        if not password:
            password = getpass.getpass("Enter Rice Password: ").strip()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        print("Navigating to ESTHER login page...")
        page.goto("https://esther.rice.edu/")

        if netid and password:
            print("Submitting login credentials...")
            page.fill("#username", netid)
            page.fill("#password", password)
            page.keyboard.press("Enter")
            print("Credentials submitted! Please approve the Duo Push notification on your phone...")

        print("Waiting for login & Duo approval...")

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

        print("Successfully authenticated with ESTHER!")

        for cookie in context.cookies():
            session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain"),
                path=cookie.get("path", "/"),
            )

        # browser.close()

    adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100, max_retries=3)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    print("Verifying ESTHER evaluation session...")
    test_res = session.get(EVAL_URL, timeout=15)
    if "Course and Instructor Evaluation" not in test_res.text and "swkscmt" not in test_res.text and "Personal Information" not in test_res.text:
        print(f"[!] Warning: ESTHER response does not seem authenticated (status {test_res.status_code})")
    else:
        print("✓ ESTHER evaluation session verified and ready!")

    return session


def scrape_instructor_eval(session: r.Session, term: str, instr_id: str) -> tuple[Optional[str], Optional[str]]:
    """Scrape the full instructor evaluation page for a given term and instructor WEBID.
    
    Returns (html_text, error_reason). The HTML contains all course sections for
    this instructor in this term.
    """
    payload = {
        "p_commentid": "",
        "p_confirm": "1",
        "p_term": term,
        "p_type": "Instructor",
        "p_instr": instr_id
    }
  
    try:
        res = session.post(EVAL_URL, data=payload, timeout=15)
        if res.status_code != 200:
            logger.warning(f"HTTP {res.status_code} for instructor {instr_id}, term {term}")
            return None, f"HTTP {res.status_code}"

        if "Course and Instructor Evaluation Display" not in res.text:
            if "User session has expired" in res.text or "Sign On" in res.text:
                return None, "ESTHER session expired"
            # Log a snippet of the response for debugging
            snippet = res.text[:500].replace('\n', ' ').strip()
            logger.debug(f"No eval display for instructor {instr_id}, term {term}. Response snippet: {snippet}")
            return None, "No evaluation record on ESTHER"

        return res.text, None
    except r.RequestException as e:
        logger.error(f"Request error scraping instructor {instr_id}, term {term}: {e}")
        return None, f"Request error: {str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error scraping instructor {instr_id}, term {term}: {e}")
        return None, f"Request error: {str(e)}"


def parse_instructor_page(html_text: str) -> List[Dict[str, Any]]:
    """Parse an instructor evaluation page into per-course section records.
    
    The ESTHER instructor page contains multiple course sections, each wrapped
    in a <div data-crn="XXXXX">. Inside each:
      - div.results-header > div.left-box > table has course metadata
      - a.toggleNumericResponses has text like "LING 318 001 (10874) - STRUCTURE OF FRENCH"
      - div.results-container (id="results-{crn}") has the charts and comments
    
    Returns a list of dicts with keys: crn, subject, course_code, title, html, charts_json.
    """
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        
        # Each course section is a div with a data-crn attribute
        crn_divs = soup.find_all("div", attrs={"data-crn": True})
        if not crn_divs:
            logger.debug("No div[data-crn] elements found in instructor page")
            return []

        sections = []
        for crn_div in crn_divs:
            crn = crn_div.get("data-crn", "")
            if not crn:
                continue

            subject, course_code, title = "", "", ""

            # Extract course info from the toggleNumericResponses link
            # e.g. "LING 318 001 (10874) - STRUCTURE OF FRENCH"
            course_link = crn_div.find("a", class_="toggleNumericResponses")
            if course_link:
                link_text = course_link.get_text(strip=True)
                match = re.search(r"([A-Z]{2,4})\s+(\d{3})\s+\d{3}\s+\(\d+\)\s*-\s*(.+)", link_text)
                if match:
                    subject = match.group(1)
                    course_code = f"{match.group(1)} {match.group(2)}"
                    title = match.group(3).strip()

            # Find the results-container for this CRN's charts/comments
            results_container = crn_div.find("div", class_="results-container")
            if not results_container:
                logger.debug(f"No results-container found for CRN {crn}")
                continue

            charts_data = extract_charts_from_results(results_container)

            sections.append({
                "crn": crn,
                "subject": subject,
                "course_code": course_code,
                "title": title,
                "html": str(results_container),
                "charts_json": json.dumps(charts_data),
            })

        return sections
    except Exception as e:
        logger.error(f"Failed to parse instructor evaluation page: {e}")
        return []


def fetch_eval_terms(term_code: Optional[str] = None) -> List[str]:
    """Fetch available evaluation term codes from the public ESTHER TERMS endpoint.

    If term_code is provided, returns only that term.
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


def fetch_instructor_mapping(term: str) -> Dict[str, str]:
    """Fetch instructor NAME -> WEBID mapping for a term from the public ESTHER INSTRUCTORS endpoint.

    Returns a dict mapping instructor NAME (e.g. "Abreu, Vitor") to WEBID (e.g. "494").
    """
    try:
        res = r.get(INSTRUCTORS_URL.format(term=term), timeout=15)
        res.raise_for_status()
        root = ET.fromstring(res.text)
        mapping = {}
        for instr_el in root.findall("INSTRUCTOR"):
            name = instr_el.get("NAME", "")
            webid = instr_el.get("WEBID", "")
            if name and webid:
                mapping[name] = webid
        logger.info(f"Fetched {len(mapping)} instructors for term {term}")
        return mapping
    except r.RequestException as e:
        logger.error(f"HTTP error fetching instructors for term {term}: {e}")
        return {}
    except ET.ParseError as e:
        logger.error(f"XML parse error on INSTRUCTORS response for term {term}: {e}")
        return {}
    except Exception as e:
        logger.error(f"Unexpected error fetching instructors for term {term}: {e}")
        return {}


def get_instructors_to_scrape(db_path: str, term_code: Optional[str] = None, skip_existing: bool = True) -> tuple[List[Dict], set]:
    """Fetch instructors with available evaluations from the public ESTHER AJAX endpoints.

    Returns a list of task dicts with keys: term, instr_name, instr_id.
    Also returns the set of existing (term, crn, instructor_id) triples.
    """
    terms = fetch_eval_terms(term_code)
    if not terms:
        print("No terms found to scrape.")
        return [], set()

    # Load existing (term, instructor_id) pairs that have been fully scraped
    # We track at (term, instr_id) granularity for skip logic
    existing_triples = set()
    existing_term_instr = set()
    if skip_existing and os.path.exists(db_path):
        conn_evals = sql.connect(db_path)
        cur_evals = conn_evals.cursor()
        try:
            cur_evals.execute("SELECT term, crn, instructor_id FROM instructor_evaluations")
            existing_triples = set(cur_evals.fetchall())
            # Also build (term, instructor_id) set for skip logic
            existing_term_instr = set((t, i) for t, _, i in existing_triples)
        except sql.OperationalError:
            pass
        finally:
            conn_evals.close()

    tasks = []
    print(f"Fetching instructor lists for {len(terms)} term(s) from ESTHER...")
    for term in tqdm(terms, desc="Fetching instructors", unit="term"):
        mapping = fetch_instructor_mapping(term)
        for instr_name, instr_id in mapping.items():
            if skip_existing and (term, instr_id) in existing_term_instr:
                continue
            tasks.append({
                "term": term,
                "instr_name": instr_name,
                "instr_id": instr_id,
            })

    return tasks, existing_triples


def process_task(task: Dict[str, str], session: r.Session) -> Tuple[str, str, str, str, List[Dict]]:
    """Helper function to process a single instructor task in a worker thread."""
    term = task["term"]
    instr_id = task["instr_id"]
    instr_name = task["instr_name"]
    
    try:
        html_text, reason = scrape_instructor_eval(session, term, instr_id)
        if html_text:
            sections = parse_instructor_page(html_text)
            if sections:
                return ("SUCCESS", term, instr_id, instr_name, sections)
            else:
                return ("EMPTY", term, instr_id, instr_name, [])
        elif reason and "session expired" in reason.lower():
            return ("EXPIRED", term, instr_id, instr_name, [])
        else:
            return ("EMPTY", term, instr_id, instr_name, [])
    except Exception as e:
        logger.error(f"Unhandled error scraping instructor {instr_name} ({instr_id}), term {term}: {e}")
        return ("ERROR", term, instr_id, instr_name, [])


def main():
    parser = argparse.ArgumentParser(description="CourseTree ESTHER Instructor Evaluation Scraper")
    parser.add_argument("--term", type=str, help="Specific term code to scrape (e.g. 202510). Default scrapes all.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of instructors to scrape for testing.")
    parser.add_argument("--delay", type=float, default=0.0, help="Delay between requests in seconds (default: 0.0s).")
    parser.add_argument("--no-skip", action="store_true", help="Do not skip already scraped instructor evaluations.")
    parser.add_argument("--workers", type=int, default=8, help="Number of concurrent worker threads.")
    parser.add_argument("--netid", type=str, default=None, help="Rice NetID.")
    parser.add_argument("--password", type=str, default=None, help="Rice Password.")
    args = parser.parse_args()

    LOG_PATH = os.path.join(BASE_DIR, "scrape_instructor_evals.log")
    logging.basicConfig(
        filename=LOG_PATH,
        filemode='a',
        format='%(asctime)s - %(levelname)s - %(message)s',
        level=logging.DEBUG,
        force=True
    )
    # Also log to stderr for visibility
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))
    logging.getLogger().addHandler(console_handler)

    logger.info("=== Starting scrape_instructor_evals run ===")

    init_db(INSTRUCTOR_EVALS_DB_PATH)

    tasks, existing = get_instructors_to_scrape(INSTRUCTOR_EVALS_DB_PATH, term_code=args.term, skip_existing=not args.no_skip)
    if args.limit:
        tasks = tasks[:args.limit]

    print(f"Found {len(tasks)} instructor-term pairs to scrape evaluations for.")
    logger.info(f"Found {len(tasks)} instructor-term pairs to scrape (term={args.term}, skip_existing={not args.no_skip})")
    if not tasks:
        print("Nothing to scrape! All instructor evaluations already in database.")
        return

    # Authenticate
    try:
        session = authenticate_playwright(netid=args.netid, password=args.password)
    except Exception as e:
        logger.critical(f"Authentication failed: {e}")
        print(f"[!] Authentication failed: {e}")
        return

    # Connect to DB for inserting
    conn = sql.connect(INSTRUCTOR_EVALS_DB_PATH)
    cur = conn.cursor()

    success_count = 0
    section_count = 0
    empty_count = 0
    error_count = 0
    session_expired = False
    start_time = time.time()

    print(f"\nStarting sequential scrape of {len(tasks)} instructor-term pairs...")
    try:
        for task in tqdm(tasks, desc="Scraping Instructor Evals", unit="instructor"):
            if session_expired:
                logger.critical("ESTHER session expired mid-scrape. Stopping.")
                print("\n[!] ESTHER session expired. Stopping scrape. Progress saved.")
                break

            term = task["term"]
            instr_name = task["instr_name"]
            instr_id = task["instr_id"]

            if args.delay > 0:
                time.sleep(args.delay)

            try:
                html_text, reason = scrape_instructor_eval(session, term, instr_id)
            except Exception as e:
                error_count += 1
                logger.error(f"Unhandled error scraping instructor {instr_name} ({instr_id}), term {term}: {e}")
                continue

            if html_text:
                sections = parse_instructor_page(html_text)
                if sections:
                    try:
                        for sec in sections:
                            cur.execute("""
                                INSERT OR REPLACE INTO instructor_evaluations (term, crn, subject, course_code, title, instructor_id, instructor_name, html, charts_json)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                term, sec["crn"], sec["subject"], sec["course_code"], sec["title"],
                                instr_id, instr_name, sec["html"], sec["charts_json"]
                            ))
                        conn.commit()
                        success_count += 1
                        section_count += len(sections)
                    except sql.Error as e:
                        error_count += 1
                        logger.error(f"DB insert error for instructor {instr_name} ({instr_id}), term {term}: {e}")
                        continue
                    logger.info(f"  [+] Saved {len(sections)} section(s) for instructor: {instr_name} ({instr_id}) | Term: {term}")
                else:
                    empty_count += 1
                    logger.info(f"  [-] No parseable sections for instructor: {instr_name} ({instr_id}) | Term: {term}")
            else:
                if reason and "session expired" in reason.lower():
                    session_expired = True
                empty_count += 1
                logger.info(f"  [-] No instructor eval: {instr_name} ({instr_id}) | Term: {term} [{reason}]")

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
        f"  Instructors processed: {success_count + empty_count + error_count}\n"
        f"  Instructors with evaluations: {success_count}\n"
        f"  Total course sections saved: {section_count}\n"
        f"  Instructors without evaluations: {empty_count}\n"
        f"  Errors: {error_count}\n"
        f"  Saved to: {INSTRUCTOR_EVALS_DB_PATH}\n"
        f"  Log: {LOG_PATH}\n"
        f"======================================================="
    )
    print(summary)
    logger.info(summary)


if __name__ == "__main__":
    main()
