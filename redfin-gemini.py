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

    # ── 2️⃣  anything still None? → scrape “Public facts” bullets ───────────
    if any(v is None for v in d.values()):
        pf_txt = _public_facts_text(driver)

        if d["lotSize"] is None:
            m = re.search(r'Lot Size\s*[:\-]?\s*([0-9\.]+)', pf_txt, re.I)
            if m: d["lotSize"] = float(m.group(1))

        if d["yearBuilt"] is None:
            m = re.search(r'Year Built\s*[:\-]?\s*([0-9]{4})', pf_txt, re.I)
            if m: d["yearBuilt"] = int(m.group(1))

        if d["livingArea"] is None:
            m = re.search(r'Sq\.?\s*Ft\.?\s*[:\-]?\s*([0-9,]+)', pf_txt, re.I)
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
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body"))); time.sleep(3)

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
    
# ───────────────────────── runner ────────────────────────────
def main():
    IN_CSV, OUT_CSV = "addresses.csv", "house_details_redfin.csv"

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
                    fo.flush(); os.fsync(fo.fileno())     #  <── instant-save
                    print("   →", data)
                    time.sleep(random.uniform(4, 8))

    finally:
        driver.quit()


if __name__ == "__main__":
    main()