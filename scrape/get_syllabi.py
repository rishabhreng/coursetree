import os
import sys
import time
import argparse
import getpass
import threading
from pathlib import Path
from typing import Optional, Dict
import concurrent.futures

import requests as r
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from tqdm import tqdm

ESTHER_BASE_URL = "https://esther.rice.edu"
SYLLABUS_URL = "https://esther.rice.edu/selfserve/!bwzkpsyl.v_viewDoc?type=SYLLABUS"
DEFAULT_CSV_PATH = "../data/syllabus_list.csv"
DEFAULT_OUTPUT_DIR = "../data/syllabi"
MAX_WORKERS = 10
DEFAULT_DELAY = 0.25


def detect_file_extension(content: bytes, content_type: Optional[str] = None) -> str:
    if content.startswith(b"%PDF"):
        return "pdf"
    if content.startswith(b"PK\x03\x04"):
        return "docx"
    if content.startswith(b"\xd0\xcf\x11\xe0"):
        return "doc"
    if content.startswith(b"{\\rtf"):
        return "rtf"

    ct = (content_type or "").lower()
    if "pdf" in ct:
        return "pdf"
    if "wordprocessingml" in ct or "docx" in ct:
        return "docx"
    if "msword" in ct:
        return "doc"
    if "plain" in ct or "text" in ct:
        return "txt"
    return "pdf"


def is_valid_document(content: bytes, content_type: Optional[str] = None) -> bool:
    if not content or len(content) < 32:
        return False
    if content.startswith(b"%PDF") or content.startswith(b"PK\x03\x04") or content.startswith(b"\xd0\xcf\x11\xe0") or content.startswith(b"{\\rtf"):
        return True
    # Check for login redirect/HTML error
    if b"<html" in content.lower() or b"<!doctype" in content.lower() or b"public request for private file" in content.lower():
        return False
    ct = (content_type or "").lower()
    return "pdf" in ct or "word" in ct or "octet-stream" in ct or "plain" in ct


def authenticate_playwright(
    netid: Optional[str] = None,
    password: Optional[str] = None,
) -> r.Session:
    """Launches Playwright, performs full ESTHER SSO/Duo login, and establishes selfserve session."""
    from playwright.sync_api import sync_playwright

    if not netid or not password:
        print("\n--- ESTHER Headless Login ---")
        if not netid:
            netid = input("Enter Rice NetID: ").strip()
        if not password:
            password = getpass.getpass("Enter Rice Password: ").strip()

    session = r.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        print("Navigating to ESTHER login page...")
        page.goto(f"{ESTHER_BASE_URL}/")

        if netid and password:
            print("Submitting login credentials...")
            page.fill("#username", netid)
            page.fill("#password", password)
            page.keyboard.press("Enter")
            print("Credentials submitted! Please approve the Duo Push notification on your phone...")
        else:
            print("Please log in and approve Duo in the browser window...")

        print("Waiting for login & Duo approval...")
        authenticated = False
        start_wait = time.time()

        while time.time() - start_wait < 90:
            current_url = page.url
            cookies = context.cookies()
            cookie_names = {c["name"] for c in cookies if "rice.edu" in c.get("domain", "")}

            has_session_cookie = "SESSID" in cookie_names or "IDMSESSID" in cookie_names
            is_esther_page = "esther.rice.edu" in current_url and any(
                k in current_url for k in ["P_GenMnu", "P_MainMnu", "swkscmt", "twbkwbis", "selfserve"]
            )

            try:
                content = page.content()
                has_login_text = any(
                    k in content for k in ["Personal Information", "Student Services", "Course and Instructor Evaluation", "Main Menu"]
                )
            except Exception:
                has_login_text = False

            if (has_session_cookie and is_esther_page) or has_login_text:
                authenticated = True
                break

            time.sleep(1)

        if not authenticated:
            browser.close()
            raise TimeoutError("Authentication timed out waiting for Duo push or ESTHER login.")

        print("Navigating to Selfserve to establish full authenticated session...")
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

        browser.close()

    adapter = HTTPAdapter(pool_connections=MAX_WORKERS * 2, pool_maxsize=MAX_WORKERS * 2, max_retries=Retry(total=3, backoff_factor=0.2, raise_on_status=False))
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class AuthError(Exception):
    pass

