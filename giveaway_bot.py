"""
Goodreads + StoryGraph giveaway auto-enterer (runs unattended).

Enters a giveaway only if ALL are true:
  1. you're eligible (prize's eligible-country list includes yours / an Enter button is offered)
  2. the prize is PRINT (not digital/audio)
  3. the book matches something on your to-read shelf by TITLE *and* AUTHOR
     (edition doesn't matter: a digital copy on your shelf still matches a print giveaway)

SETUP
  pip install playwright && playwright install chromium

  Credentials (environment variables, so they're not hard-coded):
    GR_EMAIL, GR_PASSWORD     Goodreads (Amazon login)
    SG_EMAIL, SG_PASSWORD     StoryGraph
    MY_COUNTRY                e.g. "United States" (default)

  Push notifications via ntfy (sent each time a giveaway is entered):
    NTFY_TOPIC                a long, random, private topic name (it acts like a password;
                              anyone who knows it can read your notifications)
    NTFY_SERVER (optional)    default https://ntfy.sh
    NTFY_TOKEN  (optional)    access token, if you self-host or reserved your topic
  Install the ntfy app on your phone and subscribe to that same topic.

  Optional (auto-updates): SELECTORS_URL = raw URL of a selectors.json in your GitHub repo.
    Each run the bot fetches it, so if a site changes its layout you fix selectors.json once and
    every install picks it up. It also alerts you (via ntfy) when it detects breakage or a new version.
    PLATFORMS = "goodreads,storygraph" (default) or just "storygraph".

  FIRST RUN (once, with a visible browser, so you can solve any captcha / 2FA):
      python giveaway_bot.py --headed --live --once
  The login session is saved in ./browser_profile and reused headlessly afterward.

  UNATTENDED:
      python giveaway_bot.py --live --daemon      (runs weekly; change with --every-hours)
  (or schedule `python giveaway_bot.py --live --once` with cron / Task Scheduler)

  Without --live it's a dry run: it logs what it WOULD enter.

OPTIONAL: goodreads_library_export.csv / storygraph_export.csv in this folder are
merged into your to-read list as a fallback if scraping your shelves comes up short.

Output: giveaway_bot.log (everything) and state.json (giveaways already entered).
The CSS selectors are best guesses; check the log on the first dry run.
"""
import argparse, csv, difflib, json, logging, os, random, re, sys, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ---------------- CONFIG ----------------
VERSION = "1.1.0"
MY_COUNTRY = os.environ.get("MY_COUNTRY") or "United States"
PLATFORMS = (os.environ.get("PLATFORMS") or "goodreads,storygraph").lower()
GR_EMAIL, GR_PASSWORD = os.environ.get("GR_EMAIL"), os.environ.get("GR_PASSWORD")
SG_EMAIL, SG_PASSWORD = os.environ.get("SG_EMAIL"), os.environ.get("SG_PASSWORD")
SG_USERNAME = os.environ.get("SG_USERNAME")   # optional; auto-detected if blank
GOODREADS_CSV, STORYGRAPH_CSV = "goodreads_library_export.csv", "storygraph_export.csv"
MAX_PAGES = 10
TITLE_FUZZY = 0.92
AUTHOR_FUZZY = 0.88
NTFY_SERVER = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
SUMMARY = (os.environ.get("NTFY_SUMMARY") or "1") != "0"   # weekly "I ran" heartbeat
NTFY_TOPIC, NTFY_TOKEN = os.environ.get("giveaways-k7x2qm9vh4bt8wnz"), os.environ.get("NTFY_TOKEN")
PROFILE_DIR, STATE_FILE, LOG_FILE = "browser_profile", "state.json", "giveaway_bot.log"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
log = logging.info

