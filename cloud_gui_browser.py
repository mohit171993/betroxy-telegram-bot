import os
import subprocess
import time
from pathlib import Path

PROFILE_DIR = "/data/betroxy_chrome_profile"
CHROME = "/usr/bin/google-chrome-stable"


def clear_stale_profile_locks():
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

    args = [
        CHROME,
        "--disable-dev-shm-usage",
        "--start-maximized",
        "--window-size=1365,900",
        "--autoplay-policy=no-user-gesture-required",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=9222",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--lang=en-US",
        "https://www.instagram.com/",
    ]

    env = os.environ.copy()
    env.setdefault("HOME", "/home/browser")
    env.setdefault("TZ", "Asia/Dubai")

    print("GUI_NORMAL_GOOGLE_CHROME_STARTING sandbox=enabled automation_launch=false", flush=True)
    proc = subprocess.Popen(args, env=env)
    print("GUI_NORMAL_GOOGLE_CHROME_STARTED cdp=127.0.0.1:9222 profile=/data/betroxy_chrome_profile", flush=True)

    while True:
        code = proc.poll()
        if code is not None:
            print(f"GUI_NORMAL_GOOGLE_CHROME_EXIT code={code}", flush=True)
            raise SystemExit(code)
        time.sleep(5)


if __name__ == "__main__":
    main()
