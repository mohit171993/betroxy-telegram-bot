import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

PROFILE_DIR = Path(os.environ.get("CHROME_PROFILE_DIR", "/data/betroxy_chrome_profile_v3"))
COOKIE_DB = PROFILE_DIR / "Default" / "Cookies"
CHECK_INTERVAL = 5


def has_instagram_session_cookie() -> bool:
    """Check for a persisted Instagram session cookie without attaching to Chrome.

    Chrome keeps the cookie DB locked while running, so work from a copy. We never
    read or log the cookie value; presence alone is enough to know manual login has
    completed and it is safe for the checker to attach over CDP.
    """
    if not COOKIE_DB.exists():
        return False

    tmp = Path("/tmp/betroxy_cookie_probe.sqlite")
    try:
        shutil.copy2(COOKIE_DB, tmp)
        conn = sqlite3.connect(str(tmp), timeout=2)
        try:
            row = conn.execute(
                "SELECT 1 FROM cookies "
                "WHERE host_key LIKE '%instagram.com' AND name='sessionid' "
                "AND (length(value) > 0 OR length(encrypted_value) > 0) "
                "LIMIT 1"
            ).fetchone()
            return bool(row)
        finally:
            conn.close()
    except Exception:
        return False
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass


def main():
    print(
        f"LOGIN_GATE_ACTIVE profile={PROFILE_DIR} mode=manual_first "
        "checker_attach_before_login=no apify_handoff=blocked",
        flush=True,
    )

    checker = None
    last_wait_log = 0.0

    while True:
        session_present = has_instagram_session_cookie()

        if not session_present:
            if checker is not None and checker.poll() is None:
                print("LOGIN_GATE_SESSION_LOST stopping_checker", flush=True)
                checker.terminate()
                try:
                    checker.wait(timeout=10)
                except Exception:
                    checker.kill()
                checker = None

            now = time.time()
            if now - last_wait_log >= 30:
                print("LOGIN_GATE_WAITING manual_instagram_login_required", flush=True)
                last_wait_log = now
            time.sleep(CHECK_INTERVAL)
            continue

        if checker is None or checker.poll() is not None:
            print("LOGIN_GATE_SESSION_PRESENT starting_safe_checker", flush=True)
            checker = subprocess.Popen(["python", "/app/cloud_checker_gui_shared_test.py"])

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
