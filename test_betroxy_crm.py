"""No production credentials. PG tests run only on a disposable CI database."""
import asyncio
import importlib.util
import os
import unittest
import uuid
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,MagicMock
from betroxy_crm_store import *

NOW=datetime(2026,9,22,12,tzinfo=timezone.utc)


def fixture():
    return {
        'referrals':[{'telegram_user_id':1,'telegram_username':'one','joined_at':NOW},{'telegram_user_id':2,'telegram_username':TEST_USERNAME,'joined_at':NOW}],
        'intelligence_leads':[{'telegram_user_id':1,'telegram_username':'one','first_seen_at':NOW,'source':'officialbot','opt_out':False}],
        'telegram_business_enquiries':[{'customer_user_id':1,'customer_username':'one','first_message_at':NOW},{'customer_user_id':3,'customer_username':'three','first_message_at':NOW}],
        'user_contact_profiles':[{'telegram_user_id':1,'mobile_number':'+971501234567'}],
        'v110_mobile_verifications':[{'telegram_user_id':1,'mobile_number':'+971501234567','verified_via':'telegram_contact','verified_at':NOW}],
        'v110_lead_consents':[{'telegram_user_id':1,'marketing_calls':False,'whatsapp_updates':False}]
    }


class LogicTests(unittest.TestCase):
    def test_union_dedup_bot_dm_future(self):
        s=fixture(); s['reward_awards']=[{'telegram_user_id':4,'created_at':NOW}]
        p=merge_records(s,test_uid=2)
        self.assertEqual(set(p),{1,2,3,4}); self.assertIn('business_dm',p[1]['sources']);self.assertTrue(p[2]['is_test'])
    def test_id_not_phone(self):
        self.assertEqual(merge_records(fixture())[3]['phone'],'')
    def test_saved_phone_not_verification(self):
        s=fixture();s.pop('v110_mobile_verifications')
        self.assertFalse(merge_records(s)[1]['verified'])
    def test_changed_mobile_not_verified(self):
        s=fixture();s['v110_mobile_verifications'][0]['mobile_number']='+919111111111'
        self.assertFalse(merge_records(s)[1]['verified'])
    def test_removed_mobile_not_resurrected(self):
        s=fixture();s['user_contact_profiles'][0]['mobile_removed_at']=NOW
        self.assertFalse(merge_records(s)[1]['verified']);self.assertEqual(merge_records(s)[1]['phone'],'')
    def test_consent_never_inferred(self):
        p=merge_records(fixture())[1]
        self.assertTrue(p['verified']);self.assertFalse(p['calls']);self.assertFalse(p['whatsapp'])
    def test_assigned_new_remains_new(self):
        p=merge_records(fixture(),[{'telegram_user_id':1,'assigned_to':100}],2)
        self.assertEqual(len(select_queue(p,'new')),3);self.assertEqual(len(select_queue(p,'unassigned')),2)
    def test_existing_optout_cannot_be_overridden(self):
        s=fixture();s['intelligence_leads'][0]['opt_out']=True
        p=merge_records(s,[{'telegram_user_id':1,'status':'new','next_followup_at':NOW-timedelta(days=1)}])
        self.assertEqual(p[1]['status'],'dnc');self.assertNotIn(1,[r['uid'] for r in select_queue(p,'due',now=NOW)])
    def test_search_and_queue_validation(self):
        p=merge_records(fixture())
        self.assertEqual([r['uid'] for r in select_queue(p,'all','97150')],[1])
        self.assertEqual(select_queue(p,'all',"' OR 1=1 --"),[])
        with self.assertRaises(ValueError):select_queue(p,'DROP TABLE')
    def test_own_contact_only(self):
        self.assertTrue(own_contact(2,2,'+971501234567'))
        self.assertFalse(own_contact(2,3,'+971501234567'))
        self.assertFalse(own_contact(2,None,'+971501234567'))
        self.assertFalse(own_contact(2,2,'12'))
    def test_csv_formula_escaping(self):
        for s in ['=1+1',' +SUM(A1)','@bad','\tmalicious','+971501234567','-1']:
            self.assertTrue(csv_cell(s).startswith("'"))
        self.assertEqual(csv_cell('Normal name'),'Normal name')
    def test_followup_timezone_and_validation(self):
        d=parse_followup('2026-09-23 12:00',NOW)
        self.assertEqual(d.hour,8)
        with self.assertRaises(ValueError):parse_followup('2026-09-20 12:00',NOW)
    def test_status_dnc_not_actionable(self):
        p=merge_records(fixture(),[{'telegram_user_id':1,'status':'dnc','next_followup_at':NOW-timedelta(hours=1)}])
        self.assertNotIn(1,[r['uid'] for r in select_queue(p,'due',now=NOW)])


