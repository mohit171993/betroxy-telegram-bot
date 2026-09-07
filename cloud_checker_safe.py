import os
import time

if not os.getenv("IG_SESSIONID", "").strip():
    print("CLOUD_CHECKER_LOGIN_REQUIRED", flush=True)
    print("Cloud checker is deployed but idle until IG_SESSIONID is configured.", flush=True)
    while True:
        time.sleep(60)

import cloud_checker_worker
cloud_checker_worker.main()