# ---------------- SELECTORS (updatable without touching the code) ----------------
DEFAULT_SEL = {
 "sg_profile_link": "a[href^='/profile/'], a[href*='thestorygraph.com/profile/']",
 "sg_tbr_pane": "div.book-title-author-and-series",
 "sg_tbr_title": "h3 a",
 "sg_author": "a[href*='/authors/']",
 "sg_giveaway_link": "a[href*='/giveaways/']",
 "sg_title": "h2 a[href*='/books/'], h1 a[href*='/books/']",
 "sg_enter": "a:has-text('Enter giveaway'), button:has-text('Enter giveaway')",
 "sg_confirm": "button:has-text('Confirm'), button:has-text('Enter'), input[type=submit][value*='nter']",
 "sg_email_field": "#user_email",
 "sg_password_field": "#user_password",
 "sg_login_submit": "input[type=submit], button[type=submit]",
 "gr_signed_in": "a[href*='sign_out']",
 "gr_email_signin": "text=Sign in with email",
 "gr_amazon_email": "#ap_email",
 "gr_amazon_password": "#ap_password",
 "gr_amazon_submit": "#signInSubmit",
 "gr_tbr_row": "tr.bookalike",
 "gr_tbr_title": "td.field.title a",
 "gr_tbr_author": "td.field.author a",
 "gr_giveaway_link": "a[href*='/giveaway/show/']",
 "gr_title": "h1",
 "gr_author": "a[href*='/author/show/']",
 "gr_enter": "a:has-text('Enter Giveaway'), button:has-text('Enter Giveaway'), input[value*='Enter Giveaway']",
 "gr_terms": "input[type=checkbox][name*='agree'], input[type=checkbox][id*='agree'], input[type=checkbox][id*='terms']",
 "gr_submit": "input[type=submit][value*='Enter'], button:has-text('Enter to win'), button:has-text('Enter Giveaway')"
}
SEL = dict(DEFAULT_SEL)
SELECTORS_URL = os.environ.get("SELECTORS_URL")   # raw URL of selectors.json in your repo
SELECTORS_FILE, SELECTORS_CACHE, NOTIFIED_FILE = "selectors.json", "selectors_cache.json", "update_notified.txt"
STATS = {"problems": [], "entered": 0, "inspected": 0, "unreadable": 0}

def vtuple(v):
    try: return tuple(int(x) for x in str(v).split("."))
    except Exception: return (0,)

def load_selectors():
    """defaults <- local selectors.json <- remote selectors.json (cached). DATA ONLY: strings are
    used as Playwright selectors, never executed as code."""
    SEL.clear(); SEL.update(DEFAULT_SEL)
    def merge(d):
        for k, v in (d.get("selectors") or {}).items():
            if k in DEFAULT_SEL and isinstance(v, str) and 0 < len(v) < 500: SEL[k] = v
    try: merge(json.load(open(SELECTORS_FILE)))
    except Exception: pass
    if not SELECTORS_URL: return
    data = None
    try:
        raw = urllib.request.urlopen(SELECTORS_URL, timeout=15).read()
        data = json.loads(raw); open(SELECTORS_CACHE, "wb").write(raw)
        log("selectors: fetched latest from SELECTORS_URL")
    except Exception as e:
        log(f"selectors: remote fetch failed ({e}); trying cached copy")
        try: data = json.load(open(SELECTORS_CACHE))
        except Exception: pass
    if data:
        merge(data)
        latest = data.get("latest_version")
        if latest and vtuple(latest) > vtuple(VERSION) and (
                not Path(NOTIFIED_FILE).exists() or Path(NOTIFIED_FILE).read_text().strip() != str(latest)):
            push("Giveaway bot update available",
                 f"Version {latest} is out (you have {VERSION}). {data.get('update_note', '')}\n{data.get('update_url', '')}",
                 tags=["arrow_up"], click=data.get("update_url"))
            Path(NOTIFIED_FILE).write_text(str(latest))

def problem(msg):
    log(f"PROBLEM: {msg}"); STATS["problems"].append(msg)

# ---------------- MATCHING ----------------
def norm_title(t):
    t = re.sub(r"\(.*?\)", " ", t.lower()).split(":")[0]
    t = re.sub(r"[^\w\s]", "", t).strip()
    return re.sub(r"\s+", " ", re.sub(r"^(the|a|an)\s+", "", t))

def norm_author(a):
    a = a.strip()
    if "," in a:                                   # Goodreads list view: "Knox, Jen"
        last, first = a.split(",", 1); a = f"{first} {last}"
    a = re.sub(r"\b(jr|sr|ii|iii)\b\.?", "", a.lower())
    a = re.sub(r"[.]", "", a)                      # "J.K." -> "jk"
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'-]", "", a)).strip()

def author_eq(a, b):
    if not a or not b: return False
    if a == b or difflib.SequenceMatcher(None, a, b).ratio() >= AUTHOR_FUZZY: return True
    pa, pb = a.split(), b.split()
    return pa[-1] == pb[-1] and pa[0][0] == pb[0][0]   # same surname + first initial

