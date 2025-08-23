#!/usr/bin/env python3
"""
redfin_scraper_plus.py  •  2025-08-07
Outputs:
    address, price, lotSize, yearBuilt, livingArea, bedrooms, bathrooms
to house_details_redfin.csv
"""

import csv, html, random, re, time, os, sys
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, NoSuchElementException, ElementClickInterceptedException

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


def _digits(txt):  # strip $, commas → digits
    return re.sub(r"[^\d.]", "", txt) if txt else ""


# ───────────── Property details: scroll, expand, parse ─────────────
PD_HDR_XPATH = "//*[self::h2 or self::h3 or self::h4][contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'property details')]"
PUBLIC_FACTS_XPATH_REL = ".//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'public facts')]"

def _scroll_to_property_details_and_get_text(driver, timeout=12):
    """
    Scrolls to the 'Property details' section and expands 'Public facts' if needed.
    Returns the visible text of that section (best-effort).
    """
    end = time.time() + timeout
    pd_container = None

    while time.time() < end:
        # Try to find the Property details header
        headers = driver.find_elements(By.XPATH, PD_HDR_XPATH)
        if headers:
            hdr = headers[0]
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", hdr)
            time.sleep(0.8)
            # The immediate container for the section
            try:
                pd_container = hdr.find_element(By.XPATH, "./ancestor::*[self::section or self::div][1]")
            except NoSuchElementException:
                pd_container = hdr

            # Try to expand "Public facts" accordion if present
            try:
                pf_headers = pd_container.find_elements(By.XPATH, PUBLIC_FACTS_XPATH_REL)
                if not pf_headers:
                    # sometimes it's outside the immediate container
                    pf_headers = driver.find_elements(By.XPATH, PUBLIC_FACTS_XPATH_REL)
                for pf in pf_headers[:1]:
                    try:
                        # Find a clickable ancestor button if it exists
                        try:
                            btn = pf.find_element(By.XPATH, ".//ancestor-or-self::button[1]")
                        except NoSuchElementException:
                            btn = pf
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                        time.sleep(0.3)
                        driver.execute_script("arguments[0].click();", btn)
                        time.sleep(0.6)
                    except (ElementClickInterceptedException, StaleElementReferenceException):
                        pass
            except Exception:
                pass

            try:
                return pd_container.text
            except StaleElementReferenceException:
                # Re-acquire once
                try:
                    pd_container = hdr.find_element(By.XPATH, "./ancestor::*[self::section or self::div][1]")
                    return pd_container.text
                except Exception:
                    pass

        # Not found yet — scroll further
        driver.execute_script("window.scrollBy(0, Math.max(window.innerHeight, 900));")
        time.sleep(0.7)

    # Fallback: return the whole body text if we couldn't isolate the section
    try:
        return driver.find_element(By.TAG_NAME, "body").text
    except Exception:
        return driver.page_source


def _parse_property_details_text(txt):
    """
    Parse Year Built, Lot Size, and Sq. Ft. from the 'Property details' text block.
    Returns dict: yearBuilt (int), lotSize (float acres), livingArea (int sqft).
    """
    out = {"yearBuilt": None, "lotSize": None, "livingArea": None}

    # Year Built
    m = re.search(r'\bYear\s*Built\s*:\s*([0-9]{4})\b', txt, re.IGNORECASE)
    if m:
        out["yearBuilt"] = int(m.group(1))

    # Sq. Ft. (living area)
    m = re.search(r'\b(?:Sq\.?\s*Ft\.?|Sq\s*Ft|Square\s*Feet|SqFt|Living\s*Area)\s*:\s*([0-9,]+)\b', txt, re.IGNORECASE)
    if m:
        out["livingArea"] = int(m.group(1).replace(",", ""))

    # Lot Size: capture number and units, convert sq ft → acres
    units_re = r'(acres?|ac|sq\.?\s*ft|sqft|square\s*feet)'
    m = re.search(r'\bLot\s*Size\s*:\s*([0-9,\.]+)\s*' + units_re + r'\b', txt, re.IGNORECASE)
    if m:
        num = float(m.group(1).replace(",", ""))
        units = m.group(2).lower().strip()
        if "sq" in units or "square" in units:
            acres = num / 43560.0
            out["lotSize"] = round(acres, 4)
        else:
            # acres/ac
            out["lotSize"] = round(num, 4)

    return out


