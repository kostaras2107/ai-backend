import json
import time


CACHE_FILE = "city_ids.json"


def load_cache():
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except:
        return {}


def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f)


def scrape_city_id(city_name):
    from selenium import webdriver
    from selenium.webdriver.edge.service import Service
    from selenium.webdriver.edge.options import Options
    from selenium.webdriver.common.by import By
    import time
    
    options = Options()
    service = Service("msedgedriver.exe")
    driver = webdriver.Edge(service=service, options=options)

    driver.get("https://www.agoda.com/en-gb/")
    time.sleep(3)

    search_box = driver.find_element(By.ID, "textInput")
    search_box.send_keys(city_name)

    time.sleep(3)

    first = driver.find_element(By.CSS_SELECTOR, "[data-selenium='autosuggest-item']")
    first.click()

    time.sleep(3)

    url = driver.current_url
    driver.quit()

    if "city=" in url:
        return url.split("city=")[1].split("&")[0]

    return None


def get_city_id(city_name):
    import os

    if os.environ.get("RENDER"):
        return None
    city_name = city_name.lower()

    cache = load_cache()

    if city_name in cache:
        return cache[city_name]

    city_id = scrape_city_id(city_name)

    if city_id:
        cache[city_name] = city_id
        save_cache(cache)

    return city_id