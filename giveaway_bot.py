"""
StoryGraph Giveaway Bot
=======================
Each time it runs, this script logs in to StoryGraph, reads your to-read shelf, looks at every
open giveaway, and enters the ones that pass ALL of these checks:
  1. The book (title AND author) is on your to-read shelf. Which edition you added doesn't matter.
  2. The prize is a PRINT copy (not digital or audio).
  3. Your country is on the prize's eligible-countries list.
Every time it enters a giveaway, it sends a push notification to your phone through ntfy.

SETTINGS (put these in a file named .env in this folder, one per line, like SG_EMAIL=you@example.com):
  SG_EMAIL, SG_PASSWORD    your StoryGraph login
  NTFY_TOPIC               your private ntfy topic name
  MY_COUNTRY               for example "United States" (this is the default)
  --- optional ---
  SG_USERNAME              your StoryGraph username (found automatically if you leave it blank)
  NTFY_SERVER, NTFY_TOKEN  only needed for a self-hosted or reserved ntfy topic
  NTFY_SUMMARY=0           turn off the weekly "run finished" message
  SELECTORS_URL            link to an online selectors.json, so layout fixes can be picked up automatically

HOW TO RUN (from the folder that holds this file; on Windows use "py" instead of "python3"):
  Dry run (nothing is entered):    python3 giveaway_bot.py --headed --once
  Real run, watch the browser:     python3 giveaway_bot.py --live --headed --once
  Real run, hidden browser:        python3 giveaway_bot.py --live --once
  Test the phone notification:     python3 giveaway_bot.py --test-notify
The first time, use --headed so you can sign in and click any "I'm not a robot" box yourself.
Your login is then saved in the "browser_profile" folder and reused on later runs.

FILES IT CREATES:
  giveaway_bot.log   a record of everything it did, and why it skipped each giveaway
  state.json         the giveaways already entered (so it never enters the same one twice)
  debug/             screenshots saved when something goes wrong
"""
import argparse, csv, difflib, json, logging, os, random, re, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# Always work inside the folder that holds this script, no matter where it was started from.
# (This matters when a scheduler starts the bot, because schedulers often start it somewhere else.)
SCRIPT_FOLDER = Path(__file__).resolve().parent
os.chdir(SCRIPT_FOLDER)

def load_settings_file():
    """Read your settings from a file named .env in this folder (if it exists).
    Each line looks like NAME=value. Lines starting with # are ignored.
    A setting that is already set on your computer is left alone."""
    env_file = SCRIPT_FOLDER / ".env"
    if not env_file.exists(): return
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value

load_settings_file()

# =====================================================================
# SETTINGS
# =====================================================================
VERSION = "1.2.1"
SG_EMAIL = os.environ.get("SG_EMAIL")
SG_PASSWORD = os.environ.get("SG_PASSWORD")
SG_USERNAME = os.environ.get("SG_USERNAME")                  # optional
MY_COUNTRY = os.environ.get("MY_COUNTRY") or "United States"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
NTFY_TOKEN = os.environ.get("NTFY_TOKEN")                    # optional
NTFY_SERVER = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
SEND_SUMMARY = (os.environ.get("NTFY_SUMMARY") or "1") != "0"   # weekly "I ran" message
SELECTORS_URL = os.environ.get("SELECTORS_URL")              # optional

TITLE_MATCH_STRICTNESS = 0.92    # 1.0 = titles must match exactly; lower = more forgiving
AUTHOR_MATCH_STRICTNESS = 0.88   # same idea for author names
MAX_GIVEAWAY_PAGES = 10          # how many pages of the giveaway list to read

# Files the bot reads and writes (all in the same folder as this script)
BROWSER_PROFILE_DIR = "browser_profile"     # keeps you logged in between runs
STATE_FILE = "state.json"
LOG_FILE = "giveaway_bot.log"
EXPORT_CSV = "storygraph_export.csv"        # optional backup copy of your to-read list
SELECTORS_FILE = "selectors.json"
SELECTORS_CACHE = "selectors_cache.json"
UPDATE_NOTICE_FILE = "update_notified.txt"

# Write messages both to the screen and to the log file
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
log = logging.info

