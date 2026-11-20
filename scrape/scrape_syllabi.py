import argparse
import concurrent.futures
from pathlib import Path
import sqlite3 as sql
import threading

import requests as r
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util import Retry

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "main.db"
CSV_OUTPUT_PATH = REPO_ROOT / "data" / "syllabus_list.csv"
# Courses already checked and found to have no syllabus. Syllabi for past
# terms essentially never appear later, so only recent terms get rechecked.
NO_SYLLABUS_PATH = REPO_ROOT / "data" / "syllabus_none.csv"
RECHECK_RECENT_TERMS = 4
METADATA_COURSES_URL = "https://courses.rice.edu/courses/!SWKSCAT.info?action=SYLLABUS"
MAX_WORKERS = 20

session = r.Session()
adapter = HTTPAdapter(
    pool_connections=MAX_WORKERS * 2,
    pool_maxsize=MAX_WORKERS * 2,
    max_retries=Retry(total=3, backoff_factor=0.2, raise_on_status=False),
)
session.mount("https://", adapter)
session.mount("http://", adapter)


def check_course(term: str, crn: str):
    """True/False if the course has a syllabus; None if the check failed."""
    url = f"{METADATA_COURSES_URL}&term={term}&crn={crn}"
    try:
        res = session.get(url, timeout=10)
        if res.status_code != 200:
            return None
        return 'has-syllabus="yes"' in res.text
    except Exception:
        return None


def _read_rows(path: Path) -> set:
    if not path.exists():
        return set()
    with open(path, "r") as f:
        return {line.strip() for line in f if line.strip()}


def main():
    parser = argparse.ArgumentParser(description="Find which courses have syllabi")
    parser.add_argument("--recheck-all", action="store_true", help="Recheck every course without a known syllabus, not just recent terms.")
    args = parser.parse_args()

    cur = sql.connect(str(DB_PATH)).cursor()
    courses = cur.execute("SELECT term, crn FROM global_search").fetchall()
    unique_courses = {
        f"{term.replace('courses_', '')},{crn}" for term, crn in courses
    }

    already_existing = _read_rows(CSV_OUTPUT_PATH)
    known_none = set() if args.recheck_all else _read_rows(NO_SYLLABUS_PATH)

    all_terms = sorted({row.split(",")[0] for row in unique_courses})
    recent_terms = set(all_terms[-RECHECK_RECENT_TERMS:])
    skip_none = {row for row in known_none if row.split(",")[0] not in recent_terms}

    to_check = list(unique_courses - already_existing - skip_none)

    print(f"Total unique courses in DB: {len(unique_courses)}")
    print(f"Courses already with syllabi: {len(already_existing)}")
    print(f"Courses known to have no syllabus (skipped): {len(skip_none)}")
    print(f"Courses left to check: {len(to_check)}")

    if not to_check:
        print("Nothing to check")
        return

    count = 0
    write_lock = threading.Lock()

    none_now = set()
    with open(CSV_OUTPUT_PATH, "a") as f:
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_course = {
                executor.submit(check_course, row.split(",")[0], row.split(",")[1]): row
                for row in to_check
            }

            try:
                for future in tqdm(
                    concurrent.futures.as_completed(future_to_course),
                    total=len(to_check),
                ):
                    row = future_to_course[future]
                    result = future.result()
                    if result:
                        with write_lock:
                            f.write(f"{row}\n")
                            f.flush()
                            count += 1
                    elif result is False:
                        none_now.add(row)
            except KeyboardInterrupt:
                executor.shutdown(wait=False, cancel_futures=True)

    # Rewrite the no-syllabus list: keep old entries not rechecked, add new
    # ones, and drop any course that has since gained a syllabus.
    checked = set(to_check)
    none_rows = ((known_none | _read_rows(NO_SYLLABUS_PATH)) - checked) | none_now
    none_rows -= _read_rows(CSV_OUTPUT_PATH)
    tmp = NO_SYLLABUS_PATH.with_suffix(".csv.tmp")
    with open(tmp, "w") as nf:
        nf.writelines(f"{row}\n" for row in sorted(none_rows))
    tmp.replace(NO_SYLLABUS_PATH)

    print(f"\nAdded {count} new courses to {CSV_OUTPUT_PATH}")
    print(f"{len(none_rows)} courses recorded as having no syllabus in {NO_SYLLABUS_PATH}")


if __name__ == "__main__":
    main()