class Tbr:
    def __init__(self): self.items = []            # (title_norm, [author_norm,...])
    def add(self, title, authors):
        if title:
            self.items.append((norm_title(title), [norm_author(a) for a in authors if a.strip()]))
    def matches(self, title, authors):
        t = norm_title(title); auths = [norm_author(a) for a in authors]
        for tt, tauths in self.items:
            if tt == t or difflib.SequenceMatcher(None, tt, t).ratio() >= TITLE_FUZZY:
                if any(author_eq(x, y) for x in auths for y in tauths):
                    return True
        return False

def load_csvs(tbr):
    for path, status_col, auth_cols in [(GOODREADS_CSV, "Exclusive Shelf", ["Author", "Additional Authors"]),
                                        (STORYGRAPH_CSV, "Read Status", ["Authors"])]:
        if not Path(path).exists(): continue
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if (row.get(status_col) or "").strip().lower() != "to-read": continue
                authors = [a for c in auth_cols for a in (row.get(c) or "").split(",")]
                tbr.add(row.get("Title", ""), authors)

# ---------------- STATE ----------------
def load_state():
    try: return set(json.load(open(STATE_FILE))["entered"])
    except Exception: return set()
def save_state(entered): json.dump({"entered": sorted(entered)}, open(STATE_FILE, "w"), indent=1)

def push(title, message, tags=None, click=None, priority=None):
    """Send an ntfy notification. Never raises: a failure must not stop the run."""
    if not NTFY_TOPIC:
        log("  (notifications not configured; set NTFY_TOPIC)"); return
    payload = {"topic": NTFY_TOPIC, "title": title[:100], "message": message, "tags": tags or []}
    if click: payload["click"] = click
    if priority: payload["priority"] = priority
    headers = {"Content-Type": "application/json"}
    if NTFY_TOKEN: headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
    try:   # JSON publishing supports non-ASCII book titles, unlike header-based publishing
        urllib.request.urlopen(urllib.request.Request(NTFY_SERVER, json.dumps(payload).encode("utf-8"), headers),
                               timeout=15).read()
        log("  notification sent")
    except Exception as e:
        log(f"  notification failed: {e}")

def notify(site, title, authors, fmt, url, uncertain=False):
    by = ", ".join(dict.fromkeys(a.strip() for a in authors if a.strip()))
    body = ("Clicked enter but couldn't confirm it went through. Please check.\n" if uncertain
            else "You've been entered in a giveaway!\n") + f"Book: {title}\nAuthor: {by}\nFormat: {fmt}\nSite: {site}"
    push(("Check manually: " if uncertain else "Entered: ") + title[:80], body, click=url,
         tags=["warning" if uncertain else "books"])

def pause(a=2.5, b=5.5): time.sleep(random.uniform(a, b))

def blocked(page):
    txt = page.content().lower()
    return any(k in txt for k in ["captcha", "otp", "verification code", "solve this puzzle"])

# ---------------- STORYGRAPH ----------------
def sg_login(page):
    page.goto("https://app.thestorygraph.com/users/sign_in"); page.wait_for_load_state("domcontentloaded")
    if "sign_in" not in page.url: return True                  # already signed in
    if not (SG_EMAIL and SG_PASSWORD): log("StoryGraph: not logged in and no SG_EMAIL/SG_PASSWORD"); return False
    page.fill(SEL["sg_email_field"], SG_EMAIL); page.fill(SEL["sg_password_field"], SG_PASSWORD)
    page.click(SEL["sg_login_submit"]); page.wait_for_load_state("domcontentloaded"); pause()
    ok = "sign_in" not in page.url
    if not ok: log("StoryGraph login failed (bad creds or captcha). Run once with --headed.")
    return ok

def sg_scrape_tbr(page, tbr):
    user = SG_USERNAME
    if not user:
        page.goto("https://app.thestorygraph.com/")
        a = page.query_selector(SEL["sg_profile_link"])
        user = a.get_attribute("href").rstrip("/").split("/")[-1] if a else None
    if not user: log("StoryGraph: couldn't detect username (set SG_USERNAME)"); return
    seen, n0 = set(), len(tbr.items)
    for n in range(1, 60):
        page.goto(f"https://app.thestorygraph.com/to-read/{user}?page={n}")
        panes = page.query_selector_all(SEL["sg_tbr_pane"])
        new = 0
        for p in panes:
            t = p.query_selector(SEL["sg_tbr_title"])
            if not t: continue
            title = t.inner_text().strip()
            authors = [x.inner_text() for x in p.query_selector_all(SEL["sg_author"])]
            if (title, tuple(authors)) in seen: continue
            seen.add((title, tuple(authors))); tbr.add(title, authors); new += 1
        if not new: break
        pause(1.5, 3)
    log(f"StoryGraph to-read scraped: {len(tbr.items) - n0} books")
    return len(tbr.items) - n0

