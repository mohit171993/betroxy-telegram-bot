import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

PROFILE_DIR = "/data/betroxy_chrome_profile"
CHROME = "/usr/bin/google-chrome-stable"


def clear_stale_profile_locks():
    # Railway redeploys can leave Chrome singleton lock files inside the persistent
    # profile volume. On a fresh container those files are stale and prevent Chrome
    # from starting, leaving noVNC with a blank desktop.
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        path = Path(PROFILE_DIR) / name
        try:
            if path.is_symlink() or path.exists():
                path.unlink()
                print(f"GUI_CHROME_STALE_LOCK_REMOVED {name}", flush=True)
        except FileNotFoundError:
            pass
        except Exception as exc:
            print(f"GUI_CHROME_LOCK_REMOVE_WARN {name} {type(exc).__name__}: {exc}", flush=True)


def main():
    os.environ.setdefault("DISPLAY", ":99")
    os.makedirs(PROFILE_DIR, exist_ok=True)
    clear_stale_profile_locks()

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            executable_path=CHROME,
            headless=False,
            viewport={"width": 1365, "height": 900},
            timeout=60000,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
                "--autoplay-policy=no-user-gesture-required",
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=9222",
            ],
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:
            print(f"GUI_CHROME_NAV_WARN {type(exc).__name__}: {exc}", flush=True)
        print("GUI_GOOGLE_CHROME_READY profile=/data/betroxy_chrome_profile cdp=127.0.0.1:9222 media_codecs=enabled", flush=True)
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()