# =====================================================================
# PAGE ELEMENT FINDERS ("selectors")
# These tell the bot how to find things on StoryGraph's pages (buttons, titles, and so on).
# If StoryGraph changes its layout and something stops working, fix the matching line here
# or in selectors.json. You don't need to touch the rest of the code.
# =====================================================================
DEFAULT_SEL = {
 "sg_profile_link": "a[href^='/profile/'], a[href*='thestorygraph.com/profile/']",
 "sg_tbr_pane": "div.book-title-author-and-series",
 "sg_tbr_title": "h3 a",
 "sg_author": "a[href*='/authors/']",
 "sg_giveaway_link": "a[href*='/giveaways/']",
 "sg_title": "h2 a[href*='/books/'], h1 a[href*='/books/']",
 "sg_enter": "a:has-text('Enter giveaway'), button:has-text('Enter giveaway')",
 "sg_confirm_and_enter": "button:has-text('Confirm and Enter'), a:has-text('Confirm and Enter'), [role=button]:has-text('Confirm and Enter'), input[value*='Confirm and Enter']",
 "sg_email_field": "#user_email",
 "sg_password_field": "#user_password",
 "sg_login_submit": "input[type=submit], button[type=submit]",
 "sg_cookie_accept": "button:has-text('Accept and close'), button:has-text('Accept'), #onetrust-accept-btn-handler"
}
SEL = dict(DEFAULT_SEL)   # the finders actually in use (can be updated from selectors.json)

# Counts and problems collected during a run, used for the notification at the end
STATS = {"problems": [], "entered": 0, "inspected": 0, "unreadable": 0}

def problem(msg):
    """Record something that went wrong, so it can be included in the alert at the end."""
    log(f"PROBLEM: {msg}")
    STATS["problems"].append(msg)

def version_tuple(v):
    """'1.2.0' -> (1, 2, 0), so version numbers can be compared."""
    try: return tuple(int(x) for x in str(v).split("."))
    except Exception: return (0,)

def load_selectors():
    """Set up the page element finders.
    Order: built-in list -> selectors.json in this folder -> selectors.json online (if SELECTORS_URL is set).
    Later ones win. The file is only ever read as plain text, never run as code."""
    SEL.clear(); SEL.update(DEFAULT_SEL)

    def apply(data):
        for key, value in (data.get("selectors") or {}).items():
            if key in DEFAULT_SEL and isinstance(value, str) and 0 < len(value) < 500:
                SEL[key] = value

    try: apply(json.load(open(SELECTORS_FILE)))
    except Exception: pass
    if not SELECTORS_URL: return

    data = None
    try:
        raw = urllib.request.urlopen(SELECTORS_URL, timeout=15).read()
        data = json.loads(raw)
        open(SELECTORS_CACHE, "wb").write(raw)          # keep a copy in case the website is down next time
        log("selectors: downloaded the latest version")
    except Exception as e:
        log(f"selectors: couldn't download ({e}); using the saved copy instead")
        try: data = json.load(open(SELECTORS_CACHE))
        except Exception: pass
    if not data: return
    apply(data)

    # If a newer version of the bot is announced, tell the user once.
    latest = data.get("latest_version")
    already_told = Path(UPDATE_NOTICE_FILE).read_text().strip() if Path(UPDATE_NOTICE_FILE).exists() else ""
    if latest and version_tuple(latest) > version_tuple(VERSION) and already_told != str(latest):
        push("Giveaway bot update available",
             f"Version {latest} is out (you have {VERSION}). {data.get('update_note', '')}\n{data.get('update_url', '')}",
             tags=["arrow_up"], click=data.get("update_url"))
        Path(UPDATE_NOTICE_FILE).write_text(str(latest))

# =====================================================================
# MATCHING BOOKS (title AND author must both match)
# =====================================================================
def clean_title(title):
    """Make a title easy to compare: lowercase, drop (series info), drop the subtitle after a colon,
    drop punctuation, and drop a leading The/A/An."""
    t = re.sub(r"\(.*?\)", " ", title.lower()).split(":")[0]
    t = re.sub(r"[^\w\s]", "", t).strip()
    return re.sub(r"\s+", " ", re.sub(r"^(the|a|an)\s+", "", t))

def clean_author(name):
    """Make an author name easy to compare: lowercase, no Jr./Sr., no periods ('J.K.' becomes 'jk')."""
    name = re.sub(r"\b(jr|sr|ii|iii)\b\.?", "", name.strip().lower())
    name = name.replace(".", "")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'-]", "", name)).strip()

