#!/usr/bin/env python3
"""
Redfin price scraper – with “second-enter” fallback
"""

import csv, time, random, re, html
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
            EC.element_to_be_clickable(
                (By.XPATH, "//*[contains(text(),'Accept all cookies')]"
                           " | //button[contains(text(),'Accept')]"))
        )
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(1.5)
    except TimeoutException:
        pass

def visible_price(driver, wait_secs=7):
    try:
        el = WebDriverWait(driver, wait_secs).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR,
                 "[data-testid='avm-price'] .value, .statsValue.price"))
        )
        txt = el.text.strip()
        if txt:
            parent_attr = (el.find_element(By.XPATH, "..")
                             .get_attribute("data-testid") or "")
            return ("Redfin Estimate" if "avm-price" in parent_attr
                    else "List Price"), txt
    except TimeoutException:
        return None
    return None

def regex_price(page_src):
    # Redfin Estimate from avmText
    m = re.search(r'"avmText":"([^"]*\$[0-9,]+)', page_src)
    if m:
        est = re.search(r'\$[0-9,]+', html.unescape(m.group(1))).group(0)
        return "Redfin Estimate", est
    # Sold banner
    m = re.search(r'"segments":\s*\[.*?"text":"[^"]*?FOR \$([0-9,]+)',
                  page_src, re.DOTALL)
    if m:
        return "Sold Price", m.group(1)
    return None

# ─────────────────────── core scraper ───────────────────────
def get_redfin_price(driver, address):
    wait = WebDriverWait(driver, 15)

    # Navigate & initial search
    driver.get("https://www.redfin.com")
    handle_cookie_banner(driver)
    box = wait.until(EC.presence_of_element_located((By.ID, "search-box-input")))
    box.clear(); box.send_keys(address); box.send_keys(Keys.ENTER)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(3)

    # 1) visible element?
    vp = visible_price(driver)
    if vp:
        return f"{vp[0]}: {vp[1]}"

    # 2) SECOND-ENTER fallback when search returned multiple cards
    try:
        current = driver.current_url
        box2 = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.ID, "search-box-input")))
        box2.click(); box2.send_keys(Keys.ENTER)
        WebDriverWait(driver, 10).until(EC.url_changes(current))
        time.sleep(3)

        vp = visible_price(driver, 5)
        if vp:
            return f"{vp[0]}: {vp[1]}"

        rp = regex_price(driver.page_source)
        if rp:
            return f"{rp[0]}: ${rp[1]}"
    except TimeoutException:
        pass

    # 3) regex on current page
    rp = regex_price(driver.page_source)
    if rp:
        return f"{rp[0]}: ${rp[1]}"

    # 4) force “Sold” filter via URL
    cur = driver.current_url
    sold_url = (cur + ",include=sold" if "/filter/" in cur
                else cur + "/filter/include=sold")
    driver.get(sold_url); time.sleep(5)

    vp = visible_price(driver, 5)
    if vp:
        return f"{vp[0]}: {vp[1]}"
    rp = regex_price(driver.page_source)
    if rp:
        return f"{rp[0]}: ${rp[1]}"

    return "Could not find a price."

# ───────────────────────── runner ───────────────────────────
def main():
    INFILE, OUTFILE = "testing.csv", "house_prices_redfin.csv"

    opts = Options()
    # opts.add_argument("--headless"); opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")
    driver = webdriver.Chrome(options=opts); driver.maximize_window()

    try:
        with open(INFILE, newline='', encoding='utf-8') as fi, \
             open(OUTFILE, 'w', newline='', encoding='utf-8') as fo:
            rdr, wtr = csv.reader(fi), csv.writer(fo)
            wtr.writerow(["Address", "Redfin Price/Estimate"])
            rows = list(rdr)
            if rows and rows[0][0].lower() == "address":
                rows = rows[1:]

            for r in rows:
                if not r: continue
                addr = r[0]
                print("\n" + "-"*55 + f"\nScraping: {addr}")
                price = get_redfin_price(driver, addr)
                wtr.writerow([addr, price])
                print(f"Result → {price}\n" + "-"*55)
                time.sleep(random.uniform(4, 8))
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