# ───────────── JSON/text fallbacks for beds/baths + living area ─────────────
def _parse_extras_from_html(src):
    """Fallback parse: livingArea(sqft), beds, baths, yearBuilt, lotSize if present in JSON or general text."""
    d = {"lotSize": None, "yearBuilt": None, "livingArea": None,
         "bedrooms": None, "bathrooms": None}

    # JSON: sq ft (flat or nested)
    m = re.search(r'"sqFt"\s*:\s*([0-9,]+)\b', src)
    if m:
        d["livingArea"] = int(m.group(1).replace(',', ''))
    else:
        m = re.search(r'"sqFt"\s*:\s*{[^}]*"value"\s*:\s*([0-9,]+)\b', src)
        if m:
            d["livingArea"] = int(m.group(1).replace(',', ''))

    # JSON: beds/baths
    m = re.search(r'"beds"\s*:\s*([0-9]+)\b', src)
    if m: d["bedrooms"] = int(m.group(1))
    m = re.search(r'"baths"\s*:\s*([0-9\.]+)\b', src)
    if m:
        try:
            d["bathrooms"] = float(m.group(1))
        except ValueError:
            pass

    # JSON: year built
    m = re.search(r'"yearBuilt"\s*:\s*([0-9]{4})\b', src)
    if m: d["yearBuilt"] = int(m.group(1))

    # JSON: lot size (numeric only, units unknown). We'll leave conversion to caller.
    m = re.search(r'"lotSize[A-Za-z]*"\s*:\s*([0-9,\.]+)\b', src)
    if m:
        try:
            d["lotSize"] = float(m.group(1).replace(",", ""))
        except ValueError:
            pass

    # Text fallbacks
    if d["livingArea"] is None:
        m = re.search(r'([0-9,]+)\s+square\s+foot', src, re.IGNORECASE)
        if m: d["livingArea"] = int(m.group(1).replace(",", ""))

    if d["bedrooms"] is None or d["bathrooms"] is None:
        m = re.search(r'with\s+([0-9]+)\s+bedrooms?\s+and\s+([0-9\.]+)\s+bathrooms?', src, re.IGNORECASE)
        if m:
            if d["bedrooms"] is None: d["bedrooms"] = int(m.group(1))
            if d["bathrooms"] is None:
                try:
                    d["bathrooms"] = float(m.group(2))
                except ValueError:
                    pass

    return d


