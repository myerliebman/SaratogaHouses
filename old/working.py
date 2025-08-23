#!/usr/bin/env python3
"""
redfin_scraper_plus.py • 2025-08-07
Outputs:
    address, price, lotSize, yearBuilt, livingArea, bedrooms, bathrooms
to house_details_redfin.csv
"""

import csv, html, os, random, re, time, json
import requests
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.action_chains import ActionChains   # add at top


# ───────────────────────── helpers ──────────────────────────
def handle_cookie_banner(driver):
    try:
        btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//*[contains(text(),'Accept all cookies')]"
                " | //button[contains(text(),'Accept')]")))
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(1)
    except TimeoutException:
        pass


def _visible_price(driver, secs=7):
    css = "[data-testid='avm-price'] .value, .statsValue.price"
    try:
        el = WebDriverWait(driver, secs).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, css)))
        txt = el.text.strip()
        if not txt:
            return None
        par = (el.find_element(By.XPATH, "..").get_attribute("data-testid") or "")
        return ("Redfin Estimate" if "avm-price" in par else "List Price"), txt
    except TimeoutException:
        return None


def _regex_price(src):
    m = re.search(r'"avmText":"([^"]*?\$[0-9,]+)', src)
    if m:
        return "Redfin Estimate", re.search(r'\$[0-9,]+', html.unescape(m.group(1))).group(0)
    m = re.search(r'"segments":\s*```math.*?"text":"[^"]*?FOR \$([0-9,]+)', src, re.DOTALL)
    if m:
        return "Sold Price", f"${m.group(1)}"
    return None


###############################################################################
# NEW: grab “Property details ▾  Public facts” list (Lot Size, Year Built …)  #
###############################################################################
def _from_property_details(driver):
    """
    Return dict with any values we can pick from the ‘Property details’ accordion.
    Only keys we actually find are returned → easy merge into _parse_extras().
    """
    out = {}
    try:
        # Jump to property-details section so it’s definitely in DOM/viewport
        hdr = driver.find_element(By.XPATH,
            "//h2[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'property details')]")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", hdr)
        time.sleep(0.3)
    except NoSuchElementException:
        return out     # Section not on page

    # open “Public facts” box if it’s collapsed ─ it has a caret <button>
    try:
        caret = driver.find_element(By.XPATH,
            "//div[contains(@data-rf-test-id,'public-facts')]//button[contains(@class,'AccordionItem')]")
        if caret.get_attribute("aria-expanded") == "false":
            driver.execute_script("arguments[0].click();", caret)
            time.sleep(0.2)
    except NoSuchElementException:
        pass   # already expanded (older layout) or missing

    # Now harvest all <li> strings
    lis = driver.find_elements(By.XPATH,
            "//div[contains(@data-rf-test-id,'public-facts')]//li")
    txt = "  ".join(li.text for li in lis)

    # Regex pick-off
    m = re.search(r'Lot Size\s*[:\-]?\s*([0-9\.]+)', txt, re.I)
    if m: out["lotSize"] = float(m.group(1))

    m = re.search(r'Year Built\s*[:\-]?\s*([0-9]{4})', txt, re.I)
    if m: out["yearBuilt"] = int(m.group(1))

    m = re.search(r'Sq\.?\s*Ft\.?\s*[:\-]?\s*([0-9,]+)', txt, re.I)
    if m: out["livingArea"] = int(m.group(1).replace(",", ""))

    return out