def sg_collect(page):
    urls = set(); pat = re.compile(r"/giveaways/[0-9a-f]{8}-[0-9a-f-]{27}")
    def grab():
        for a in page.query_selector_all(SEL["sg_giveaway_link"]):
            m = pat.search(a.get_attribute("href") or "")
            if m: urls.add("https://app.thestorygraph.com" + m.group(0))
    page.goto("https://app.thestorygraph.com/"); page.wait_for_load_state("domcontentloaded")
    for _ in range(4): page.mouse.wheel(0, 4000); time.sleep(1)     # home page giveaways sit at the bottom
    grab()
    for n in range(1, MAX_PAGES + 1):
        before = len(urls)
        page.goto(f"https://app.thestorygraph.com/giveaways?page={n}")
        for _ in range(3): page.mouse.wheel(0, 4000); time.sleep(0.8)
        grab()
        if len(urls) == before: break
        pause(1.5, 3)
    log(f"StoryGraph: {len(urls)} giveaways found")
    return sorted(urls)

PRIZE_BLOCK_JS = """el => { let n = el; while (n.parentElement) { n = n.parentElement;
    if ((n.textContent || '').includes('Format:')) return n.textContent; } return ''; }"""

def sg_country_ok(block):
    m = re.search(r"Eligible countries(.*)", block, re.S | re.I)
    seg = m.group(1) if m else ""
    return bool(re.search(rf"(?<![A-Za-z-]){re.escape(MY_COUNTRY)}(?![A-Za-z-])", seg))

def sg_process(page, url, tbr, live, entered):
    page.goto(url); page.wait_for_load_state("domcontentloaded")
    t = page.query_selector(SEL["sg_title"])
    title = t.inner_text().strip() if t else ""
    authors = [a.inner_text() for a in page.query_selector_all(SEL["sg_author"])]
    if not title or not authors: return f"skip (couldn't read title/author): {url}"
    if not tbr.matches(title, authors): return f"skip (not on TBR): {title} / {authors}"

    btns = page.query_selector_all(SEL["sg_enter"])
    if not btns: return f"skip (no enter button: closed/entered/ineligible): {title}"
    for i, btn in enumerate(btns):
        block = btn.evaluate(PRIZE_BLOCK_JS)
        if block.count("Format:") != 1: log(f"  ambiguous prize layout on {url}"); continue
        fmt = (re.search(r"Format:\s*([A-Za-z]+)", block) or [None, ""])[1].lower()
        key = f"sg:{url}:{fmt}"
        if fmt != "print":      log(f"  skip prize (format={fmt}): {title}"); continue
        if key in entered:      return f"skip (already entered): {title}"
        if not sg_country_ok(block): log(f"  skip prize (not eligible in {MY_COUNTRY}): {title}"); continue
        if not live: return f"WOULD ENTER (StoryGraph, print): {title} {url}"
        btn.click(); page.wait_for_load_state("domcontentloaded"); time.sleep(2)
        confirm = page.query_selector(SEL["sg_confirm"])
        if confirm: confirm.click(); time.sleep(2)
        body = page.inner_text("body").lower()
        if "entered" in body or "good luck" in body or "you're in" in body:
            entered.add(key); save_state(entered)
            notify("StoryGraph", title, authors, "Print", url)
            return f"ENTERED (StoryGraph): {title} {url}"
        notify("StoryGraph", title, authors, "Print", url, uncertain=True)
        return f"UNCERTAIN (clicked enter, no confirmation text seen; check manually): {title} {url}"
    return f"skip (no eligible print prize): {title}"

# ---------------- GOODREADS ----------------
def gr_logged_in(page):
    page.goto("https://www.goodreads.com/"); page.wait_for_load_state("domcontentloaded")
    return page.query_selector(SEL["gr_signed_in"]) is not None