# ───────────────────── core scrape routine ─────────────────────
def scrape(driver, address):
    wait = WebDriverWait(driver, 15)

    driver.get("https://www.redfin.com")
    handle_cookie_banner(driver)
    box = wait.until(EC.presence_of_element_located((By.ID, "search-box-input")))
    box.clear(); box.send_keys(address); box.send_keys(Keys.ENTER)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body"))); time.sleep(3)

    price_pair = _visible_price(driver) or _regex_price(driver.page_source)

    if not price_pair:   # SECOND-ENTER fallback (multi-grid)
        try:
            cur = driver.current_url
            box2 = wait.until(EC.presence_of_element_located((By.ID, "search-box-input")))
            driver.execute_script("arguments[0].focus();", box2)
            box2.send_keys(Keys.END); box2.send_keys(Keys.ENTER)
            WebDriverWait(driver, 10).until(EC.url_changes(cur)); time.sleep(3)
            price_pair = _visible_price(driver, 5) or _regex_price(driver.page_source)
        except TimeoutException:
            pass

    if not price_pair:   # SOLD filter fallback
        cur = driver.current_url
        sold_url = cur + (",include=sold" if "/filter/" in cur else "/filter/include=sold")
        driver.get(sold_url); time.sleep(5)
        price_pair = _visible_price(driver, 5) or _regex_price(driver.page_source)

    price_clean = _digits(price_pair[1]) if price_pair else ""

    # 1) Grab basics from HTML/JSON (beds/baths and possibly living area)
    extras = _parse_extras_from_html(driver.page_source)

    # 2) Force read from the "Property details" → "Public facts" table
    pd_text = _scroll_to_property_details_and_get_text(driver, timeout=12)
    pd_vals = _parse_property_details_text(pd_text)

    # Overwrite with values from Property details (authoritative)
    for k in ("livingArea", "lotSize", "yearBuilt"):
        if pd_vals.get(k) is not None:
            extras[k] = pd_vals[k]

    # Save the final page HTML to a file for debugging (after all attempts)
    print("  > Saving final page HTML to html.txt...")
    try:
        with open("html.txt", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except Exception as e:
        print("  ! Could not save html.txt:", e)

    return {
        "price":      extras.get("price", "") or price_clean,
        "lotSize":    extras["lotSize"]    if extras["lotSize"]    is not None else "",
        "yearBuilt":  extras["yearBuilt"]  if extras["yearBuilt"]  is not None else "",
        "livingArea": extras["livingArea"] if extras["livingArea"] is not None else "",
        "bedrooms":   extras["bedrooms"]   if extras["bedrooms"]   is not None else "",
        "bathrooms":  extras["bathrooms"]  if extras["bathrooms"]  is not None else "",
    }


# ───────────────────────── runner ────────────────────────────
def main():
    IN_CSV, OUT_CSV = "addresses.csv", "house_details_redfin.csv"

    opts = Options()
    # opts.add_argument("--headless")  # uncomment if desired
    # opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(options=opts)
    driver.maximize_window()

    try:
        # Resume support: load already processed addresses from OUT_CSV
        processed = set()
        if os.path.exists(OUT_CSV):
            try:
                with open(OUT_CSV, newline="", encoding="utf-8") as fr:
                    rdr = csv.DictReader(fr)
                    if rdr.fieldnames and "address" in rdr.fieldnames:
                        for r in rdr:
                            a = (r.get("address") or "").strip()
                            if a:
                                processed.add(a.lower())
            except Exception:
                pass

        # Open output CSV in append mode and ensure durable writes
        file_exists = os.path.exists(OUT_CSV)
        with open(OUT_CSV, 'a', newline='', encoding='utf-8') as fo:
            wtr = csv.writer(fo)
            header = ["address", "price", "lotSize", "yearBuilt",
                      "livingArea", "bedrooms", "bathrooms"]
            if not file_exists:
                wtr.writerow(header)
                fo.flush(); os.fsync(fo.fileno())

            # Load input rows
            with open(IN_CSV, newline='', encoding='utf-8') as fi:
                rdr = csv.reader(fi)
                rows = list(rdr)
                if rows and rows[0] and rows[0][0].strip().lower() == "address":
                    rows = rows[1:]

                for row in rows:
                    if not row:
                        continue
                    addr = row[0].strip()
                    if not addr:
                        continue
                    if addr.lower() in processed:
                        print(f"→ Skipping already processed: {addr}")
                        continue

                    print("\n──── Scraping:", addr)
                    try:
                        data = scrape(driver, addr)
                    except Exception as e:
                        print(f"  ! Error scraping {addr}: {e}")
                        # still write a line so we can see failures and resume later
                        data = {"price":"", "lotSize":"", "yearBuilt":"", "livingArea":"", "bedrooms":"", "bathrooms":""}

                    wtr.writerow([addr,
                                  data["price"], data["lotSize"], data["yearBuilt"],
                                  data["livingArea"], data["bedrooms"], data["bathrooms"]])
                    fo.flush(); os.fsync(fo.fileno())  # durable write per row
                    processed.add(addr.lower())
                    print("   →", data)
                    time.sleep(random.uniform(4, 8))

    finally:
        driver.quit()


if __name__ == "__main__":
    main()