###############################################################################
def _parse_extras(driver, src):
    """
    Returns a dict with:
        lotSize (float, acres) | yearBuilt (int) | livingArea (int, sqft)
        bedrooms (int) | bathrooms (float)
    Pull from page JSON first; whatever is still missing comes from
    the Public-facts accordion.
    """
    d = {"lotSize": None, "yearBuilt": None, "livingArea": None,
         "bedrooms": None, "bathrooms": None}

    # ── 1️⃣  quick JSON grabs ───────────────────────────────────────────────
    m = re.search(r'"sqFt"\s*:\s*([0-9,]+)', src)
    if m: d["livingArea"] = int(m.group(1).replace(",", ""))

    m = re.search(r'"lotSize[A-Za-z]*"\s*:\s*([0-9,\.]+)', src)
    if m: d["lotSize"] = float(m.group(1).replace(",", ""))

    m = re.search(r'"yearBuilt"\s*:\s*([0-9]{4})', src)
    if m: d["yearBuilt"] = int(m.group(1))

    m = re.search(r'"beds"\s*:\s*([0-9]+)', src)
    if m: d["bedrooms"] = int(m.group(1))

    m = re.search(r'"baths"\s*:\s*([0-9\.]+)', src)
    if m: d["bathrooms"] = float(m.group(1))

    # ── 2️⃣  consult “Public facts” bullets and prefer its Year Built ────────
    # Always read the Public facts text so we can override yearBuilt with the
    # value users see on the page. On Redfin, inline JSON can be stale.
    pf_txt = _public_facts_text(driver)

    # Prefer Public facts values when present to match what the user sees
    m = re.search(r'Year Built\s*[:\-]?\s*([0-9]{4})', pf_txt, re.I)
    if m:
        d["yearBuilt"] = int(m.group(1))

    # Living area (Sq. Ft.)
    m = re.search(r'Sq\.?\s*Ft\.?\s*[:\-]?\s*([0-9,]+)', pf_txt, re.I)
    if m:
        d["livingArea"] = int(m.group(1).replace(",", ""))

    # Lot size with unit normalization (supports acres or square feet)
    m = re.search(r'Lot Size\s*[:\-]?\s*([0-9,\.]+)\s*(acres|square feet)?', pf_txt, re.I)
    if m:
        num  = float(m.group(1).replace(",", ""))
        unit = (m.group(2) or "").strip().lower()
        d["lotSize"] = round(num / 43560, 3) if unit == "square feet" else num

    # Beds / Baths
    m = re.search(r'Beds?\s*[:\-]?\s*([0-9]+)', pf_txt, re.I)
    if m:
        d["bedrooms"] = int(m.group(1))
    m = re.search(r'Baths?\s*[:\-]?\s*([0-9\.]+)', pf_txt, re.I)
    if m:
        d["bathrooms"] = float(m.group(1))

    # ── 3️⃣  anything still None? backfill from Public facts ────────────────
    if any(v is None for v in d.values()):

        if d["lotSize"] is None:
            m = re.search(r'Lot Size\s*[:\-]?\s*([0-9,\.]+)\s*(acres|square feet)?', pf_txt, re.I)
            if m:
                num  = float(m.group(1).replace(",", ""))
                unit = (m.group(2) or "").strip().lower()  # None → "" → "acres" or "square feet" or ""
                if unit == "square feet":
                    d["lotSize"] = round(num / 43560, 3)
                else:
                    d["lotSize"] = num

        if d["livingArea"] is None:
            m = re.search(r'Sq\.?\s*Ft\.?\s*[:\-]?\s*([0-9,]+)', pf_txt, re.I)
            if m: d["livingArea"] = int(m.group(1).replace(",", ""))

        if d["bedrooms"] is None:
            m = re.search(r'Beds?\s*[:\-]?\s*([0-9]+)', pf_txt, re.I)
            if m: d["bedrooms"] = int(m.group(1))

        if d["bathrooms"] is None:
            m = re.search(r'Baths?\s*[:\-]?\s*([0-9\.]+)', pf_txt, re.I)
            if m: d["bathrooms"] = float(m.group(1))

    # ── 4️⃣  final fallback: read top summary stats (Beds, Baths, Sq Ft) ─────
    if any(d[k] is None for k in ("bedrooms", "bathrooms", "livingArea")):
        top = _top_summary_stats(driver)
        for k in ("bedrooms", "bathrooms", "livingArea"):
            if d[k] is None and k in top:
                d[k] = top[k]

    return d


def _digits(txt):
    return re.sub(r"[^\d.]", "", txt) if txt else ""


