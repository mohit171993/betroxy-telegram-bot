"""Disposable local-CI PostgreSQL + real Telegram-library smoke check.

Never reads DATABASE_URL or production credentials. Refuses non-loopback hosts
and any database name other than betroxy_crm_test.
"""
import asyncio
import os
import re
import sys
from types import SimpleNamespace, ModuleType
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone
import logging


def run():
    url = os.environ.get('CRM_TEST_DATABASE_URL','')
    parsed = urlparse(url)
    if parsed.hostname not in {'127.0.0.1','localhost'} or parsed.path != '/betroxy_crm_test':
        raise RuntimeError('A disposable loopback betroxy_crm_test database is required')
    import psycopg
    from psycopg.rows import dict_row
    from telegram.ext import Application
    from betroxy_crm import CRM
    from betroxy_admin_ops import install
    from test_betroxy_crm import LEGACY_SCHEMA
    with psycopg.connect(url) as conn:
        conn.execute('DROP SCHEMA IF EXISTS bcrm_ci CASCADE')
        conn.execute('CREATE SCHEMA bcrm_ci')
    def get_db():
        return psycopg.connect(url, row_factory=dict_row, options='-c search_path=bcrm_ci -c timezone=UTC')
    # Only test-fixture timestamp column types differ between local SQLite and PG.
    sql = re.sub(r'\b([a-z_]+_at) TEXT\b',r'\1 TIMESTAMPTZ',LEGACY_SCHEMA)
    with get_db() as conn:
        for statement in sql.split(';'):
            if statement.strip(): conn.execute(statement)
        conn.execute("INSERT INTO referrals VALUES(1001,'mohit_97saxena','Test','','2026-09-01 00:00:00','test')")
        conn.execute("INSERT INTO referrals VALUES(1002,'contact_owner','Example','','2026-09-01 00:00:00','ad_sample')")
        conn.execute("INSERT INTO telegram_business_enquiries VALUES(1,1003,'dm_only','Example','','2026-09-02 00:00:00','2026-09-02 00:00:00')")
    crm = CRM(get_db,lambda uid:uid==999,999)
    assert crm.setup()['total']==3
    assert crm.lead(1003)['status']=='new'
    assert crm.mutate(1003,999,'assign','Test owner',0,'assign')
    assert crm.lead(1003)['status']=='new'
    assert crm.mutate(1003,999,'note','Postgres smoke only',1,'note')
    assert crm.mutate(1003,999,'followup',datetime.now(timezone.utc)+timedelta(hours=2),2,'followup')
    crm.save_own_contact(1002,1002,'+4412345678','contact')
    assert crm.lead(1002)['verified']
    assert not crm.lead(1002)['whatsapp_consent']
    assert crm.page('verified')[2]==1
    assert crm.page('all',0,'+44 1234 5678')[2]==1
    assert crm.mutate(1003,999,'status','dnc',3,'dnc')
    assert crm.marketing_blocked(1003)
    assert len(crm.export_csv())>100
    before = crm.lead(1001)
    crm.record_verification_test(1001,'+971500000001')
    assert crm.lead(1001)==before
    # No Telegram network connection: only build/register actual library objects.
    fake_safe = ModuleType('safe_reminder_delivery')
    fake_safe._original_tg_send = lambda *a,**kw:(True,{'ok':True})
    fake_safe.send_claimed_result = lambda *a,**kw:{'sent':True}
    sys.modules['safe_reminder_delivery'] = fake_safe
    application = Application.builder().token('123456:FAKE_CI_TOKEN_NEVER_SENT').build()
    bot = SimpleNamespace(get_db=get_db,is_admin=lambda uid:uid==999,ADMIN_ID=999,logger=logging.getLogger('ci'))
    asyncio.run(install(application,bot))
    assert len(application.handlers[-240])==7
    assert application.bot_data.get('betroxy_crm_installed_v1')
    print('BETROXY_POSTGRES_CHECK passed projection=on mutations=on consent_separation=on test_isolation=on telegram21_10_handlers=7 provider_calls=0')


if __name__=='__main__':
    run()
