import sqlite3
import time
import requests
from bs4 import BeautifulSoup
import re
from playwright.sync_api import sync_playwright

DB_PATH = "courses.db"

def get_term_tables(cursor):
    """Finds all tables that look like 'courses_202610'."""
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'courses_%'")
    # Extract the table names and ignore the global_search table if it doesn't match the pattern
    return [row[0] for row in cursor.fetchall()]

def ensure_eval_column(cursor, table_name):
    """Ensures the eval_score column exists in the given table."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [col[1] for col in cursor.fetchall()]
    
    if "eval_score" not in columns:
        print(f"Adding 'eval_score' column to {table_name}...")
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN eval_score TEXT")

def get_human_cookies():
    """Use Playwright to let the user log in, then steal the session cookies."""
    with sync_playwright() as p:
        print("Launching browser... Please log in and pass Duo!")
        browser = p.chromium.launch(headless=False) 
        context = browser.new_context()
        page = context.new_page()

        page.goto("https://esther.rice.edu/")

        # Wait indefinitely for the user to log in and the main menu to appear
        page.wait_for_selector("text='Personal Information'", timeout=0)
        print("✅ Logged in successfully! Snatching cookies...\n")
        
        playwright_cookies = context.cookies()
        browser.close()
        
        return {cookie['name']: cookie['value'] for cookie in playwright_cookies}

def do_scrape():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    term_tables = get_term_tables(cursor)

    # 1. Figure out exactly how much work we have to do across ALL tables
    all_courses_to_scrape = []

    for table in term_tables:
        ensure_eval_column(cursor, table)
        conn.commit()
        
        # The term code is the part after 'courses_'
        term_code = table.replace("courses_", "")
        
        cursor.execute(f"SELECT crn, crs FROM {table} WHERE eval_score IS NULL")
        rows = cursor.fetchall()
        
        for crn, crs in rows:
            # crs is usually like "BIOS 211", so we split by space to get "BIOS"
            subject = crs.split()[0] if crs else ""
            all_courses_to_scrape.append({
                "table": table,
                "term": term_code,
                "crn": crn,
                "subject": subject,
                "crs": crs
            })

    if not all_courses_to_scrape:
        print("🎉 All term tables are fully scraped! Nothing to do.")
        conn.close()
        return

    print(f"Found {len(all_courses_to_scrape)} courses missing evaluation data across {len(term_tables)} terms.")

    # 2. Authenticate
    cookies = get_human_cookies()

    # 3. Setup Requests
    session = requests.Session()
    requests.utils.add_dict_to_cookiejar(session.cookies, cookies)
    url = "https://esther.rice.edu/selfserve/swkscmt.main"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    print("🚀 Starting the scraping engine. Grab a coffee...\n")
    success_count = 0
    fail_count = 0

    # 4. Scrape Loop
    for course in all_courses_to_scrape:
        payload = {
            "p_commentid": "",
            "p_confirm": "1",
            "p_term": course["term"], 
            "p_type": "Course",
            "p_subj": course["subject"],
            "p_crn": course["crn"]
        }

        response = session.post(url, headers=headers, data=payload)

        # Session timeout check
        if "bmenu.P_MainMnu" not in response.text and "Personal Information" not in response.text:
            print("\n❌ Session expired or blocked. Stopping script.")
            break

        soup = BeautifulSoup(response.text, 'html.parser')
        mean_divs = soup.find_all('div', class_='third', string=re.compile(r'Class Mean:'))
        
        if not mean_divs:
            score_to_save = "N/A"
        else:
            try:
                # Grab the 3rd chart's mean (usually "Overall Quality")
                if len(mean_divs) >= 3:
                    score_to_save = mean_divs[2].text.replace("Class Mean: ", "").strip()
                else:
                    score_to_save = mean_divs[0].text.replace("Class Mean: ", "").strip()
            except Exception:
                score_to_save = "Error"

        # 5. Save back to the SPECIFIC term table this course belongs to
        cursor.execute(
            f"UPDATE {course['table']} SET eval_score = ? WHERE crn = ?", 
            (score_to_save, course["crn"])
        )
        conn.commit()

        if score_to_save not in ["N/A", "Error"]:
            print(f"✅ {course['term']} | {course['crs']} (CRN {course['crn']}): {score_to_save}")
            success_count += 1
        else:
            print(f"⚠️ {course['term']} | {course['crs']} (CRN {course['crn']}): No data")
            fail_count += 1

        time.sleep(0.5)

    print(f"\n🏁 Finished! Successfully scraped {success_count} scores. ({fail_count} had no data).")
    
    # Optional: If your global_search table is a real table and not just a VIEW, 
    # you might want to rebuild it here after the script finishes updating the base tables!

    conn.close()
do_scrape()