# ───────────────────── resolve via Redfin autocomplete API ─────────────────────
def _resolve_property_url_via_api(address: str) -> str:
    """
    Query Redfin's autocomplete endpoint to get the canonical property URL.
    Returns a full https URL string when found; otherwise empty string.
    """
    base = "https://www.redfin.com/stingray/api/gis-autocomplete"
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    )
    headers = {"User-Agent": ua, "Accept": "*/*"}
    # Try without and with market hint
    tries = [
        {"location": address, "start": 0, "count": 10, "v": 2},
        {"location": address + ", NY", "start": 0, "count": 10, "v": 2, "market": "albany"},
    ]
    for params in tries:
        try:
            r = requests.get(base, params=params, headers=headers, timeout=8)
            if r.status_code != 200:
                continue
            txt = r.text.lstrip(")]}'\n \t")
            # Fast-path: regex extract a /home/<id> URL from payload
            m = re.search(r"\"url\"\s*:\s*\"(\/[^\"]*\/home\/[0-9]+)\"", txt)
            if m:
                rel = m.group(1)
                return "https://www.redfin.com" + rel
            # As fallback, parse JSON and scan any 'rows'
            try:
                data = json.loads(txt)
            except Exception:
                continue
            sections = data.get("sections") or []
            for sec in sections:
                for row in sec.get("rows", []):
                    url = row.get("url") or row.get("link") or ""
                    if url and "/home/" in url:
                        if not url.startswith("http"):
                            url = "https://www.redfin.com" + url
                        return url
        except Exception:
            continue
    return ""


# ───────────────────── typeahead suggestions ─────────────────────
def _try_click_typeahead_suggestion(driver, address: str, timeout: int = 6) -> bool:
    """
    When typing an address in the Redfin search box, a typeahead dropdown often
    appears with direct address matches. This tries to click the best match.
    Returns True if a click was performed.
    """
    try:
        wait = WebDriverWait(driver, timeout)

        # Normalize tokens to match by house number and street name
        addr_l = address.lower()
        tokens = [t for t in re.split(r"[^a-z0-9]+", addr_l) if t]
        house_num = next((t for t in tokens if t.isdigit()), "")
        street_tokens = [t for t in tokens if not t.isdigit()]

        def _candidate_elements():
            selectors = [
                "ul[role='listbox'] li[role='option'] a",
                "ul[role='listbox'] li[role='option']",
                "[data-rf-test-name='typeahead-suggestion'] a",
                "[data-rf-test-name='typeahead-suggestion']",
                "div[data-rf-test-name='typeahead-results'] a",
                ".search-box-dropdown li a",
            ]
            for css in selectors:
                els = driver.find_elements(By.CSS_SELECTOR, css)
                if els:
                    return els
            return []

        # Wait until any list shows up
        els = wait.until(lambda d: _candidate_elements())
        if not els:
            return False

        # Pick best match: contains house number and any street token
        best = None
        for el in els:
            try:
                txt = el.text.strip().lower()
            except Exception:
                continue
            if not txt:
                continue
            has_num = house_num and house_num in txt
            has_street = any(tok in txt for tok in street_tokens[:3])
            if has_num and has_street:
                best = el
                break
            if not best and (has_num or has_street):
                best = el
        if not best:
            best = els[0]

        before_url = driver.current_url
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best)
        except Exception:
            pass
        driver.execute_script("arguments[0].click();", best)

        # Wait for navigation or dropdown to disappear
        try:
            WebDriverWait(driver, 8).until(lambda d: d.current_url != before_url)
        except TimeoutException:
            try:
                WebDriverWait(driver, 4).until(EC.staleness_of(best))
            except TimeoutException:
                pass
        return True
    except TimeoutException:
        return False
    except Exception:
        return False