def download_syllabus(term: str, crn: str, session: r.Session, output_dir: Path, no_skip: bool, delay: float) -> tuple:
    term_dir = output_dir / term
    term_dir.mkdir(parents=True, exist_ok=True)
    
    time.sleep(delay)  # Throttle to prevent ESTHER rate-limiting/WAF blocking

    if not no_skip:
        for ext in ["pdf", "docx", "doc", "txt", "rtf", "html"]:
            existing_file = term_dir / f"{crn}.{ext}"
            if existing_file.exists() and existing_file.stat().st_size > 512:
                return "skipped", str(existing_file)

    url = f"{SYLLABUS_URL}&term={term}&crn={crn}"
    for attempt in range(2):
        try:
            res = session.get(url, timeout=15)
            if res.status_code == 200:
                content = res.content
                ct = res.headers.get("content-type", "")
                
                if "id.rice.edu" in res.url or "login" in res.url.lower():
                    raise AuthError("Session redirected to login. Please authenticate again.")

                if is_valid_document(content, ct):
                    ext = detect_file_extension(content, ct)
                    target_fp = term_dir / f"{crn}.{ext}"
                    with open(target_fp, "wb") as f:
                        f.write(content)
                    return "downloaded", str(target_fp)
                
                if b"public request for private file" in content.lower():
                    return "auth_error", "Private/restricted syllabus"
                
                # Build a verbose error with content-type, size, magic bytes, and text preview
                magic = content[:8].hex() if content else "empty"
                details = f"ct={ct} len={len(content)} magic={magic}"
                
                is_html = b"<html" in content.lower() or b"<!doctype" in content.lower()
                if is_html:
                    try:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(content, "html.parser")
                        text = soup.get_text(separator=" ", strip=True)
                        preview = text[:120].replace(",", ";")
                    except Exception:
                        preview = content[:120].decode("utf-8", errors="replace").replace(",", ";")
                    return "empty", f"HTML response | {details} | {preview}"
                else:
                    preview = content[:60].decode("utf-8", errors="replace").replace(",", ";")
                    return "empty", f"Unknown format | {details} | {preview}"
            time.sleep(0.3)
        except AuthError:
            raise
        except Exception as e:
            if attempt == 1:
                return "error", str(e)
            time.sleep(0.5)

    return "empty", "No valid syllabus document returned"


def main():
    parser = argparse.ArgumentParser(description="Authenticated ESTHER Syllabus Downloader")
    parser.add_argument("--csv", type=str, default=DEFAULT_CSV_PATH, help="Path to syllabus_list.csv")
    parser.add_argument("--output-dir", type=str, default=DEFAULT_OUTPUT_DIR, help="Directory to save syllabi")
    parser.add_argument("--cookie-str", type=str, default=None, help="Raw cookie string if already available")
    parser.add_argument("--netid", type=str, default=None, help="Rice NetID")
    parser.add_argument("--password", type=str, help="ESTHER password (or prompt)")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="Delay between requests in seconds to prevent rate limiting (default: 0.25)")
    parser.add_argument("--threads", type=int, default=MAX_WORKERS, help="Concurrent worker threads")
    parser.add_argument("--no-skip", action="store_true", help="Redownload existing files")
    args = parser.parse_args()

    if not os.path.exists(args.csv):
        print(f"Error: CSV file '{args.csv}' not found.")
        sys.exit(1)

    with open(args.csv, "r") as f:
        lines = [line.strip().split(",") for line in f if line.strip()]

    items = [(parts[0], parts[1]) for parts in lines if len(parts) >= 2]
    print(f"Loaded {len(items)} courses from {args.csv}.")

    if args.cookie_str:
        session = r.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Cookie": args.cookie_str,
        })
        adapter = HTTPAdapter(pool_connections=args.threads * 2, pool_maxsize=args.threads * 2, max_retries=3)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    else:
        try:
            session = authenticate_playwright(netid=args.netid, password=args.password)
        except Exception as e:
            print(f"Authentication Failed: {e}")
            sys.exit(1)

    downloaded = 0
    skipped = 0
    failed = 0
    
    failed_log_path = "failed_syllabi.log"
    # Clear previous log
    with open(failed_log_path, "w") as f:
        f.write("term,crn,error\n")

    write_lock = threading.Lock()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.threads) as executor:
        future_to_item = {
            executor.submit(download_syllabus, term, crn, session, Path(args.output_dir), args.no_skip, args.delay): (term, crn)
            for term, crn in items
        }

        try:
            for future in tqdm(concurrent.futures.as_completed(future_to_item), total=len(items), desc="Downloading Syllabi", unit="file"):
                term, crn = future_to_item[future]
                try:
                    status, msg = future.result()
                    if status == "downloaded":
                        downloaded += 1
                    elif status == "skipped":
                        skipped += 1
                    else:
                        failed += 1
                        with write_lock:
                            with open(failed_log_path, "a") as f:
                                f.write(f"{term},{crn},{msg}\n")
                except AuthError as e:
                    print(f"\nCritical Error: {e}")
                    executor.shutdown(wait=False, cancel_futures=True)
                    sys.exit(1)
        except KeyboardInterrupt:
            print("\nDownload paused by user.")
            executor.shutdown(wait=False, cancel_futures=True)

    print(f"\nFinished! Downloaded: {downloaded} | Skipped: {skipped} | Failed/Empty: {failed}")
    if failed > 0:
        print(f"Failed syllabi details written to {failed_log_path}")


if __name__ == "__main__":
    main()
