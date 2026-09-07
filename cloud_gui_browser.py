import os
import subprocess
import time
from pathlib import Path

PROFILE_DIR = "/data/betroxy_chrome_profile_v3"
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


def chrome_args():
    # Railway's container runtime does not expose the kernel features Chrome's
    # Linux sandbox needs, so --no-sandbox is required for a stable headful
    # browser here. Chrome itself is still run as an unprivileged desktop user.
    # No Playwright launch flags or webdriver automation flags are used.
    return [
        CHROME,
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--password-store=basic",
        "--start-maximized",
        "--window-size=1368,900",
        "--autoplay-policy=no-user-gesture-required",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=9222",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--lang=en-US",
        "https://www.instagram.com/",
    ]


def main():
    os.environ.setdefault("DISPLAY", ":99")
    os.environ.setdefault("HOME", "/home/browser")
    os.environ.setdefault("TZ", "Asia/Dubai")
    os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
    os.makedirs(PROFILE_DIR, exist_ok=True)

    restart_count = 0
    while True:
        clear_stale_profile_locks()
        print(
            f"GUI_GOOGLE_CHROME_STARTING restart={restart_count} "
            f"profile={PROFILE_DIR} automation_launch=false media=google_chrome manual_first=yes",
            flush=True,
        )
        proc = subprocess.Popen(chrome_args(), env=os.environ.copy())
        code = proc.wait()
        restart_count += 1
        print(f"GUI_GOOGLE_CHROME_EXIT code={code} restart={restart_count}", flush=True)
        # Keep the visible desktop usable even if Chrome exits unexpectedly.
        time.sleep(3)


if __name__ == "__main__":
    main()