# ───────────────────── results page: click by href match ─────────────────────
def _try_click_result_link_by_href(driver, address: str, timeout: int = 8) -> bool:
    """
    On search/area pages, try clicking an <a> whose href looks like the property
    URL and matches the address tokens (house number + street token).
    """
    try:
        wait = WebDriverWait(driver, timeout)

        addr_l = address.lower()
        tokens = [t for t in re.split(r"[^a-z0-9]+", addr_l) if t]
        if not tokens:
            return False
        house_num = next((t for t in tokens if t.isdigit()), "")
        street_tok = next((t for t in tokens if not t.isdigit()), "")
        if not street_tok:
            return False

        def _candidates():
            # Many anchor variants; filter in Python for token presence
            anchors = driver.find_elements(By.XPATH, "//a[contains(@href,'/home/')]")
            return anchors

        anchors = wait.until(lambda d: _candidates())
        if not anchors:
            return False

        street_tok_hyphen = street_tok.replace(" ", "-")
        best = None
        for a in anchors:
            try:
                href = (a.get_attribute("href") or "").lower()
            except Exception:
                continue
            if not href or "/home/" not in href:
                continue
            cond_num = not house_num or house_num in href
            cond_street = street_tok in href or street_tok_hyphen in href
            if cond_num and cond_street:
                best = a
                break
        if not best:
            return False

        before = driver.current_url
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best)
        except Exception:
            pass
        driver.execute_script("arguments[0].click();", best)
        try:
            WebDriverWait(driver, 10).until(lambda d: d.current_url != before)
        except TimeoutException:
            pass
        return True
    except TimeoutException:
        return False
    except Exception:
        return False


# ───────────────────── helpers: disambiguation popup ─────────────────────
def _click_first_disambiguation_result(driver, timeout: int = 7) -> bool:
    """
    If a "Did you mean" disambiguation dialog appears, click the first
    address option. Returns True if a click was performed.
    """
    try:
        wait = WebDriverWait(driver, timeout)

        def _find_first_anchor():
            selectors = [
                ".expanded-results a.item-title",
                ".resultsView a.item-title",
                "div.item-row.item-row-show-sections.clickable a.item-title",
                "[data-rf-test-name='expanded-results'] a.item-title",
                "a.item-title.block",
                # Additional modern selectors
                "[data-rf-test-name='searchAddressList'] a",
                "[data-rf-test-id='searchAddressList'] a",
                "div[data-rf-test-name='search-result-row'] a",
            ]
            for css in selectors:
                els = driver.find_elements(By.CSS_SELECTOR, css)
                if els:
                    return els[0]
            return None

        el = wait.until(lambda d: _find_first_anchor())
        if not el:
            return False

        before_url = driver.current_url
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        except Exception:
            pass
        driver.execute_script("arguments[0].click();", el)

        # Wait for either navigation or the dialog to disappear
        try:
            WebDriverWait(driver, 8).until(
                lambda d: d.current_url != before_url
            )
        except TimeoutException:
            try:
                WebDriverWait(driver, 4).until(EC.staleness_of(el))
            except TimeoutException:
                pass

        return True
    except TimeoutException:
        return False
    except Exception:
        return False