def same_author(a, b):
    """True if two cleaned author names are the same person (close spelling, or same last name + first initial)."""
    if not a or not b: return False
    if a == b or difflib.SequenceMatcher(None, a, b).ratio() >= AUTHOR_MATCH_STRICTNESS: return True
    first_a, first_b = a.split(), b.split()
    return first_a[-1] == first_b[-1] and first_a[0][0] == first_b[0][0]

class ToReadShelf:
    """Your to-read list. Each book is stored as (cleaned title, [cleaned author names])."""
    def __init__(self):
        self.books = []

    def add(self, title, authors):
        if title:
            self.books.append((clean_title(title), [clean_author(a) for a in authors if a.strip()]))

    def has(self, title, authors):
        """True if a book with a matching title AND author is on the shelf."""
        wanted_title = clean_title(title)
        wanted_authors = [clean_author(a) for a in authors]
        for shelf_title, shelf_authors in self.books:
            title_matches = (shelf_title == wanted_title or
                             difflib.SequenceMatcher(None, shelf_title, wanted_title).ratio() >= TITLE_MATCH_STRICTNESS)
            if title_matches and any(same_author(x, y) for x in wanted_authors for y in shelf_authors):
                return True
        return False

    def similar_titles(self, title):
        """Shelf books with a similar title. Used only to explain in the log why a match failed."""
        wanted = clean_title(title)
        return [(t, a) for t, a in self.books
                if t == wanted or difflib.SequenceMatcher(None, t, wanted).ratio() >= 0.8]