def gr_login(page):
    if gr_logged_in(page): return True
    if not (GR_EMAIL and GR_PASSWORD): log("Goodreads: not logged in and no GR_EMAIL/GR_PASSWORD"); return False
    page.goto("https://www.goodreads.com/user/sign_in"); page.wait_for_load_state("domcontentloaded")
    e = page.query_selector(SEL["gr_email_signin"])
    if e: e.click(); page.wait_for_load_state("domcontentloaded")
    try:
        page.fill(SEL["gr_amazon_email"], GR_EMAIL); page.fill(SEL["gr_amazon_password"], GR_PASSWORD)
        page.click(SEL["gr_amazon_submit"]); page.wait_for_load_state("domcontentloaded"); pause()
    except Exception as ex:
        log(f"Goodreads login form not found: {ex}"); return False
    if blocked(page) or not gr_logged_in(page):
        log("Goodreads login blocked (captcha/2FA?). Run once with --headed to sign in manually."); return False
    return True

def gr_scrape_tbr(page, tbr):
    n0 = len(tbr.items); seen = set()
    for n in range(1, 60):
        page.goto(f"https://www.goodreads.com/review/list?shelf=to-read&per_page=100&page={n}")
        rows = page.query_selector_all(SEL["gr_tbr_row"])
        new = 0
        for r in rows:
            t = r.query_selector(SEL["gr_tbr_title"])
            if not t: continue
            title = (t.get_attribute("title") or t.inner_text()).strip()
            authors = [a.inner_text() for a in r.query_selector_all(SEL["gr_tbr_author"])]
            if (title, tuple(authors)) in seen: continue
            seen.add((title, tuple(authors))); tbr.add(title, authors); new += 1
        if not new: break
        pause(1.5, 3)
    log(f"Goodreads to-read scraped: {len(tbr.items) - n0} books")
    return len(tbr.items) - n0

def gr_collect(page):
    urls = set()
    for n in range(1, MAX_PAGES + 1):
        page.goto(f"https://www.goodreads.com/giveaway?sort=ending_soon&page={n}")
        links = page.query_selector_all(SEL["gr_giveaway_link"])
        if not links: break
        for a in links:
            h = a.get_attribute("href").split("?")[0]
            urls.add(h if h.startswith("http") else "https://www.goodreads.com" + h)
        pause(1.5, 3)
    log(f"Goodreads: {len(urls)} giveaways found")
    return sorted(urls)

def gr_is_print(text):
    t = text.lower()
    digital = re.search(r"\b(e-?book|kindle|digital|audiobook|audible)\b", t)
    physical = re.search(r"\b(print|hardcover|hardback|paperback)\b", t)
    return bool(physical) and not (digital and not physical)

def gr_country_ok(text):
    m = re.search(r"available (?:for|in)[:\s]+([^\n]+)", text, re.I)
    if not m: return True
    listed = m.group(1).lower()
    return "worldwide" in listed or "international" in listed or MY_COUNTRY.lower() in listed \
        or (MY_COUNTRY == "United States" and re.search(r"\b(usa|u\.s\.)", listed) is not None)

def gr_process(page, url, tbr, live, entered):
    if f"gr:{url}" in entered: return f"skip (already entered): {url}"
    page.goto(url); page.wait_for_load_state("domcontentloaded")
    h1 = page.query_selector(SEL["gr_title"]); title = h1.inner_text().strip() if h1 else ""
    authors = [a.inner_text() for a in page.query_selector_all(SEL["gr_author"])][:3]
    body = page.inner_text("body")
    if not title or not authors:        return f"skip (couldn't read title/author): {url}"
    if not tbr.matches(title, authors): return f"skip (not on TBR): {title} / {authors}"
    if not gr_is_print(body):           return f"skip (not print): {title}"
    if not gr_country_ok(body):         return f"skip (country): {title}"
    btn = page.query_selector(SEL["gr_enter"])
    if not btn:                         return f"skip (no enter button): {title}"
    if not live:                        return f"WOULD ENTER (Goodreads): {title} {url}"
    btn.click(); page.wait_for_load_state("domcontentloaded"); time.sleep(1.5)
    for cb in page.query_selector_all(SEL["gr_terms"]):
        if not cb.is_checked(): cb.check()
    sub = page.query_selector(SEL["gr_submit"])
    if not sub: return f"FAILED (no submit button; check selectors): {title}"
    sub.click(); page.wait_for_load_state("domcontentloaded")
    entered.add(f"gr:{url}"); save_state(entered)
    fm = re.search(r"\b(hardcover|hardback|paperback)\b", body, re.I)
    fmt = f"Print ({fm.group(1).lower()})" if fm else "Print"
    notify("Goodreads", title, authors, fmt, url)
    return f"ENTERED (Goodreads): {title} {url}"