# ───────────────────── core scrape routine ─────────────────────
def scrape(driver, address):
    wait = WebDriverWait(driver, 5)

    # Guard against long network stalls when navigating
    try:
        driver.get("https://www.redfin.com")
    except TimeoutException:
        try:
            driver.execute_script("window.stop();")
        except Exception:
            pass
    handle_cookie_banner(driver)
    box = wait.until(EC.presence_of_element_located((By.ID, "search-box-input")))

    # First, try resolving via Redfin's internal autocomplete API and navigate directly
    direct_url = _resolve_property_url_via_api(address)
    if direct_url:
        try:
            driver.get(direct_url)
        except TimeoutException:
            try:
                driver.execute_script("window.stop();")
            except Exception:
                pass
        clicked_typeahead = True
    else:
        box.clear(); box.send_keys(address)
        # Try to click the typeahead suggestion first; fallback to Enter
        clicked_typeahead = _try_click_typeahead_suggestion(driver, address, timeout=6)
        if not clicked_typeahead:
            box.send_keys(Keys.ENTER)

    # ─── Handle "Did you mean" popup by clicking the first result ────
    clicked = _click_first_disambiguation_result(driver, timeout=7)
    if clicked or clicked_typeahead:
        # Give the property page a moment and wait for price/summary to exist
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    "[data-testid='avm-price'] .value, .statsValue.price, .home-main-stats-variant"
                ))
            )
        except TimeoutException:
            pass
    else:
        # Normal case: no dialog shown. Keep a small delay for page load.
        time.sleep(3)
        # Extra fallback: on area/search page, try clicking the card/link by href
        clicked_link = _try_click_result_link_by_href(driver, address, timeout=8)
        if clicked_link:
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((
                        By.CSS_SELECTOR,
                        "[data-testid='avm-price'] .value, .statsValue.price, .home-main-stats-variant"
                    ))
                )
            except TimeoutException:
                pass
    # ──────────────────────────────────────────────────────────────────

    # wait.until(EC.presence_of_element_located((By.TAG_NAME, "body"))); time.sleep(3)

    price_pair = _visible_price(driver) or _regex_price(driver.page_source)

    if not price_pair and not clicked and not clicked_typeahead:  # secondary enter only if we didn't just click a result
        try:
            cur = driver.current_url
            box2 = wait.until(EC.presence_of_element_located((By.ID, "search-box-input")))
            driver.execute_script("arguments[0].focus();", box2)
            box2.send_keys(Keys.END); box2.send_keys(Keys.ENTER)
            WebDriverWait(driver, 10).until(EC.url_changes(cur)); time.sleep(3)
            price_pair = _visible_price(driver, 5) or _regex_price(driver.page_source)
        except TimeoutException:
            pass

    if not price_pair:  # sold filter
        cur = driver.current_url
        sold_url = cur + (",include=sold" if "/filter/" in cur else "/filter/include=sold")
        try:
            driver.get(sold_url)
        except TimeoutException:
            try:
                driver.execute_script("window.stop();")
            except Exception:
                pass
        time.sleep(5)
        price_pair = _visible_price(driver, 5) or _regex_price(driver.page_source)

    price_clean = _digits(price_pair[1]) if price_pair else ""
    html_src = driver.page_source
    extras = _parse_extras(driver, html_src)

    # Optional debug dump:
    # with open("html.txt", "w", encoding="utf-8") as f: f.write(html_src)

    return {
        "price":      price_clean,
        "lotSize":    extras["lotSize"]    or "",
        "yearBuilt":  extras["yearBuilt"]  or "",
        "livingArea": extras["livingArea"] or "",
        "bedrooms":   extras["bedrooms"]   or "",
        "bathrooms":  extras["bathrooms"]  or "",
    }

