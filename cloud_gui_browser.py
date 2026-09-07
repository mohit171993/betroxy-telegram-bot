import os
import time
from playwright.sync_api import sync_playwright

PROFILE_DIR = "/data/betroxy_chrome_profile"


def main():
    os.environ.setdefault("DISPLAY", ":99")
    os.makedirs(PROFILE_DIR, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            viewport={"width": 1365, "height": 900},
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=9222",
            ],
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass
        print("GUI_CHROMIUM_READY profile=/data/betroxy_chrome_profile cdp=127.0.0.1:9222", flush=True)
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()
