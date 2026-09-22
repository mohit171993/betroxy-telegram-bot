"""Read-only release gate; does not import the live bot or create tables."""
import hashlib
import json
import os
from pathlib import Path

PROTECTED = {
    'bot.py':'080430ad389e86f5ff6f79414a3be73177a2201a',
    'production.py':'a053dc022fde4bc53aaa53ab1c7413537f0ea766',
    'daily_quiz_schedule.py':'5a9c1dc60469d767906ee7e29ec847d7744297a6',
    'quiz_mobile_verification_overlay.py':'8ab025615f38589f3239635570de16dc200cd669',
    'reward_code_display_fix.py':'77d6fce30c64e0251e181733d4c592c4e1c626cc',
    'reward_receipt_confirmation.py':'5c32c8e59fe831e6a101f3c87ae966decd26de0b',
    'weekly_mega_quiz.py':'7a32769aafe9169a76d8dc051312cdcb94e4f0a2',
    'safe_reminder_delivery.py':'0d7c093a3bcdec97004df0152e883c05a39a0083',
}


def check_protected(root=Path('.')):
    for path,expected in PROTECTED.items():
        data=(root/path).read_bytes()
        actual=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        if actual!=expected:
            raise RuntimeError('Protected baseline file changed: '+path)


def main():
    import psycopg
    from psycopg.rows import dict_row
    from betroxy_crm_store import Store, merge_records
    check_protected()
    def connect():
        return psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=10)
    store=Store(connect,lambda uid:False)
    sources=store.inspect()
    people=merge_records(sources,test_uid=store.test_uid)
    print('BTX_CRM_PREFLIGHT '+json.dumps({'read_only':True,'protected_files':len(PROTECTED),'unique_leads':len(people),'source_counts':{t:len(r) for t,r in sources.items()},'tester_pinned_candidate':store.test_uid,'sms_otp':False},sort_keys=True),flush=True)


if __name__=='__main__':
    main()