@unittest.skipUnless(importlib.util.find_spec('telegram'),'Telegram library unavailable in local container; CI installs it')
class UITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from betroxy_crm_ui import UI
        self.store=MagicMock(); self.store.test_uid=2; self.store.test_busy.return_value=False
        self.bot=NS(is_admin=lambda uid:uid==100)
        self.ui=UI(self.store,self.bot,NS())
    def update(self,uid=100,kind='private',data='btxcrm:export'):
        msg=NS(reply_text=AsyncMock(),reply_document=AsyncMock(),edit_text=AsyncMock(),text='abc',contact=None)
        return NS(effective_user=NS(id=uid),effective_chat=NS(type=kind),effective_message=msg,callback_query=NS(data=data,message=msg,answer=AsyncMock()))
    async def test_customer_export_denied(self):
        from telegram.ext import ApplicationHandlerStop
        u=self.update(2)
        with self.assertRaises(ApplicationHandlerStop):await self.ui.callback(u,NS(user_data={}))
        self.store.export.assert_not_called();u.effective_message.reply_document.assert_not_called()
    async def test_group_admin_export_denied(self):
        from telegram.ext import ApplicationHandlerStop
        u=self.update(kind='group')
        with self.assertRaises(ApplicationHandlerStop):await self.ui.callback(u,NS(user_data={}))
        self.store.export.assert_not_called()
    async def test_old_callback_untouched(self):
        u=self.update(data='campaign_pixel_manager')
        await self.ui.callback(u,NS(user_data={}))
        self.store.snapshot.assert_not_called();u.callback_query.answer.assert_not_called()
    async def test_regular_customer_text_not_consumed(self):
        await self.ui.input(self.update(2),NS(user_data={}))
        self.store.change.assert_not_called()
    async def test_non_test_verification_not_intercepted(self):
        u=self.update(1)
        await self.ui.verify_begin(u,NS(user_data={}))
        u.effective_message.reply_text.assert_not_called()
    async def test_tester_prompt_does_not_reset_live_state(self):
        from telegram.ext import ApplicationHandlerStop
        u=self.update(2)
        with self.assertRaises(ApplicationHandlerStop):await self.ui.verify_begin(u,NS(user_data={}))
        self.store.save_test.assert_not_called();self.store.change.assert_not_called()
    async def test_dashboard_keeps_core_tools(self):
        from betroxy_crm_ui import dashboard,card_keys
        text,k=dashboard(merge_records(fixture()))
        callbacks=[b.callback_data for r in k.inline_keyboard for b in r]
        for name in ['campaign_pixel_manager','campaign_add_single','v94_admin_home','v91_reports:url']:
            self.assertIn(name,callbacks)
        for r in k.inline_keyboard:
            for b in r:
                self.assertLessEqual(len(b.callback_data.encode()),64)
        p=merge_records(fixture())[1];p['whatsapp']=True;p['status']='dnc'
        k=card_keys(p,'all',0,3)
        self.assertFalse(any(b.url and 'wa.me' in b.url for row in k.inline_keyboard for b in row))
    async def test_registration_idempotent_and_single_app(self):
        from telegram.ext import Application
        app=Application.builder().token('123:TEST_ONLY_NOT_A_REAL_TOKEN').build()
        self.ui.register(app);before=sum(len(r) for r in app.handlers.values())
        self.ui.register(app);self.assertEqual(before,sum(len(r) for r in app.handlers.values()))
        self.assertNotIn(0,app.handlers)