###############################################################################
# ─── helper: open the Public facts accordion & return raw list text ─────────#
###############################################################################
###############################################################################
# helper: open the “Public facts” accordion & return the bullet-list text     #
###############################################################################
def _public_facts_text(driver) -> str:
    """
    Scroll to ‘Property details’, expand ‘Public facts’ if needed,
    and return one big string containing all <li> bullet texts.
    """
    # 1️⃣  make sure the Property-details area is on screen
    try:
        h2 = driver.find_element(
            By.XPATH,
            "//h2[translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')"
            "        ='property details']"
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", h2)
    except NoSuchElementException:
        return ""

    # 2️⃣  grab the <h3>Public facts</h3> header
    try:
        pf_header = driver.find_element(
            By.XPATH,
            "//h3[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),"
            "      'public facts')]"
        )
    except NoSuchElementException:
        return ""

    # 3️⃣  the real accordion container is the closest ancestor with class “expandableSection”
    pf_container = pf_header.find_element(
        By.XPATH, "./ancestor::*[contains(@class,'expandableSection')][1]"
    )

    # 4️⃣  expand if collapsed
    classes = pf_container.get_attribute("class") or ""
    if "collapsed" in classes and "expanded" not in classes:
        driver.execute_script("arguments[0].click();", pf_header)
        try:
            WebDriverWait(driver, 4).until(
                lambda d: "expanded" in pf_container.get_attribute("class")
            )
        except TimeoutException:
            pass

    # 5️⃣  wait until bullet list is present
    try:
        WebDriverWait(driver, 4).until(
            EC.presence_of_element_located((By.XPATH, ".//li"))
        )
    except TimeoutException:
        return ""

    # 6️⃣  concatenate the bullet texts
    bullets = pf_container.find_elements(By.XPATH, ".//li")
    return "  ".join(li.text for li in bullets)

def _top_summary_stats(driver) -> dict:
    """
    Read the top summary blocks that show Beds, Baths, and Sq Ft when
    the Public facts list is empty. Returns any values it can find.
    """
    out = {}
    try:
        # This container holds the top stats blocks
        container = None
        for sel in [
            "div.home-main-stats-variant",
            "[data-rf-test-id='home-main-stats']",
        ]:
            try:
                container = driver.find_element(By.CSS_SELECTOR, sel)
                break
            except NoSuchElementException:
                continue
        if not container:
            return out

        # Ensure it's within viewport for reliability
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", container)
        except Exception:
            pass

        blocks = container.find_elements(By.CSS_SELECTOR, ".stat-block")
        for bl in blocks:
            try:
                value_txt = bl.find_element(By.CSS_SELECTOR, ".statsValue").text.strip()
            except NoSuchElementException:
                continue
            if not value_txt:
                continue
            label_txt = ""
            try:
                label_txt = bl.find_element(By.CSS_SELECTOR, ".statsLabel").text.strip().lower()
            except NoSuchElementException:
                try:
                    label_txt = bl.text.strip().lower()
                except Exception:
                    label_txt = ""

            if not label_txt:
                continue

            if "bed" in label_txt:
                digits = re.sub(r"[^\d]", "", value_txt)
                if digits:
                    out["bedrooms"] = int(digits)
            elif "bath" in label_txt:
                num = re.sub(r"[^\d\.]", "", value_txt)
                if num:
                    out["bathrooms"] = float(num)
            elif "sq" in label_txt:
                digits = re.sub(r"[^\d]", "", value_txt)
                if digits:
                    out["livingArea"] = int(digits)
    except Exception:
        # Be silent on failures; this is a best-effort fallback
        return out

    return out
    
# ───────────────────────── runner ────────────────────────────
def main():
    IN_CSV, OUT_CSV = "addresses2.csv", "full_redfin_results.csv"

    opts = Options()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(options=opts); driver.maximize_window()

    try:
        file_exists = os.path.exists(OUT_CSV)
        with open(OUT_CSV, 'a', newline='', encoding='utf-8') as fo:
            wtr = csv.writer(fo)
            header = ["address", "price", "lotSize", "yearBuilt",
                      "livingArea", "bedrooms", "bathrooms"]
            if not file_exists:
                wtr.writerow(header)

            with open(IN_CSV, newline='', encoding='utf-8') as fi:
                rdr = csv.reader(fi)
                rows = list(rdr)
                if rows and rows[0][0].strip().lower() == "address":
                    rows = rows[1:]

                for row in rows:
                    if not row:
                        continue
                    addr = row[0].strip()
                    print("\n──── Scraping:", addr)
                    
                    try:
                        data = scrape(driver, addr)
                        wtr.writerow([addr,
                                      data["price"], data["lotSize"], data["yearBuilt"],
                                      data["livingArea"], data["bedrooms"], data["bathrooms"]])
                        fo.flush(); os.fsync(fo.fileno())     #  <── instant-save
                        print("   →", data)
                    except Exception as e:
                        print(f"   ❌ ERROR scraping {addr}: {e}")
                        # Write empty data for failed addresses to maintain CSV structure
                        wtr.writerow([addr, "", "", "", "", "", ""])
                        fo.flush(); os.fsync(fo.fileno())
                    
                    time.sleep(random.uniform(4, 8))

    finally:
        driver.quit()


if __name__ == "__main__":
    main()