"""Apply explicit CRM DNC/private-reminder pause without touching prize messages.

Only claimed daily-quiz marketing reminders enter this context. Direct voucher,
admin, transactional and interactive sends bypass it. Existing pacing, retries,
dedupe keys, public-channel workers and result workers are not replaced.
"""
from contextvars import ContextVar
from datetime import datetime, date, time as clock, timezone
from zoneinfo import ZoneInfo
import time

_context = ContextVar('betroxy_crm_marketing_context', default=None)


def is_marketing(action, key):
    return action == 'quiz_rewards' and str(key).startswith('daily_quiz_open:')


def cutoff_for(key):
    target = date.fromisoformat(str(key).split(':',1)[1])
    return datetime.combine(target,clock(20,30),tzinfo=ZoneInfo('Asia/Kolkata')).astimezone(timezone.utc)


def skipped(reason):
    return {'sent':False,'status':reason,'retried':0,'permanent':False,'rate_limited':False}


def install_guard(safe, crm, sleep=time.sleep, now=lambda:datetime.now(timezone.utc)):
    if getattr(safe,'_betroxy_crm_guard_installed',False):
        return
    original_claim = safe.send_claimed_result
    original_raw = safe._original_tg_send
    if not callable(original_raw):
        raise RuntimeError('Original safe-delivery transport is not installed')

    def guarded_raw(*args,**kwargs):
        uid = _context.get()
        if uid is not None:
            try:
                denied = crm.marketing_blocked(uid)
            except Exception:
                denied = True  # Cannot establish marketing permission: do not send.
            if denied:
                return False,{'ok':False,'error_code':0,'description':'CRM do-not-contact policy; no outbound message sent'}
        return original_raw(*args,**kwargs)

    def guarded_claim(user_id, action, key, *args, **kwargs):
        if not is_marketing(action,key):
            return original_claim(user_id,action,key,*args,**kwargs)
        try:
            cutoff = cutoff_for(key)
            # Wait BEFORE claiming a delivery. A pause does not consume users,
            # mark messages sent or leave a stale claimed job behind.
            while crm.reminders_paused():
                if now() >= cutoff:
                    return skipped('crm_pause_cutoff')
                sleep(10)
            if now() >= cutoff:
                return skipped('crm_pause_cutoff')
            if crm.marketing_blocked(user_id):
                return skipped('crm_dnc')
        except Exception:
            return skipped('crm_permission_check_failed')
        token = _context.set(int(user_id))
        try:
            # The raw-send check also runs after the existing pacing wait, so a
            # DNC applied while this reminder is waiting still takes effect.
            return original_claim(user_id,action,key,*args,**kwargs)
        finally:
            _context.reset(token)

    safe._original_tg_send = guarded_raw
    safe.send_claimed_result = guarded_claim
    owner = getattr(safe,'_installed_v83',None)
    if owner is not None:
        owner._safe_delivery_send_claimed_result = guarded_claim
    safe._betroxy_crm_guard_installed = True