DB=os.getenv('BETROXY_TEST_DATABASE_URL','')
@unittest.skipUnless(DB,'Disposable PostgreSQL URL not configured')
class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row
        from urllib.parse import urlparse
        parsed=urlparse(DB)
        if parsed.hostname not in {'localhost','127.0.0.1','postgres'} or parsed.path!='/btx_crm_test':
            raise RuntimeError('Refusing non-disposable test database')
        cls.pg=psycopg;cls.dict_row=dict_row
    def setUp(self):
        self.schema='btx_test_'+uuid.uuid4().hex
        with self.pg.connect(DB) as c:c.execute('CREATE SCHEMA '+self.schema)
        self.connect=lambda:self.pg.connect(DB,row_factory=self.dict_row,options='-csearch_path='+self.schema)
        with self.connect() as c:
            c.execute('CREATE TABLE referrals(telegram_user_id BIGINT,telegram_username TEXT,joined_at TIMESTAMPTZ)')
            c.execute('CREATE TABLE intelligence_leads(telegram_user_id BIGINT,telegram_username TEXT,source TEXT,opt_out BOOLEAN)')
            c.execute("INSERT INTO referrals VALUES(1,'one',NOW()),(2,%s,NOW()),(3,'three',NOW())",(TEST_USERNAME,))
            c.execute("INSERT INTO intelligence_leads VALUES(1,'one','officialbot',FALSE)")
        self.store=Store(self.connect,lambda uid:uid in {100,101})
        self.before=self.legacy()
        self.store.setup()
    def legacy(self):
        with self.connect() as c:
            return [c.execute('SELECT * FROM '+t+' ORDER BY telegram_user_id').fetchall() for t in ('referrals','intelligence_leads')]
    def tearDown(self):
        self.assertEqual(self.legacy(),self.before,'Legacy source data must never be modified')
        with self.pg.connect(DB) as c:c.execute('DROP SCHEMA '+self.schema+' CASCADE')
    def test_repeat_migration_preserves_manual_state(self):
        self.store.change(1,100,'note','Keep this note')
        self.store.setup();self.assertEqual(self.store.snapshot()[1]['note'],'Keep this note')
    def test_assignment_and_new_counts(self):
        self.store.change(1,100,'assign',100)
        self.assertEqual(len(select_queue(self.store.snapshot(),'new')),3)
        with self.assertRaises(ValueError):self.store.change(1,101,'assign',101)
    def test_status_and_audit(self):
        self.store.change(1,100,'status','contacted')
        self.store.change(1,100,'followup',datetime.now(timezone.utc)+timedelta(days=1))
        self.assertEqual(len(self.store.history(1)),2)
        self.store.change(1,100,'status','dnc');self.assertTrue(self.store.suppressed(1))
        self.assertIsNone(self.store.snapshot()[1]['next_followup'])
    def test_unauthorized_mutation_rejected(self):
        with self.assertRaises(PermissionError):self.store.change(1,2,'note','Forbidden')
        self.assertEqual(self.store.history(1),[])
    def test_invalid_note_rejected(self):
        with self.assertRaises(ValueError):self.store.change(1,100,'note','x'*1001)
    def test_tester_only_separate_contact(self):
        self.store.save_test(2,2,'+971501234567')
        self.assertTrue(self.store.test_state())
        with self.assertRaises(PermissionError):self.store.save_test(1,1,'+971501234567')
        with self.assertRaises(PermissionError):self.store.save_test(2,3,'+971501234567')
        self.assertFalse(self.store.snapshot()[2]['verified'])
    def test_export_no_fabricated_phone(self):
        text=self.store.export().decode('utf-8-sig')
        self.assertIn('phone,verified',text);self.assertIn(TEST_USERNAME,text)
    def test_pin_cannot_silently_change(self):
        with self.connect() as c:c.execute('UPDATE btx_crm_test_identity SET telegram_user_id=999 WHERE slot=1')
        with self.assertRaises(RuntimeError):self.store.setup()
    def test_concurrent_assignment_guard(self):
        from concurrent.futures import ThreadPoolExecutor
        def claim(actor):
            try:self.store.change(1,actor,'assign',actor);return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(claim,[100,101]))
        self.assertEqual(sum(results),1)


if __name__=='__main__':unittest.main(verbosity=2)
