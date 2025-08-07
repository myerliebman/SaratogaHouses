#!/usr/bin/env python3
"""
redfin_scraper_v2.py   –   2025-08-07
Outputs CSV columns: address, price, lotSize(acres), yearBuilt,
                     livingArea(sqft), bedrooms, bathrooms
"""

import csv, html, random, re, time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

ACRES_PER_SQFT = 43_560
WAIT_SECS      = 15
PAUSE_RANGE    = (4, 8)          # polite pause between addresses


# ────────────────────────── small helpers ─────────────────────────
def handle_cookie_banner(driver):
    try:
        b = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable(
                (By.XPATH,
                 "//*[contains(text(),'Accept all cookies')]"
                 "| //button[contains(text(),'Accept')]")))
        driver.execute_script("arguments[0].click();", b)
        time.sleep(1)
    except TimeoutException:
        pass


def _strip_money(txt: str) -> str:
    """'$426,090' → '426090' (digits only for CSV)."""
    return re.sub(r"[^\d]", "", txt)


def _visible_price(driver, secs=7):
    css = "[data-testid='avm-price'] .value, .statsValue.price"
    try:
        el = WebDriverWait(driver, secs).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, css)))
        return el.text.strip()
    except TimeoutException:
        return ""


def _regex_price(src: str):
    for pat in (
        r'"avmText":"[^"]*?\$([0-9,]+)',                          # Redfin estimate
        r'"segments":\s*\[.*?FOR\s+\$([0-9,]+)'                   # sold banner
    ):
        m = re.search(pat, src, re.DOTALL)
        if m:
            return m.group(1)
    return ""


# ─────────────── parse living area / lot size / facts ───────────────
def parse_home_facts(src: str) -> dict:
    """Return dict with livingArea, lotSize (acres), yearBuilt, beds, baths."""
    facts = {k: "" for k in
             ("livingArea", "lotSize", "yearBuilt", "beds", "baths")}

    # 1️⃣  Embedded JSON (fast & language-agnostic) -------------------
    block = re.search(r'"addressSectionInfo":\{.+?}', src, re.DOTALL)
    if block:
        j = block.group(0)

        def jnum(pat, as_float=False):
            m = re.search(pat, j)
            if m:
                return (m.group(1).replace(",", "") if not as_float
                        else m.group(1))
            return ""

        # living area -------------------------------------------------
        facts["livingArea"] = (
            jnum(r'"sqFtFinished"\s*:\s*([0-9,]+)') or
            jnum(r'"sqFt"\s*\{\s*"displayLevel":[0-9]+,\s*"value":\s*([0-9,]+)')
        )

        # lot size (convert to acres if we only get square-feet) ------
        lot_sqft = jnum(r'"lotSize"\s*:\s*([0-9,]+)')
        if not lot_sqft:
            lot_sqft = jnum(r'"lotSize"\s*\{\s*"displayLevel":[0-9]+,\s*"value":\s*([0-9,]+)')
        if lot_sqft:
            acres = round(int(lot_sqft.replace(",", "")) / ACRES_PER_SQFT, 2)
            facts["lotSize"] = str(acres)

        # simple numeric keys ----------------------------------------
        facts["yearBuilt"] = jnum(r'"yearBuilt"\s*:\s*([0-9]{4})')
        facts["beds"]      = jnum(r'"beds"\s*:\s*([0-9]+)')
        facts["baths"]     = jnum(r'"baths"\s*:\s*([0-9.]+)', as_float=True)

    # 2️⃣  HTML fall-backs (only if still blank) ----------------------
    if not facts["livingArea"]:
        m = re.search(r'([\d,]+)\s+square\s+foot', src, re.I)
        if m:
            facts["livingArea"] = m.group(1).replace(",", "")

    if not facts["lotSize"]:
        m = re.search(r'([0-9.]+)\s*acre(?:s)?\s+lot', src, re.I)
        if m:
            facts["lotSize"] = m.group(1)

    if not facts["yearBuilt"]:
        m = re.search(r'Year Built[^0-9]*([0-9]{4})', src, re.I)
        if m:
            facts["yearBuilt"] = m.group(1)

    if not facts["beds"]:
        m = re.search(r'(\d+)\s+bed(?:room)?s?', src, re.I)
        if m:
            facts["beds"] = m.group(1)

    if not facts["baths"]:
        m = re.search(r'(\d+(?:\.\d)?)\s+bath', src, re.I)
        if m:
            facts["baths"] = m.group(1)

    return facts


# ─────────────────────── scrape one address ───────────────────────
def scrape_one(driver, address: str) -> dict:
    w = WebDriverWait(driver, WAIT_SECS)

    # Redfin home → search
    driver.get("https://www.redfin.com")
    handle_cookie_banner(driver)
    box = w.until(EC.presence_of_element_located((By.ID, "search-box-input")))
    box.clear(); box.send_keys(address); box.send_keys(Keys.ENTER)
    w.until(EC.presence_of_element_located((By.TAG_NAME, "body"))); time.sleep(3)

    # price (widget → regex)
    price = _visible_price(driver) or _regex_price(driver.page_source)

    # ambiguous results → “second-ENTER”
    if not price:
        try:
            cur = driver.current_url
            sb  = w.until(EC.presence_of_element_located((By.ID, "search-box-input")))
            sb.send_keys(Keys.END); sb.send_keys(Keys.ENTER)
            WebDriverWait(driver, 10).until(EC.url_changes(cur)); time.sleep(3)
            price = _visible_price(driver, 5) or _regex_price(driver.page_source)
        except TimeoutException:
            pass

    # final fallback → include=sold
    if not price:
        sold = driver.current_url + (",include=sold" if "/filter/" in driver.current_url
                                     else "/filter/include=sold")
        driver.get(sold); time.sleep(4)
        price = _visible_price(driver, 5) or _regex_price(driver.page_source)

    price = _strip_money(price)

    # other facts
    facts = parse_home_facts(driver.page_source)

    return {
        "address":    address,
        "price":      price,
        "lotSize":    facts["lotSize"],
        "yearBuilt":  facts["yearBuilt"],
        "livingArea": facts["livingArea"],
        "bedrooms":   facts["beds"],
        "bathrooms":  facts["baths"]
    }


# ───────────────────────── runner ──────────────────────────
def main():
    IN_FILE, OUT_FILE = "testing.csv", "house_details_redfin.csv"

    opts = Options()
    # opts.add_argument("--headless=new")   # uncomment for headless mode
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(options=opts); driver.maximize_window()

    try:
        with open(IN_FILE, newline='', encoding='utf-8') as fin, \
             open(OUT_FILE, 'w', newline='', encoding='utf-8') as fout:

            rdr, wtr = csv.reader(fin), csv.writer(fout)
            wtr.writerow(["address", "price", "lotSize(acres)",
                          "yearBuilt", "livingArea(sqft)",
                          "bedrooms", "bathrooms"])

            rows = list(rdr)
            if rows and rows[0][0].strip().lower() == "address":
                rows = rows[1:]

            for row in rows:
                if not row: continue
                addr = row[0].strip()
                print(f"\n→ Scraping {addr} …")
                data = scrape_one(driver, addr)
                print("   ", data)             # live terminal output

                wtr.writerow([data["address"], data["price"], data["lotSize"],
                              data["yearBuilt"], data["livingArea"],
                              data["bedrooms"], data["bathrooms"]])

                time.sleep(random.uniform(*PAUSE_RANGE))
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
