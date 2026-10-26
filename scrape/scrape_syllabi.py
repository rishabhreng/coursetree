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


def check_course(term: str, crn: str) -> bool:
    url = f"{METADATA_COURSES_URL}&term={term}&crn={crn}"
    try:
        res = session.get(url, timeout=10)
        return res.status_code == 200 and 'has-syllabus="yes"' in res.text
    except Exception:
        return False


def main():
    cur = sql.connect(str(DB_PATH)).cursor()
    courses = cur.execute("SELECT term, crn FROM global_search").fetchall()
    unique_courses = {
        f"{term.replace('courses_', '')},{crn}" for term, crn in courses
    }

    already_existing = set()
    if CSV_OUTPUT_PATH.exists():
        with open(CSV_OUTPUT_PATH, "r") as f:
            already_existing = {line.strip() for line in f if line.strip()}

    to_check = list(unique_courses - already_existing)

    print(f"Total unique courses in DB: {len(unique_courses)}")
    print(f"Courses already with syllabi: {len(already_existing)}")
    print(f"Courses left to check: {len(to_check)}")

    if not to_check:
        print("Nothing to check")
        return

    count = 0
    write_lock = threading.Lock()

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
                    if future.result():
                        with write_lock:
                            f.write(f"{row}\n")
                            f.flush()
                            count += 1
            except KeyboardInterrupt:
                executor.shutdown(wait=False, cancel_futures=True)

    print(f"\nAdded {count} new courses to {CSV_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
