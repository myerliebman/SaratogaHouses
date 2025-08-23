#!/usr/bin/env python3
"""
redfin_scraper_plus.py  •  2025-08-07
Outputs:
    address, price, lotSize, yearBuilt, livingArea, bedrooms, bathrooms
to house_details_redfin.csv
"""

import csv, html, random, re, time, os
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

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

def _parse_extras(src):
    """Return dict with lotSize(acres), yearBuilt, livingArea(sqft), beds, baths."""
    d = {"lotSize": None, "yearBuilt": None, "livingArea": None,
         "bedrooms": None, "bathrooms": None}

    # JSON: livingArea (sqFt)
    m = re.search(r'"sqFt"\s*:\s*([0-9,]+)', src)
    if m:
        value = m.group(1).replace(',', '')
        d["livingArea"] = int(value)
    else:
        m = re.search(r'"sqFt"\s*:\s*{[^}]*"value"\s*:\s*([0-9,]+)', src)
        if m:
            value = m.group(1).replace(',', '')
            d["livingArea"] = int(value)

    # JSON: beds/baths
    m = re.search(r'"beds"\s*:\s*([0-9]+)', src); d["bedrooms"]  = int(m.group(1)) if m else None
    m = re.search(r'"baths"\s*:\s*([0-9\.]+)', src); d["bathrooms"] = float(m.group(1)) if m else None

    # JSON: year built
    m = re.search(r'"yearBuilt"\s*:\s*([0-9]{4})', src)
    if m:
        d["yearBuilt"] = int(m.group(1))
    else:
        m = re.search(r'"builtYear"\s*:\s*([0-9]{4})', src, re.IGNORECASE)
        if m:
            d["yearBuilt"] = int(m.group(1))
        else:
            m = re.search(r'(?:Built in|Year Built|Year):\s*([0-9]{4})', src, re.IGNORECASE)
            if m:
                d["yearBuilt"] = int(m.group(1))

    # JSON: lotSize
    m = re.search(r'"lotSize[A-Za-z]*"\s*:\s*([0-9,\.]+)', src)
    if m:
        value = m.group(1).replace(',', '')
        d["lotSize"] = float(value)
    else:
        m = re.search(r'([0-9\.]+)\s*(acres?|sq ft|sqft|sq\. ft|sq ft|sq\. ft)\s*(lot|Lot Size)', src, re.IGNORECASE)
        if m:
            d["lotSize"] = float(m.group(1))

    # Fallback for livingArea
    if d["livingArea"] is None:
        m = re.search(r'([0-9,]+)\s+square foot', src)
        if m:
            d["livingArea"] = int(m.group(1).replace(",", ""))

    # Fallback for beds/baths
    if d["bedrooms"] is None or d["bathrooms"] is None:
        m = re.search(r'with ([0-9]+) bedrooms? and ([0-9\.]+) bathrooms?', src)
        if m:
            if d["bedrooms"] is None:
                d["bedrooms"] = int(m.group(1))
            if d["bathrooms"] is None:
                d["bathrooms"] = float(m.group(2))

    return d

def _digits(txt):
    return re.sub(r"[^\d.]", "", txt) if txt else ""

# ───────────────────── core scrape routine ─────────────────────
def scrape(driver, address):
    wait = WebDriverWait(driver, 15)

    driver.get("https://www.redfin.com")
    handle_cookie_banner(driver)
    box = wait.until(EC.presence_of_element_located((By.ID, "search-box-input")))
    box.clear(); box.send_keys(address); box.send_keys(Keys.ENTER)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body"))); time.sleep(3)

    price_pair = _visible_price(driver) or _regex_price(driver.page_source)

    if not price_pair:
        try:
            cur = driver.current_url
            box2 = wait.until(EC.presence_of_element_located((By.ID, "search-box-input")))
            driver.execute_script("arguments[0].focus();", box2)
            box2.send_keys(Keys.END); box2.send_keys(Keys.ENTER)
            WebDriverWait(driver, 10).until(EC.url_changes(cur)); time.sleep(3)
            price_pair = _visible_price(driver, 5) or _regex_price(driver.page_source)
        except TimeoutException:
            pass

    if not price_pair:
        cur = driver.current_url
        sold_url = cur + (",include=sold" if "/filter/" in cur else "/filter/include=sold")
        driver.get(sold_url); time.sleep(5)
        price_pair = _visible_price(driver, 5) or _regex_price(driver.page_source)

    price_clean = _digits(price_pair[1]) if price_pair else ""

    extras = _parse_extras(driver.page_source)
    
    # Save final page HTML for debugging
    with open("html.txt", "w", encoding="utf-8") as f:
        f.write(driver.page_source)
    
    return {
        "price": price_clean,
        "lotSize": extras["lotSize"] or "",
        "yearBuilt": extras["yearBuilt"] or "",
        "livingArea": extras["livingArea"] or "",
        "bedrooms": extras["bedrooms"] or "",
        "bathrooms": extras["bathrooms"] or "",
    }

# ───────────────────── runner ────────────────────────────
def main():
    IN_CSV, OUT_CSV = "addresses.csv", "house_details_redfin.csv"

    opts = Options()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(options=opts)
    driver.maximize_window()

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
                    if not row: continue
                    addr = row[0].strip()
                    print("\n──── Scraping:", addr)
                    data = scrape(driver, addr)
                    wtr.writerow([addr,
                                  data["price"], data["lotSize"], data["yearBuilt"],
                                  data["livingArea"], data["bedrooms"], data["bathrooms"]])
                    print("   →", data)
                    time.sleep(random.uniform(4, 8))

    finally:
        driver.quit()

if __name__ == "__main__":
    main()