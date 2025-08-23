#!/usr/bin/env python3
"""
redfin_scraper_plus.py • 2025-08-07
Outputs:
    address, price, lotSize, yearBuilt, livingArea, bedrooms, bathrooms
to house_details_redfin.csv
"""

import csv, html, os, random, re, time
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.action_chains import ActionChains


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
    m = re.search(r'"segments":\s*\[.*?"text":"[^"]*?FOR \$([0-9,]+)', src, re.DOTALL)
    if m:
        return "Sold Price", f"${m.group(1)}"
    return None

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
    m = re.search(r'"sqFt"\s*:\s*{"value"\s*:\s*([0-9,]+)', src)
    if m: d["livingArea"] = int(m.group(1).replace(",", ""))

    m = re.search(r'"lotSize"\s*:\s*{"value"\s*:\s*([0-9,\.]+)', src)
    if m: d["lotSize"] = float(m.group(1).replace(",", ""))

    m = re.search(r'"yearBuilt"\s*:\s*{"value"\s*:\s*([0-9]{4})', src)
    if m: d["yearBuilt"] = int(m.group(1))

    m = re.search(r'"beds"\s*:\s*([0-9]+)', src)
    if m: d["bedrooms"] = int(m.group(1))

    m = re.search(r'"baths"\s*:\s*([0-9\.]+)', src)
    if m: d["bathrooms"] = float(m.group(1))

    # ── 2️⃣  anything still None? → scrape “Public facts” bullets ───────────
    if any(v is None for v in d.values()):
        pf_txt = _public_facts_text(driver)

        if d["lotSize"] is None:
            m = re.search(r'Lot Size\s*[:\-]?\s*([0-9,\.]+)\s*(acres|square feet)?', pf_txt, re.I)
            if m:
                num  = float(m.group(1).replace(",", ""))
                unit = (m.group(2) or "").strip().lower()
                if unit == "square feet":
                    d["lotSize"] = round(num / 43560, 3)
                else:
                    d["lotSize"] = num

        if d["yearBuilt"] is None:
            m = re.search(r'Year Built\s*[:\-]?\s*([0-9]{4})', pf_txt, re.I)
            if m: d["yearBuilt"] = int(m.group(1))

        if d["livingArea"] is None:
            m = re.search(r'(?:Living|Home)\s*Area\s*[:\-]?\s*([0-9,]+)\s*Sq\.?\s*Ft\.?', pf_txt, re.I)
            if m: d["livingArea"] = int(m.group(1).replace(",", ""))

        if d["bedrooms"] is None:
            m = re.search(r'Beds?\s*[:\-]?\s*([0-9]+)', pf_txt, re.I)
            if m: d["bedrooms"] = int(m.group(1))

        if d["bathrooms"] is None:
            m = re.search(r'Baths?\s*[:\-]?\s*([0-9\.]+)', pf_txt, re.I)
            if m: d["bathrooms"] = float(m.group(1))

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

    # ─── FIXED: Handle "Did you mean" popup ──────────────────────────
    try:
        # After searching, a "Did you mean" popup might appear.
        # We'll wait up to 5 seconds for it.
        first_suggestion = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((
                By.CSS_SELECTOR,
                "div.item-row.item-row-show-sections.clickable"
            ))
        )
        print("   → 'Did you mean' popup found, selecting first option.")
        # Click the first suggestion to proceed to the correct page.
        driver.execute_script("arguments[0].click();", first_suggestion)
        # Wait a moment for the property page to load after the click.
        time.sleep(3)
    except TimeoutException:
        # No popup appeared, which is the normal case. Wait and continue.
        time.sleep(3) # Maintain original wait time
        pass
    # ──────────────────────────────────────────────────────────────────

    price_pair = _visible_price(driver) or _regex_price(driver.page_source)

    if not price_pair:  # secondary enter
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
        driver.get(sold_url); time.sleep(5)
        price_pair = _visible_price(driver, 5) or _regex_price(driver.page_source)

    price_clean = _digits(price_pair[1]) if price_pair else ""
    html_src = driver.page_source
    extras = _parse_extras(driver, html_src)

    return {
        "price":      price_clean,
        "lotSize":    extras["lotSize"]    or "",
        "yearBuilt":  extras["yearBuilt"]  or "",
        "livingArea": extras["livingArea"] or "",
        "bedrooms":   extras["bedrooms"]   or "",
        "bathrooms":  extras["bathrooms"]  or "",
    }


# ── helper: open the “Public facts” accordion & return the bullet-list text ──
def _public_facts_text(driver) -> str:
    """
    Scroll to ‘Property details’, expand ‘Public facts’ if needed,
    and return one big string containing all <li> bullet texts.
    """
    try:
        h2 = driver.find_element(
            By.XPATH,
            "//h2[translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')"
            "        ='property details']"
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", h2)
        time.sleep(0.3)
    except NoSuchElementException:
        return ""

    try:
        facts_container = h2.find_element(By.XPATH, "./following-sibling::div[1]")
        caret = facts_container.find_element(By.XPATH, ".//button[contains(@class,'AccordionButton')]")
        if caret.get_attribute("aria-expanded") == "false":
            driver.execute_script("arguments[0].click();", caret)
            WebDriverWait(driver, 3).until(
                lambda d: caret.get_attribute("aria-expanded") == "true"
            )
    except (NoSuchElementException, TimeoutException):
        pass

    try:
        WebDriverWait(driver, 3).until(
             EC.presence_of_element_located((
                By.XPATH, "//div[contains(@data-rf-test-id, 'public-facts')]//li"
            ))
        )
        bullets = driver.find_elements(By.XPATH, "//div[contains(@data-rf-test-id, 'public-facts')]//li")
        return "  ".join(li.text for li in bullets)
    except (NoSuchElementException, TimeoutException):
        return ""

    
# ───────────────────────── runner ────────────────────────────
def main():
    IN_CSV, OUT_CSV = "addresses2.csv", "house_details_redfin.csv"

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
                    data = scrape(driver, addr)
                    wtr.writerow([addr,
                                  data["price"], data["lotSize"], data["yearBuilt"],
                                  data["livingArea"], data["bedrooms"], data["bathrooms"]])
                    fo.flush(); os.fsync(fo.fileno())
                    print("   →", data)
                    time.sleep(random.uniform(4, 8))

    finally:
        driver.quit()


if __name__ == "__main__":
    main()