def load_export_csv(shelf):
    """Optional backup: also add the to-read books from your StoryGraph export file, if it's in this folder."""
    if not Path(EXPORT_CSV).exists(): return
    with open(EXPORT_CSV, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if (row.get("Read Status") or "").strip().lower() == "to-read":
                shelf.add(row.get("Title", ""), (row.get("Authors") or "").split(","))

# =====================================================================
# MEMORY OF WHAT'S ALREADY ENTERED
# =====================================================================
def load_entered():
    try: return set(json.load(open(STATE_FILE))["entered"])
    except Exception: return set()

def save_entered(entered):
    json.dump({"entered": sorted(entered)}, open(STATE_FILE, "w"), indent=1)

# =====================================================================
# PHONE NOTIFICATIONS (ntfy)
# =====================================================================
def push(title, message, tags=None, click=None, priority=None):
    """Send a notification through ntfy. If sending fails, it's logged but never stops the run."""
    if not NTFY_TOPIC:
        log("  (no notification sent: NTFY_TOPIC isn't set)"); return
    payload = {"topic": NTFY_TOPIC, "title": title[:100], "message": message, "tags": tags or []}
    if click: payload["click"] = click          # tapping the notification opens this link
    if priority: payload["priority"] = priority
    headers = {"Content-Type": "application/json"}
    if NTFY_TOKEN: headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
    try:
        request = urllib.request.Request(NTFY_SERVER, json.dumps(payload).encode("utf-8"), headers)
        urllib.request.urlopen(request, timeout=15).read()
        log("  notification sent")
    except Exception as e:
        log(f"  notification failed: {e}")

def notify_entered(title, authors, url, uncertain=False):
    """Tell you about an entry. 'uncertain' means we clicked through but couldn't confirm it worked."""
    by = ", ".join(dict.fromkeys(a.strip() for a in authors if a.strip()))
    if uncertain:
        intro, heading, tag = "Clicked through the entry steps but couldn't confirm it worked. Please check.\n", "Check manually: ", "warning"
    else:
        intro, heading, tag = "You've been entered in a giveaway!\n", "Entered: ", "books"
    push(heading + title[:80], intro + f"Book: {title}\nAuthor: {by}\nFormat: Print\nSite: StoryGraph",
         click=url, tags=[tag])

# =====================================================================
# SMALL BROWSER HELPERS
# =====================================================================
def pause(low=2.5, high=5.5):
    """Wait a random few seconds, so the bot moves at a human pace."""
    time.sleep(random.uniform(low, high))

def goto(page, url):
    """Open a page and wait until it's ready. Also copes with pages that redirect right away."""
    try:
        page.goto(url, wait_until="domcontentloaded")
    except Exception as e:
        if "ERR_ABORTED" in str(e):
            log(f"  (the page redirected; continuing at {page.url})")
        else:
            raise
    try: page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception: pass
    time.sleep(1)   # let any extra redirect finish

def safe_query(page, selector):
    """Find one element. If the page changes mid-search, wait a moment and try once more."""
    for _ in range(2):
        try:
            return page.query_selector(selector)
        except Exception as e:
            if "context was destroyed" in str(e).lower() or "execution context" in str(e).lower():
                log("  (page changed while searching; trying again)"); time.sleep(1); continue
            raise
    return None

def visible_elements(page, selector):
    """All elements matching the selector that are actually showing on screen."""
    found = []
    for element in page.query_selector_all(selector):
        try:
            if element.is_visible(): found.append(element)
        except Exception:
            pass
    return found

def click(element):
    """Click something. If another element is blocking it, click it directly instead of getting stuck."""
    try: element.scroll_into_view_if_needed(timeout=5000)
    except Exception: pass
    try:
        element.click(timeout=8000)
    except PWTimeout:
        log("  normal click was blocked; clicking it directly instead")
        element.evaluate("e => e.click()")

def snapshot(page, name):
    """Save a screenshot and the page's HTML into the debug folder, to help figure out what went wrong."""
    try:
        Path("debug").mkdir(exist_ok=True)
        page.screenshot(path=f"debug/{name}.png", full_page=True)
        Path(f"debug/{name}.html").write_text(page.content(), encoding="utf-8")
        log(f"  saved debug/{name}.png and debug/{name}.html")
    except Exception as e:
        log(f"  couldn't save a screenshot: {e}")

_saved_once = set()
def snapshot_once(page, name):
    """Same as snapshot(), but only the first time per run (so the debug folder doesn't fill up)."""
    if name not in _saved_once:
        _saved_once.add(name); snapshot(page, name)

def dismiss_cookies(page):
    """Close StoryGraph's cookie banner if it's showing. It can cover buttons and block clicks."""
    for find in (lambda: safe_query(page, SEL["sg_cookie_accept"]),
                 lambda: page.query_selector("text=Accept and close")):
        try:
            button = find()
            if button and button.is_visible():
                button.click(timeout=3000); time.sleep(0.5); return
        except Exception:
            pass

# =====================================================================
# LOGGING IN
# =====================================================================
def login(page, headed=False):
    """Make sure we're signed in to StoryGraph. Returns True if we are."""
    goto(page, "https://app.thestorygraph.com/users/sign_in")
    if "sign_in" not in page.url:
        return True                                  # the saved login from a previous run still works
    if not (SG_EMAIL and SG_PASSWORD):
        log("StoryGraph: not signed in, and SG_EMAIL / SG_PASSWORD aren't set"); return False
    dismiss_cookies(page)

    # If you're watching (--headed), wait up to 10 minutes so you can click any "I'm not a robot" box.
    # If nobody is watching, give up quickly, because nobody could click it anyway.
    wait_for_form_ms = 600_000 if headed else 30_000
    wait_after_submit_checks = 200 if headed else 10   # each check is 3 seconds apart
    try:
        if headed:
            log("  waiting up to 10 minutes for the sign-in form (click any verification box if you see one)...")
        page.wait_for_selector(SEL["sg_email_field"], state="visible", timeout=wait_for_form_ms)
    except PWTimeout:
        snapshot(page, "login_form_not_found")
        log("StoryGraph: the sign-in form never appeared. See debug/login_form_not_found.png")
        return False

    page.fill(SEL["sg_email_field"], SG_EMAIL)
    page.fill(SEL["sg_password_field"], SG_PASSWORD)
    page.click(SEL["sg_login_submit"])
    try: page.wait_for_load_state("domcontentloaded")
    except PWTimeout: pass

    # StoryGraph may show a second verification step after you submit. Give it time to be completed.
    if headed:
        log("  if another verification step appears, you have up to 10 more minutes...")
    for _ in range(wait_after_submit_checks):
        if "sign_in" not in page.url: break
        time.sleep(3)
    signed_in = "sign_in" not in page.url
    if not signed_in:
        snapshot(page, "login_failed")
        log("StoryGraph login failed (wrong password, or a verification step). See debug/login_failed.png")
    return signed_in

# =====================================================================
# READING YOUR TO-READ SHELF
# =====================================================================
def read_to_read_shelf(page, shelf):
    """Read every book on your StoryGraph to-read shelf into 'shelf'. Returns how many books were found."""
    username = SG_USERNAME
    if not username:
        goto(page, "https://app.thestorygraph.com/")
        link = page.query_selector(SEL["sg_profile_link"])
        username = link.get_attribute("href").rstrip("/").split("/")[-1] if link else None
    if not username:
        log("StoryGraph: couldn't find your username (you can set SG_USERNAME)"); return 0

    seen = set()
    count_before = len(shelf.books)
    for page_number in range(1, 60):
        goto(page, f"https://app.thestorygraph.com/to-read/{username}?page={page_number}")
        new_books = 0
        for pane in page.query_selector_all(SEL["sg_tbr_pane"]):
            title_link = pane.query_selector(SEL["sg_tbr_title"])
            if not title_link: continue
            title = title_link.inner_text().strip()
            authors = [a.inner_text() for a in pane.query_selector_all(SEL["sg_author"])]
            if (title, tuple(authors)) in seen: continue
            seen.add((title, tuple(authors)))
            shelf.add(title, authors)
            new_books += 1
        if not new_books: break            # no new books on this page, so we've reached the end
        pause(1.5, 3)
    found = len(shelf.books) - count_before
    log(f"StoryGraph to-read scraped: {found} books")
    return found

# =====================================================================
# FINDING GIVEAWAYS
# =====================================================================
def find_giveaways(page):
    """Collect the link to every open giveaway (from the home page and the giveaways list)."""
    links = set()
    link_pattern = re.compile(r"/giveaways/[0-9a-f]{8}-[0-9a-f-]{27}")

    def collect_links():
        for a in page.query_selector_all(SEL["sg_giveaway_link"]):
            match = link_pattern.search(a.get_attribute("href") or "")
            if match: links.add("https://app.thestorygraph.com" + match.group(0))

    goto(page, "https://app.thestorygraph.com/")
    for _ in range(4):                                  # scroll down; giveaways are at the bottom of the home page
        page.mouse.wheel(0, 4000); time.sleep(1)
    collect_links()

    for page_number in range(1, MAX_GIVEAWAY_PAGES + 1):
        count_before = len(links)
        goto(page, f"https://app.thestorygraph.com/giveaways?page={page_number}")
        for _ in range(3):
            page.mouse.wheel(0, 4000); time.sleep(0.8)
        collect_links()
        if len(links) == count_before: break            # nothing new, so that was the last page
        pause(1.5, 3)
    log(f"StoryGraph: {len(links)} giveaways found")
    return sorted(links)

# =====================================================================
# READING A GIVEAWAY PAGE
# =====================================================================
# A giveaway can have several prizes (for example one print and one digital). Each prize is a block
# of text that includes "Format: Print" or "Format: Digital". This snippet starts at a button and
# moves outward until it finds the block of text for the prize that button belongs to.
PRIZE_TEXT_JS = """el => { let n = el; while (n.parentElement) { n = n.parentElement;
    if ((n.textContent || '').includes('Format:')) return n.textContent; } return ''; }"""

def prize_format(prize_text):
    """Return the prize's format as one lowercase word, for example 'print' or 'digital'."""
    match = re.search(r"Format:\s*(.*)", prize_text, re.S)
    rest = (match.group(1) if match else "").strip().lower()
    for name in ("print", "digital", "audio", "ebook", "e-book"):
        if rest.startswith(name): return name
    return (re.match(r"[a-z]+", rest) or [""])[0]

def country_is_eligible(prize_text):
    """True if your country is listed under the prize's eligible countries."""
    match = re.search(r"Eligible countries(.*)", prize_text, re.S | re.I)
    countries_text = match.group(1) if match else ""
    # The checks on either side stop 'Niger' from matching 'Nigeria', or 'Guinea' from matching 'Guinea-Bissau'.
    return bool(re.search(rf"(?<![A-Za-z-]){re.escape(MY_COUNTRY)}(?![a-z-])", countries_text))

def authors_from_heading(title_link, title):
    """On a giveaway page the heading reads 'Book Title by Author One, Author Two'.
    The author isn't a link, so read it from the heading text that comes after the title."""
    try:
        heading = title_link.evaluate("e => ((e.closest('h1,h2,h3') || e.parentElement).innerText || '')")
    except Exception:
        return []
    heading = re.sub(r"\s+", " ", heading).strip()
    rest = heading.replace(title, "", 1).strip() if title in heading else heading
    rest = re.sub(r"^by\s+", "", rest, flags=re.I).strip()
    if not rest or rest == heading: return []
    return [name.strip() for name in re.split(r",|&|\band\b", rest) if name.strip()]

def print_enter_buttons(page):
    """The 'Enter giveaway' buttons on screen that belong to a PRINT prize."""
    buttons = []
    for button in visible_elements(page, SEL["sg_enter"]):
        try: prize_text = button.evaluate(PRIZE_TEXT_JS)
        except Exception: continue
        if prize_text.count("Format:") == 1 and prize_format(prize_text) == "print":
            buttons.append(button)
    return buttons

# =====================================================================
# ENTERING A GIVEAWAY
# =====================================================================
def click_through_entry(page, first_button):
    """StoryGraph's entry steps are:
         1. click 'Enter giveaway'                      (opens a new screen)
         2. click 'Enter giveaway' again under Prizes   (opens a section with a confirm button)
         3. click 'Confirm and Enter'
    This looks at what's on screen after every click and does the next step, so it still works
    if the steps are slightly different from what's described above."""
    dismiss_cookies(page)
    click(first_button)
    time.sleep(3)
    for _ in range(4):
        dismiss_cookies(page)
        confirm_buttons = visible_elements(page, SEL["sg_confirm_and_enter"])
        if confirm_buttons:                              # step 3
            click(confirm_buttons[0]); time.sleep(3)
            return
        enter_buttons = print_enter_buttons(page)        # step 2
        if not enter_buttons:
            raise RuntimeError("couldn't find the Enter / Confirm and Enter button for the print prize")
        click(enter_buttons[0])
        try: page.wait_for_selector(SEL["sg_confirm_and_enter"], state="visible", timeout=8000)
        except PWTimeout: pass
    raise RuntimeError("the 'Confirm and Enter' button never appeared")

def check_giveaway(page, url, shelf, live, entered):
    """Look at one giveaway. Enter it if it passes every check (or just report it, on a dry run).
    Returns a one-line result that gets written to the log."""
    goto(page, url)

    # 1. Read the book's title and author
    title_link = page.query_selector(SEL["sg_title"])
    title = re.sub(r"\s+", " ", title_link.inner_text()).strip() if title_link else ""
    authors = authors_from_heading(title_link, title) if title_link else []
    authors += [a.inner_text() for a in page.query_selector_all(SEL["sg_author"])]   # in case authors are links
    if not title or not authors:
        snapshot_once(page, "giveaway_page_unreadable")
        return f"skip (couldn't read title/author): {url}"

    # 2. Is it on your to-read shelf?
    if not shelf.has(title, authors):
        similar = shelf.similar_titles(title)
        hint = f"  [similar title on your shelf but the author differs: {similar}]" if similar else ""
        return f"skip (not on TBR): {title} / {authors}{hint}"

    # 3. Is there an Enter button? (There isn't if it's closed or you've already entered.)
    enter_buttons = page.query_selector_all(SEL["sg_enter"])
    if not enter_buttons:
        return f"skip (no enter button: closed/entered/ineligible): {title}"

    # 4. Find the print prize that's open to your country
    for button in enter_buttons:
        prize_text = button.evaluate(PRIZE_TEXT_JS)
        if prize_text.count("Format:") != 1:
            log(f"  couldn't tell which prize a button belongs to on {url}"); continue
        fmt = prize_format(prize_text)
        memory_key = f"sg:{url}:{fmt}"
        if fmt != "print":
            log(f"  skip prize (format={fmt}): {title}"); continue
        if memory_key in entered:
            return f"skip (already entered): {title}"
        if not country_is_eligible(prize_text):
            log(f"  skip prize (not open to {MY_COUNTRY}): {title}"); continue
        if not live:
            return f"WOULD ENTER (StoryGraph, print): {title} {url}"

        # 5. Enter it for real
        try:
            click_through_entry(page, button)
        except Exception as e:
            snapshot(page, "enter_failed")
            return f"FAILED ({type(e).__name__}: {str(e)[:120]}): {title} {url} -> see debug/enter_failed.png"

        # 6. Double-check: once you've entered, StoryGraph stops showing an Enter button for that prize
        goto(page, url); dismiss_cookies(page)
        if not print_enter_buttons(page):
            entered.add(memory_key); save_entered(entered)
            notify_entered(title, authors, url)
            return f"ENTERED (StoryGraph): {title} {url}"
        snapshot(page, "enter_uncertain")
        notify_entered(title, authors, url, uncertain=True)
        return f"UNCERTAIN (clicked through, but an Enter button is still showing; please check): {title} {url}"

    return f"skip (no eligible print prize): {title}"

# =====================================================================
# ONE FULL RUN
# =====================================================================
def run_once(live, headed):
    entered = load_entered()
    shelf = ToReadShelf()
    STATS.update(problems=[], entered=0, inspected=0, unreadable=0)
    load_selectors()

    with sync_playwright() as pw:
        # A real browser window (visible if --headed). Its saved login lives in the profile folder.
        context = pw.chromium.launch_persistent_context(BROWSER_PROFILE_DIR, headless=not headed,
                                                        viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.set_default_timeout(20000)
        try:
            if not login(page, headed=headed):
                problem("StoryGraph login failed (wrong password, or a verification step)")
            else:
                # Read your shelf
                try: books_found = read_to_read_shelf(page, shelf)
                except Exception as e: log(f"couldn't read your to-read shelf: {e}"); books_found = 0
                if books_found == 0:
                    problem("Found 0 books on your to-read shelf. It may be empty, or StoryGraph's layout changed.")
                load_export_csv(shelf)
                log(f"Total to-read entries: {len(shelf.books)}")

                # Find the giveaways
                giveaway_links = find_giveaways(page)
                if not giveaway_links:
                    problem("Found 0 giveaways. StoryGraph's layout may have changed.")
                if not shelf.books:
                    problem("No to-read books loaded, so nothing was entered this run.")
                    giveaway_links = []

                # Check each giveaway (in random order, to look less robotic)
                random.shuffle(giveaway_links)
                for url in giveaway_links:
                    STATS["inspected"] += 1
                    try:
                        result = check_giveaway(page, url, shelf, live, entered)
                        log(result)
                        if result.startswith("skip (couldn't read"): STATS["unreadable"] += 1
                        if result.startswith(("ENTERED", "WOULD ENTER")): STATS["entered"] += 1
                    except PWTimeout:
                        log(f"timed out: {url}")
                        snapshot_once(page, "timed_out")
                    except Exception as e:
                        log(f"error on {url}: {e}")
                    pause()

                # If most giveaway pages couldn't be read, the layout has probably changed
                if STATS["inspected"] >= 5 and STATS["unreadable"] / STATS["inspected"] >= 0.5:
                    problem(f"Couldn't read the title/author on {STATS['unreadable']} of {STATS['inspected']} "
                            "giveaway pages. The page element finders likely need updating.")
        except Exception:
            snapshot(page, "run_crashed")      # a picture of what the browser was showing when it broke
            raise
        finally:
            context.close()

    # Final notifications
    if STATS["problems"]:
        push("Giveaway bot needs attention", "\n".join("- " + p for p in STATS["problems"]),
             tags=["rotating_light"], priority=4)
    if SEND_SUMMARY:
        word = "entered" if live else "would enter"
        push("Weekly giveaway run finished",
             f"Checked {STATS['inspected']} giveaways; {word} {STATS['entered']}." + ("" if live else " (dry run)"),
             tags=["books"], priority=2)

def main():
    parser = argparse.ArgumentParser(description="StoryGraph giveaway bot")
    parser.add_argument("--live", action="store_true", help="really enter giveaways (without this it's a dry run)")
    parser.add_argument("--headed", action="store_true", help="show the browser window (use this the first time)")
    parser.add_argument("--once", action="store_true", help="run one time and stop (this is the default)")
    parser.add_argument("--daemon", action="store_true", help="keep running, repeating on a schedule")
    parser.add_argument("--every-hours", type=float, default=168, help="with --daemon: hours between runs (default 168 = weekly)")
    parser.add_argument("--test-notify", action="store_true", help="send a sample phone notification and stop")
    args = parser.parse_args()

    if args.test_notify:
        notify_entered("Radicals: Echoes of the First Woman to Run for President", ["Jen Knox"],
                       "https://app.thestorygraph.com/giveaways/example")
        return

    while True:
        try:
            run_once(args.live, args.headed)
        except Exception as e:
            log(f"run failed: {e}")
            push("Giveaway bot crashed", str(e)[:300], tags=["rotating_light"], priority=4)
        if not args.daemon: break
        wait_seconds = args.every_hours * 3600 * random.uniform(0.9, 1.1)
        log(f"sleeping {wait_seconds / 3600:.1f} hours")
        time.sleep(wait_seconds)

if __name__ == "__main__":
    main()
