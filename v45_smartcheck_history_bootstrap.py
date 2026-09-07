import bot
import v40_apify_only_bootstrap as v40
import v43_latest_reports_bootstrap as v43
import v44_reporting_menu_fix_bootstrap as v44

_original_begin = v40._begin_apify_run
_original_finish = v40._finish_apify_run


def _ensure_smartcheck_history():
    """Create persistent run history and seed it from the latest stored result once.

    V40 historically reset verifier_control.completed_at during every process
    start. The run-history table keeps the actual Smart Check completion marker
    independent of deploy/restart time.
    """
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS smart_check_runs (
                    id BIGSERIAL PRIMARY KEY,
                    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ,
                    status TEXT NOT NULL DEFAULT 'running',
                    summary TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("SELECT COUNT(*) AS c FROM smart_check_runs")
            count = int((cur.fetchone() or {}).get('c') or 0)
            if count == 0:
                cur.execute(
                    """
                    SELECT MAX(auto_checked_at) AS t
                    FROM campaign_verification
                    WHERE auto_checked_at IS NOT NULL
                    """
                )
                seeded = (cur.fetchone() or {}).get('t')
                if seeded:
                    cur.execute(
                        """
                        INSERT INTO smart_check_runs
                            (started_at, completed_at, status, summary)
                        VALUES (%s, %s, 'completed', %s)
                        """,
                        (
                            seeded,
                            seeded,
                            'Latest stored verification result before persistent run-history tracking',
                        ),
                    )

            # Repair the legacy control row after V40's import-time reset so
            # Apify Status never mistakes a deployment time for a check time.
            cur.execute(
                """
                SELECT completed_at, summary
                FROM smart_check_runs
                WHERE completed_at IS NOT NULL
                ORDER BY completed_at DESC, id DESC
                LIMIT 1
                """
            )
            latest = cur.fetchone()
            if latest:
                cur.execute(
                    """
                    UPDATE verifier_control
                    SET status='idle',
                        completed_at=%s,
                        result_summary=%s
                    WHERE id=1
                    """,
                    (latest.get('completed_at'), latest.get('summary')),
                )
        conn.commit()


def _latest_run_control():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT started_at AS requested_at,
                       started_at AS claimed_at,
                       completed_at,
                       status,
                       summary AS result_summary
                FROM smart_check_runs
                WHERE completed_at IS NOT NULL
                ORDER BY completed_at DESC, id DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
    if row:
        return row
    return {
        'requested_at': None,
        'claimed_at': None,
        'completed_at': None,
        'status': 'idle',
        'result_summary': 'No completed Smart Check yet',
    }


def _tracked_begin():
    ok = _original_begin()
    if not ok:
        return False
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO smart_check_runs (started_at, status, summary)
                    VALUES (NOW(), 'running', 'Manual Apify-only Smart Check running')
                    """
                )
            conn.commit()
    except Exception:
        # Cost guard/run itself must not fail merely because audit history failed.
        bot.logger.exception('SMARTCHECK_HISTORY_BEGIN_FAILED')
    return True


def _tracked_finish(summary, warning=False):
    _original_finish(summary, warning=warning)
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE smart_check_runs
                    SET completed_at=NOW(),
                        status=%s,
                        summary=%s
                    WHERE id=(
                        SELECT id
                        FROM smart_check_runs
                        WHERE status='running'
                        ORDER BY started_at DESC, id DESC
                        LIMIT 1
                    )
                    """,
                    ('completed_with_warning' if warning else 'completed', str(summary)[:3900]),
                )
            conn.commit()
    except Exception:
        bot.logger.exception('SMARTCHECK_HISTORY_FINISH_FAILED')


_ensure_smartcheck_history()
v40._begin_apify_run = _tracked_begin
v40._finish_apify_run = _tracked_finish
v43.latest_verifier_control = _latest_run_control

# Rewrite V43's sanitized latest-status snapshot using the restored persistent
# check timestamp. Snapshot contains no tokens, passwords, cookies, IPs or IDs.
v43._write_sanitized_latest_snapshot()

bot.logger.warning(
    'V45_SMARTCHECK_HISTORY_ACTIVE persistent_completion_time=on deploy_time_not_check_time=on'
)

if __name__ == '__main__':
    bot.main()