# ---------------- RUN ----------------
def run_once(live, headed):
    entered = load_state(); tbr = Tbr()
    STATS.update(problems=[], entered=0, inspected=0, unreadable=0)
    load_selectors()
    want_sg, want_gr = "storygraph" in PLATFORMS, "goodreads" in PLATFORMS
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(PROFILE_DIR, headless=not headed,
                                                    viewport={"width": 1280, "height": 900})
        try:
            page = ctx.new_page(); page.set_default_timeout(20000)
            sg_ok = sg_login(page) if want_sg else False
            gr_ok = gr_login(page) if want_gr else False
            if want_sg and not sg_ok: problem("StoryGraph login failed (credentials/captcha?)")
            if want_gr and not gr_ok: problem("Goodreads login failed (captcha/2FA/blocked?)")

            counts = {}
            for name, ok, fn in [("StoryGraph", sg_ok, sg_scrape_tbr), ("Goodreads", gr_ok, gr_scrape_tbr)]:
                if not ok: continue
                try: counts[name] = fn(page, tbr) or 0
                except Exception as e: log(f"{name} TBR scrape error: {e}"); counts[name] = 0
                if counts[name] == 0:
                    problem(f"{name}: scraped 0 to-read books. Empty shelf, or the page layout changed.")
            load_csvs(tbr)
            log(f"Total to-read entries: {len(tbr.items)}")

            jobs = []
            for name, ok, collect, fn in [("StoryGraph", sg_ok, sg_collect, sg_process),
                                          ("Goodreads", gr_ok, gr_collect, gr_process)]:
                if not ok: continue
                urls = collect(page)
                if not urls: problem(f"{name}: found 0 giveaways. Layout may have changed.")
                jobs += [(u, fn) for u in urls]
            if not tbr.items:
                problem("No to-read books loaded; skipping entries this run."); jobs = []

            random.shuffle(jobs)
            for url, fn in jobs:
                STATS["inspected"] += 1
                try:
                    result = fn(page, url, tbr, live, entered); log(result)
                    if result.startswith("skip (couldn't read"): STATS["unreadable"] += 1
                    if result.startswith(("ENTERED", "WOULD ENTER")): STATS["entered"] += 1
                except PWTimeout: log(f"timeout: {url}")
                except Exception as e: log(f"error on {url}: {e}")
                pause()
            if STATS["inspected"] >= 5 and STATS["unreadable"] / STATS["inspected"] >= 0.5:
                problem(f"Couldn't read title/author on {STATS['unreadable']} of {STATS['inspected']} giveaway pages. "
                        "Selectors likely need updating.")
        finally:
            ctx.close()
    if STATS["problems"]:
        push("Giveaway bot needs attention", "\n".join("- " + x for x in STATS["problems"]),
             tags=["rotating_light"], priority=4)
    if SUMMARY:
        push("Weekly giveaway run finished",
             f"Checked {STATS['inspected']} giveaways; {'entered' if live else 'would enter'} {STATS['entered']}."
             f"{'' if live else ' (dry run)'}", tags=["books"], priority=2)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="actually enter (default: dry run)")
    ap.add_argument("--headed", action="store_true", help="show the browser (use for first login)")
    ap.add_argument("--daemon", action="store_true", help="loop forever")
    ap.add_argument("--once", action="store_true", help="single pass (default)")
    ap.add_argument("--every-hours", type=float, default=168, help="hours between runs in --daemon mode (default: weekly)")
    ap.add_argument("--test-notify", action="store_true", help="send a sample notification and exit")
    a = ap.parse_args()
    if a.test_notify:
        notify("TEST", "Radicals: Echoes of the First Woman to Run for President", ["Jen Knox"],
               "Print (hardcover)", "https://app.thestorygraph.com/giveaways/example"); return
    while True:
        try: run_once(a.live, a.headed)
        except Exception as e:
            log(f"run failed: {e}"); push("Giveaway bot crashed", str(e)[:300], tags=["rotating_light"], priority=4)
        if not a.daemon: break
        wait = a.every_hours * 3600 * random.uniform(0.9, 1.1)
        log(f"sleeping {wait/3600:.1f}h"); time.sleep(wait)

if __name__ == "__main__":
    main()
