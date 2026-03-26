import requests
from bs4 import BeautifulSoup
import os
import re
import urllib.parse
import time
import urllib3
import shutil
import concurrent.futures
import threading
import subprocess
import sys
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Suppress only the single InsecureRequestWarning from urllib3 needed
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Global lock for thread-safe printing
print_lock = threading.Lock()
# Global event to signal threads to stop (e.g., if login fails)
stop_event = threading.Event()

def safe_print(msg, end='\n'):
    """Thread-safe print function that supports unicode"""
    with print_lock:
        try:
            print(msg, end=end, flush=True)
        except UnicodeEncodeError:
            print(msg.encode('ascii', 'replace').decode('ascii'), end=end, flush=True)

def get_headers(cookie_string):
    """
    Constructs the headers dictionary with the user-provided cookie.
    User-Agent is spoofed to look like a standard browser.
    """
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Cookie': cookie_string.strip(),
        'Referer': 'https://portal.utar.edu.my/stuIntranet/examination/pastPaper/pastPaperSearch.jsp'
    }

def get_level_map():
    return {
        'F': ('F', 'Foundation Studies'),
        'B': ('B', 'Bachelor Degree'),
        'M': ('M', 'Master Degree')
    }

def check_ocrmypdf_installed():
    """Checks if ocrmypdf and its system dependencies (tesseract/ghostscript) are reachable."""
    try:
        # Check if Python module exists
        subprocess.run([sys.executable, '-m', 'ocrmypdf', '--version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        
        # Check if tesseract is installed in system PATH
        try:
            subprocess.run(['tesseract', '--version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        except Exception:
            return "MISSING_TESSERACT"
            
        return "OK"
    except Exception:
        return "MISSING_OCRMYPDF"

def fetch_links_for_subject(session, base_url, subject_url, subject_name, parent_folder):
    """
    Phase 1 Worker: Visits a subject page and collects all PDF download links.
    Returns: A list of task dictionaries [{'url':..., 'path':...}, ...]
    """
    if stop_event.is_set():
        return []

    tasks = []
    # Clean subject name for filesystem
    safe_name = re.sub(r'[\\/*?:"<>|]', "_", subject_name).strip()
    subject_folder = os.path.join(parent_folder, safe_name)

    try:
        # Short timeout for page loading (Discovery phase)
        course_res = session.get(subject_url, verify=False, timeout=20)
        course_res.raise_for_status()

        if "loginPage" in course_res.text:
            stop_event.set()
            return "LOGIN_ERROR"

    except requests.exceptions.RequestException:
        return []

    course_soup = BeautifulSoup(course_res.text, 'html.parser')

    # Extract hidden download links
    popup_links = course_soup.find_all('a', onclick=re.compile(r"mypopup"))

    if not popup_links:
        return []

    if not os.path.exists(subject_folder):
        try:
            os.makedirs(subject_folder, exist_ok=True)
        except OSError:
            pass 

    dl_idx = 0
    for link in popup_links:
        onclick_text = link['onclick']
        match = re.search(r"mypopup\('([^']*)'", onclick_text)

        if match:
            raw_dl_stub = match.group(1)
            parsed_stub = urllib.parse.urlparse(raw_dl_stub)
            qs = urllib.parse.parse_qs(parsed_stub.query)

            if 'fname' in qs:
                fname = qs['fname'][0]
                if not fname.lower().endswith('.pdf'):
                    fname += ".pdf"
            elif 'text' in qs:
                fname = qs['text'][0]
            else:
                fname = f"doc_{dl_idx}.pdf"

            fname = re.sub(r'[\\/*?:"<>|]', "", fname)
            file_path = os.path.join(subject_folder, fname)
            download_url = urllib.parse.urljoin(base_url, raw_dl_stub)

            tasks.append({
                'url': download_url,
                'path': file_path,
                'name': fname,
                'subject': safe_name
            })
            dl_idx += 1

    return tasks

def download_single_file(session, task):
    """
    Phase 2 Worker: Downloads a single file and processes it via ocrmypdf if requested.
    """
    if stop_event.is_set():
        return False

    is_ocr = task.get('is_ocr', False)
    
    if is_ocr:
        base, ext = os.path.splitext(task['path'])
        final_path = f"{base}_ocr{ext}"
        temp_raw_path = f"{base}_raw_temp{ext}"
    else:
        final_path = task['path']
        temp_raw_path = task['path']

    if os.path.exists(final_path):
        return True

    try:
        dl_response = session.get(task['url'], stream=True, verify=False, timeout=60)
        dl_response.raise_for_status()

        if "loginPage" in dl_response.text: 
            if 'text/html' in dl_response.headers.get('Content-Type', ''):
                stop_event.set()
                return "LOGIN_ERROR"

        with open(temp_raw_path, 'wb') as f:
            for chunk in dl_response.iter_content(chunk_size=8192):
                f.write(chunk)
                
        if is_ocr:
            try:
                ocr_process = subprocess.run(
                    [sys.executable, '-m', 'ocrmypdf', '--skip-text', temp_raw_path, final_path],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                if os.path.exists(temp_raw_path):
                    os.remove(temp_raw_path)
            except subprocess.CalledProcessError as e:
                # Extract actual OCR error logic from OCRmyPDF's STDERR
                error_msg = e.stderr.strip().split('\n')[-1] if e.stderr else "Unknown OCR Error"
                safe_print(f"\n    [OCR Error] {task['name']}: Exit Code {e.returncode} - {error_msg}")
                if os.path.exists(temp_raw_path):
                    failed_path = final_path.replace("_ocr.pdf", "_ocr_failed.pdf")
                    os.rename(temp_raw_path, failed_path)
            except Exception as e:
                safe_print(f"\n    [OCR Error] {task['name']}: {e}")
                if os.path.exists(temp_raw_path):
                    failed_path = final_path.replace("_ocr.pdf", "_ocr_failed.pdf")
                    os.rename(temp_raw_path, failed_path)

        return True
    except Exception:
        # Cleanup temporary file if download fails entirely
        if is_ocr and os.path.exists(temp_raw_path):
            try: os.remove(temp_raw_path)
            except: pass
        return False

def process_quick_search(session, base_url, query, root_download_folder, max_workers, is_ocr):
    """
    Direct downloading via Quick Search (reqQuery=1).
    Bypasses directory level categorization and finds 'hidden' subjects natively.
    """
    safe_print(f"\n[Quick Search Mode] Globally fast-searching for '{query}'...")
    tasks = []
    current_page = 1
    
    qs_folder = os.path.join(root_download_folder, f"QuickSearch_{query}")
    
    while True:
        params = {
            'reqCPage': str(current_page),
            'reqQuery': '1',
            'reqKey': query
        }
        
        try:
            response = session.post(f"{base_url}pastPaperSearch.jsp", data=params, verify=False, timeout=30)
            response.raise_for_status()
            if "loginPage" in response.text:
                safe_print("\n  [Error] Session Expired during page load.")
                return "LOGIN_ERROR"
        except requests.exceptions.RequestException as e:
            safe_print(f"\n  [Error] Could not access page {current_page}: {e}")
            return False

        soup = BeautifulSoup(response.text, 'html.parser')
        popup_links = soup.find_all('a', onclick=re.compile(r"mypopup"))
        
        if not popup_links:
            safe_print("") 
            break
            
        for link in popup_links:
            onclick_text = link.get('onclick', '')
            match = re.search(r"mypopup\('([^']*)'", onclick_text)
            if match:
                raw_dl_stub = match.group(1)
                parsed_stub = urllib.parse.urlparse(raw_dl_stub)
                qs = urllib.parse.parse_qs(parsed_stub.query)

                if 'fname' in qs:
                    fname = qs['fname'][0]
                    if not fname.lower().endswith('.pdf'):
                        fname += ".pdf"
                elif 'text' in qs:
                    fname = qs['text'][0]
                else:
                    fname = f"doc_{len(tasks)}.pdf"

                fname = re.sub(r'[\\/*?:"<>|]', "", fname)
                
                # Derive folder name from filename if possible (e.g. 'UEMX3613')
                subject_prefix = fname.split('_')[0] if '_' in fname else query
                safe_name = re.sub(r'[\\/*?:"<>|]', "_", subject_prefix).strip()
                subject_folder = os.path.join(qs_folder, safe_name)
                
                if not os.path.exists(subject_folder):
                    try: os.makedirs(subject_folder, exist_ok=True)
                    except: pass
                    
                file_path = os.path.join(subject_folder, fname)
                download_url = urllib.parse.urljoin(base_url, raw_dl_stub)

                tasks.append({
                    'url': download_url,
                    'path': file_path,
                    'name': fname,
                    'subject': safe_name,
                    'is_ocr': is_ocr
                })
        
        safe_print(f"\r  [Info] Discovered {len(tasks)} papers across {current_page} pages...", end='')
        current_page += 1

    if not tasks:
        safe_print(f"  [Info] No papers found using Quick Search for '{query}'.")
        return True

    safe_print(f"  [Phase 2] Downloading: Starting batch download with {max_workers} threads...")
    total_new_files = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(download_single_file, session, t) for t in tasks]
        completed_dl = 0
        total_dl = len(tasks)
        for future in concurrent.futures.as_completed(futures):
            if stop_event.is_set(): return "LOGIN_ERROR"
            res = future.result()
            completed_dl += 1
            safe_print(f"\r    Downloaded {completed_dl}/{total_dl} files...", end='')
            if res is True:
                total_new_files += 1

    safe_print(f"\n  [Done] Finished Quick Search batch. New files: {total_new_files}")
    return True

def process_level(session, base_url, level_code, level_desc, filter_query, root_download_folder, max_workers, is_ocr):
    """
    Standard Directory Level Crawling (reqQuery=2). 
    """
    level_search_url = f"{base_url}pastPaperSearch.jsp"
    
    safe_print(f"\nScanning Directory Level: [{level_desc}]...")
    
    subjects_to_scan = []
    current_page = 1
    
    while True:
        params = {
            'reqCPage': str(current_page),
            'reqQuery': '2',
            'reqLevel': level_code,
            'reqLevelDesc': level_desc
        }
        
        try:
            response = session.get(level_search_url, params=params, verify=False, timeout=30)
            response.raise_for_status()
            if "loginPage" in response.text or "loginPage" in response.url:
                safe_print("\n  [Error] Session Expired during page load.")
                return "LOGIN_ERROR"
        except requests.exceptions.RequestException as e:
            safe_print(f"\n  [Error] Could not access {level_desc} page {current_page}: {e}")
            return False

        soup = BeautifulSoup(response.text, 'html.parser')
        all_links = soup.find_all('a', href=True)

        found_any_on_page = False
        for link in all_links:
            if 'reqUnit' in link['href']:
                found_any_on_page = True
                link_text = link.get_text().strip()
                if not filter_query or (filter_query in link_text.upper()):
                    full_url = urllib.parse.urljoin(base_url, link['href'])
                    subjects_to_scan.append((full_url, link_text))
                    
        if not found_any_on_page:
            safe_print("") 
            break 
            
        safe_print(f"\r  [Info] Discovered {len(subjects_to_scan)} directory subjects across {current_page} pages...", end='')
        current_page += 1

    if not subjects_to_scan:
        safe_print(f"  [Info] No subjects matched in directory '{filter_query}'.")
        return True

    safe_print(f"  [Phase 1] Discovery: Crawling inside {len(subjects_to_scan)} subjects for PDFs...")

    level_folder = os.path.join(root_download_folder, level_desc)
    all_download_tasks = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {
            executor.submit(fetch_links_for_subject, session, base_url, url, name, level_folder): name
            for url, name in subjects_to_scan
        }

        completed = 0
        total = len(subjects_to_scan)

        for future in concurrent.futures.as_completed(future_to_url):
            if stop_event.is_set(): return "LOGIN_ERROR"

            result = future.result()
            completed += 1
            safe_print(f"\r    Scanned {completed}/{total} subjects...", end='')

            if result == "LOGIN_ERROR":
                return "LOGIN_ERROR"

            if result:
                for r in result:
                    r['is_ocr'] = is_ocr
                all_download_tasks.extend(result)

    safe_print(f"\n  [Phase 1 Complete] Found {len(all_download_tasks)} PDFs inside {level_desc}.")

    if not all_download_tasks:
        return True

    safe_print(f"  [Phase 2] Downloading: Batch downloading with {max_workers} threads...")

    total_new_files = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(download_single_file, session, task) for task in all_download_tasks]

        completed_dl = 0
        total_dl = len(all_download_tasks)

        for future in concurrent.futures.as_completed(futures):
            if stop_event.is_set(): return "LOGIN_ERROR"

            res = future.result()
            completed_dl += 1
            safe_print(f"\r    Downloaded {completed_dl}/{total_dl} files...", end='')

            if res == "LOGIN_ERROR":
                return "LOGIN_ERROR"
            if res is True:
                total_new_files += 1

    safe_print(f"\n  [Done] Finished {level_desc}. New files: {total_new_files}")
    return True

def scrape_untar_past_papers():
    print("=== UTAR Past Year Exam Paper Downloader (Adaptive Engine + OCR) ===")
    
    # Load environment variables silently
    load_dotenv()
    
    env_cookie = os.environ.get("JSESSIONID")
    
    if env_cookie:
        print("[+] Found JSESSIONID inside .env file. Automatically authenticating!")
        cookie_input = env_cookie
    else:
        print("NOTE: Login to portal in browser -> F12 -> Network -> Copy 'Cookie'")
        cookie_input = input("\nEnter Cookie (e.g. JSESSIONID=...): ").strip()
        
    if not cookie_input:
        print("Cookie is required!")
        return

    if "=" not in cookie_input and len(cookie_input) > 10:
        print("  [Auto-fix] Adding 'JSESSIONID=' prefix...")
        cookie_str = f"JSESSIONID={cookie_input}"
    else:
        cookie_str = cookie_input

    # Request desired download formatting (RAW vs OCR)
    print("\nDownload Format:")
    print("  1 = RAW (Standard original PDF)")
    print("  2 = OCR (Make text searchable using OCRmyPDF)")
    format_choice = input("Choice (1/2) [1]: ").strip()
    is_ocr = (format_choice == '2')

    if is_ocr:
        print("Checking for OCR dependencies...")
        ocr_status = check_ocrmypdf_installed()
        if ocr_status == "MISSING_OCRMYPDF":
            print("\n[!] ERROR: 'ocrmypdf' is not installed in Python environment!")
            print("    Run: pip install ocrmypdf")
            print("    Falling back to standard RAW mode.\n")
            is_ocr = False
        elif ocr_status == "MISSING_TESSERACT":
            print("\n[!] ERROR: Python 'ocrmypdf' is installed, but system 'Tesseract-OCR' is missing from PATH!")
            print("    Windows Users: Install Tesseract natively, OR use Google Colab / WSL.")
            print("    Linux/Colab: apt-get install tesseract-ocr ghostscript qpdf")
            print("    Falling back to standard RAW mode to prevent errors.\n")
            is_ocr = False
        else:
            print("[+] OCRmyPDF and system dependencies detected! OCR format enabled.")

    course_code_query = ""
    level_choice = ""
    
    print("\nTarget Subject:")
    course_code_query = input("Enter Course Code (Leave EMPTY to download ALL subjects): ").strip().upper()

    if not course_code_query:
        print("\n[WARNING] You left the Course Code EMPTY.")
        print("This will crawl the ENTIRE system for ALL papers by level.")
        confirm = input("Are you sure? (y/n): ").lower()
        if confirm != 'y':
            print("Aborted.")
            return

        print("\nSelect Level:")
        print("  F = Foundation Studies")
        print("  B = Bachelor Degree")
        print("  M = Master Degree")
        print("  A = ALL LEVELS")
        level_choice = input("Choice (F/B/M/A): ").strip().upper()

    # Dynamic thread options based on mode
    try:
        if is_ocr:
            max_workers_input = input("\nEnter number of OCR threads (Default 4, Max recommended 8): ").strip()
            max_workers = int(max_workers_input) if max_workers_input else 4
        else:
            max_workers_input = input("\nEnter number of download threads (Default 50, Max 100): ").strip()
            max_workers = int(max_workers_input) if max_workers_input else 50
    except ValueError:
        max_workers = 4 if is_ocr else 50
        
    print("\nDownload Directory:")
    root_input = input("Enter download path (Leave EMPTY for current folder): ").strip()
    if not root_input:
        root_folder = os.path.dirname(os.path.abspath(__file__))
    else:
        root_folder = root_input

    if not os.path.exists(root_folder):
        try:
            os.makedirs(root_folder)
        except OSError as e:
            print(f"Error creating directory: {e}")
            return

    session = requests.Session()
    retry_strategy = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"]
    )
    adapter = HTTPAdapter(
        pool_connections=max_workers+50,
        pool_maxsize=max_workers+50,
        max_retries=retry_strategy
    )

    session.mount('https://', adapter)
    session.headers.update(get_headers(cookie_str))

    base_url = "https://portal.utar.edu.my/stuIntranet/examination/pastPaper/"

    stop_event.clear()

    # Adaptive Logic Engine
    if course_code_query:
        print(f"\n[!] Fast-Track: Specific subject '{course_code_query}' requested.")
        print("[!] Using Native Quick Search mode instead of Directory Navigation...")
        
        result = process_quick_search(session, base_url, course_code_query, root_folder, max_workers, is_ocr)
        
        if result == "LOGIN_ERROR":
            print("\n[!] CRITICAL ERROR: Session Invalid/Expired.")
            print("    Please re-login to UTAR portal and get a fresh Cookie.")
    else:
        level_map = get_level_map()
        levels_to_check = []
        if level_choice == 'A':
            levels_to_check = [level_map['F'], level_map['B'], level_map['M']]
        elif level_choice in level_map:
            levels_to_check = [level_map[level_choice]]
        else:
            print("Invalid level choice. Defaulting to Bachelor (B).")
            levels_to_check = [level_map['B']]

        for lvl_code, lvl_desc in levels_to_check:
            if stop_event.is_set():
                break

            result = process_level(session, base_url, lvl_code, lvl_desc, course_code_query, root_folder, max_workers, is_ocr)

            if result == "LOGIN_ERROR":
                print("\n[!] CRITICAL ERROR: Session Invalid/Expired.")
                print("    Please re-login to UTAR portal and get a fresh Cookie.")
                break

    print("\n=== Processing Complete ===")
    print(f"Files are safely stored in: {os.path.abspath(root_folder)}")

if __name__ == "__main__":
    scrape_untar_past_papers()
