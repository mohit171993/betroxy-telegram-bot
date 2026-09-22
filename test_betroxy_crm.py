"""Offline regression suite: real relational fixtures + fake Telegram transport.

Does not import production.py, access Railway, send messages, or issue vouchers.
The SQLite adapter changes placeholders and CREATE VIEW syntax only; the tested
projection/mutation queries are the ones used by the PostgreSQL implementation.
"""
import asyncio
import csv
from contextlib import contextmanager
import io
import json
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, AsyncMock, patch

from betroxy_crm import CRM, QUEUES, TEST_USERNAME, VERSION, followup_time, own_contact, normalize_phone, lead_card, may_whatsapp
from betroxy_admin_ops import AdminUI, PENDING, VERIFY_PENDING, plain_admin_start, matches_prompt
from betroxy_crm_delivery_guard import install_guard, is_marketing
import betroxy_admin_ops_guard as guard

@contextmanager
def db_sqlite(path):
    con = sqlite3.connect(path)
    try:
        with con:
            yield con
    finally:
        con.close()


OWNER = 999
TEST = 1001


class Cursor:
    def __init__(self, con): self.cur = con.cursor()
    def __enter__(self): return self
    def __exit__(self,*a): self.cur.close()
    def execute(self, sql, args=()):
        sql = sql.replace('%s','?').replace('CREATE OR REPLACE VIEW','CREATE VIEW IF NOT EXISTS')
        self.cur.execute(sql,tuple(x.isoformat(sep=" ") if isinstance(x,datetime) else x for x in args))
    def fetchone(self):
        r=self.cur.fetchone(); return dict(r) if r is not None else None
    def fetchall(self): return [dict(r) for r in self.cur.fetchall()]
    def fetchmany(self,n): return [dict(r) for r in self.cur.fetchmany(n)]


class Connection:
    def __init__(self, path):
        self.con=sqlite3.connect(path,check_same_thread=False)
        self.con.row_factory=sqlite3.Row
    def __enter__(self): return self
    def __exit__(self,kind,*a):
        self.con.rollback() if kind else self.con.commit()
        self.con.close()
    def cursor(self): return Cursor(self.con)
    def commit(self): self.con.commit()


LEGACY_SCHEMA = '''
CREATE TABLE intelligence_leads(telegram_user_id BIGINT PRIMARY KEY,telegram_username TEXT,first_name TEXT,last_name TEXT,source TEXT,lifecycle_stage TEXT,opt_out BOOLEAN DEFAULT FALSE,first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,last_event TEXT,reachable_bot BOOLEAN DEFAULT FALSE);
CREATE TABLE referrals(telegram_user_id BIGINT PRIMARY KEY,telegram_username TEXT,first_name TEXT,last_name TEXT,joined_at TEXT,start_payload TEXT);
CREATE TABLE telegram_business_enquiries(id INTEGER PRIMARY KEY,customer_user_id BIGINT,customer_username TEXT,customer_first_name TEXT,customer_last_name TEXT,first_message_at TEXT,last_message_at TEXT);
CREATE TABLE user_contact_profiles(telegram_user_id BIGINT PRIMARY KEY,mobile_number TEXT,mobile_removed_at TEXT,updated_at TEXT,mobile_source TEXT,mobile_consent_at TEXT);
CREATE TABLE v110_mobile_verifications(telegram_user_id BIGINT PRIMARY KEY,mobile_number TEXT,verified_via TEXT,verified_at TEXT,updated_at TEXT);
CREATE TABLE v110_lead_consents(telegram_user_id BIGINT PRIMARY KEY,marketing_calls BOOLEAN,whatsapp_updates BOOLEAN,consented_at TEXT);
CREATE TABLE v110_quiz_entries(telegram_user_id BIGINT,telegram_username TEXT,started_at TEXT,completed_at TEXT);
CREATE TABLE mega_quiz_entries(telegram_user_id BIGINT,telegram_username TEXT,started_at TEXT,completed_at TEXT);
CREATE TABLE reward_awards(telegram_user_id BIGINT,created_at TEXT,issued_at TEXT,delivered_at TEXT);
CREATE TABLE engagement_subscriptions(telegram_user_id BIGINT PRIMARY KEY,master_enabled BOOLEAN DEFAULT TRUE,sports_updates BOOLEAN DEFAULT TRUE,promotions BOOLEAN DEFAULT TRUE,quiz_rewards BOOLEAN DEFAULT TRUE,last_preference_action_at TEXT,updated_at TEXT);
CREATE TABLE v110_quiz_sessions(telegram_user_id BIGINT PRIMARY KEY,flow_state TEXT);
CREATE TABLE mega_quiz_sessions(telegram_user_id BIGINT PRIMARY KEY,flow_state TEXT);
'''


class DataFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.temp.name)/'test.sqlite')
        with db_sqlite(self.path) as con:
            con.executescript(LEGACY_SCHEMA)
            con.executemany('INSERT INTO referrals VALUES(?,?,?,?,?,?)',[
                (OWNER,'owner','Owner','','2026-09-01 01:00:00',''),
                (TEST,TEST_USERNAME,'Test','','2026-09-01 02:00:00','test'),
                (1002,'alice','Alice','','2026-09-02 01:00:00','campaign_a'),
                (1003,'bob','Bob','','2026-09-03 01:00:00','')])
            con.execute("INSERT INTO intelligence_leads VALUES(1002,'alice','Alice','','officialbot','engaged',FALSE,'2026-09-02 01:00:00','2026-09-04 01:00:00','clicked',TRUE)")
            con.executemany('INSERT INTO telegram_business_enquiries VALUES(?,?,?,?,?,?,?)',[
                (1,1002,'alice','Alice','','2026-09-02 02:00:00','2026-09-04 01:00:00'),
                (2,1004,'dm_only','DM','','2026-09-04 01:00:00','2026-09-04 01:00:00'),
                (3,1004,'dm_only','DM','','2026-09-04 02:00:00','2026-09-05 01:00:00')])
            con.executemany('INSERT INTO user_contact_profiles(telegram_user_id,mobile_number,mobile_removed_at,updated_at) VALUES(?,?,?,?)',[
                (1002,'+919876543210',None,'2026-09-04 01:00:00'),
                (1004,'+447700900111',None,'2026-09-04 01:00:00'),
                (1005,'+971500000001',None,'2026-09-05 01:00:00')])
            con.execute("INSERT INTO v110_mobile_verifications VALUES(1002,'+919876543210','telegram_contact','2026-09-04 01:00:00','2026-09-04 01:00:00')")
            con.execute("INSERT INTO v110_lead_consents VALUES(1002,TRUE,TRUE,'2026-09-04 01:00:00')")
            con.execute("INSERT INTO v110_quiz_entries VALUES(1006,'quiz_only','2026-09-06 01:00:00',NULL)")
            con.execute("INSERT INTO mega_quiz_entries VALUES(1007,'mega_only','2026-09-07 01:00:00',NULL)")
            con.execute("INSERT INTO reward_awards VALUES(1008,'2026-09-08 01:00:00',NULL,NULL)")
            con.executemany('INSERT INTO engagement_subscriptions(telegram_user_id,updated_at) VALUES(?,?)',[(1002,'2026-09-04 01:00:00'),(1009,'2026-09-09 01:00:00')])
        self.crm=CRM(lambda:Connection(self.path),lambda uid:uid==OWNER,OWNER)
        self.crm.setup()
    def tearDown(self): self.temp.cleanup()
    def execute(self,sql,args=()):
        with db_sqlite(self.path) as con: return con.execute(sql,args).fetchall()
    def snapshot(self):
        tables=[r[0] for r in self.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'betroxy_%'")]
        return {t:self.execute('SELECT * FROM '+t) for t in tables}


class CRMTests(DataFixture):
    def test_union_includes_every_identity_source_without_duplicates(self):
        self.assertEqual(self.crm.counts()['total'],10)
        ids=[int(r['telegram_user_id']) for r in self.crm.rows('SELECT * FROM betroxy_crm_leads_v1')]
        self.assertEqual(len(ids),len(set(ids)))
        for uid in range(1001,1010): self.assertIn(uid,ids)

    def test_setup_does_not_modify_legacy_records(self):
        before=self.snapshot(); self.crm.setup(); self.assertEqual(before,self.snapshot())

    def test_legacy_engaged_does_not_mean_staff_worked(self):
        self.assertEqual(self.crm.lead(1002)['status'],'new')

    def test_assignment_does_not_remove_new_unworked(self):
        count=self.crm.counts()['new']
        self.assertTrue(self.crm.mutate(1002,OWNER,'assign','Owner',0,'a'))
        self.assertEqual(self.crm.counts()['new'],count)
        self.assertEqual(self.crm.lead(1002)['assigned_to'],OWNER)

    def test_unassigned_contains_old_verified_and_unverified(self):
        self.assertEqual(self.crm.counts()['unassigned'],10)
        self.assertTrue(self.crm.lead(1002)['verified'])
        self.assertFalse(self.crm.lead(1004)['verified'])

    def test_mobile_is_not_telegram_id(self):
        self.assertEqual(self.crm.lead(1004)['mobile_number'],'+447700900111')
        self.assertEqual(self.crm.lead(1004)['telegram_user_id'],1004)
        self.assertEqual(self.crm.lead(1006)['mobile_number'],'')

    def test_saved_phone_is_not_automatically_verified_or_consented(self):
        r=self.crm.lead(1004)
        self.assertFalse(r['verified']); self.assertFalse(r['whatsapp_consent'])
        self.assertFalse(may_whatsapp(r))

    def test_changed_number_invalidates_current_verification(self):
        self.execute("UPDATE user_contact_profiles SET mobile_number='+919999999999' WHERE telegram_user_id=1002")
        self.assertFalse(self.crm.lead(1002)['verified'])

    def test_removed_phone_never_reappears_from_old_verification(self):
        self.execute("UPDATE user_contact_profiles SET mobile_number=NULL,mobile_removed_at=CURRENT_TIMESTAMP WHERE telegram_user_id=1002")
        r=self.crm.lead(1002); self.assertEqual(r['mobile_number'],''); self.assertFalse(r['verified'])

    def test_verified_queue_only_has_current_own_contact_proof(self):
        row,index,total=self.crm.page('verified')
        self.assertEqual(total,1); self.assertEqual(row['telegram_user_id'],1002)

    def test_page_returns_one_card_and_clamps_stale_offsets(self):
        seen=[]
        for n in range(10):
            row,index,total=self.crm.page('all',n); seen.append(row['telegram_user_id'])
            self.assertEqual(index,n); self.assertEqual(total,10)
        self.assertEqual(len(set(seen)),10)
        row,index,total=self.crm.page('all',999); self.assertEqual(index,9)
        row,index,total=self.crm.page('all',-4); self.assertEqual(index,0)

    def test_new_records_appear_without_resync(self):
        self.execute("INSERT INTO referrals VALUES(1010,'new_arrival','','','2026-09-22 01:00:00','')")
        self.assertEqual(self.crm.counts()['total'],11)
        self.assertIsNotNone(self.crm.lead(1010))

    def test_search_id_username_and_formatted_phone(self):
        for term in ['1002','@alice','+91 98765 43210']:
            row,_,n=self.crm.page('all',0,term); self.assertEqual(n,1); self.assertEqual(row['telegram_user_id'],1002)

    def test_search_wildcards_and_sql_are_literals(self):
        for term in ['%', "' OR 1=1 --",'ali_e']:
            self.assertEqual(self.crm.page('all',0,term)[2],0)
        self.assertEqual(self.crm.counts()['total'],10)

    def test_queue_is_allowlisted(self):
        with self.assertRaises(ValueError): self.crm.page('all; DROP TABLE referrals')

    def test_status_moves_user_out_of_new_without_skipping_next(self):
        before=self.crm.counts()['new']
        row,idx,_=self.crm.page('new',0)
        self.crm.mutate(row['telegram_user_id'],OWNER,'status','contacted',0,'status')
        nxt,_,total=self.crm.page('new',idx)
        self.assertNotEqual(nxt['telegram_user_id'],row['telegram_user_id'])
        self.assertEqual(total,before-1)

    def test_dnc_updates_suppression_without_deleting_prizes(self):
        before=self.execute('SELECT * FROM reward_awards')
        self.crm.mutate(1002,OWNER,'status','dnc',0,'dnc')
        r=self.crm.lead(1002); self.assertTrue(r['opted_out']); self.assertFalse(may_whatsapp(r))
        self.assertEqual(self.execute('SELECT master_enabled FROM engagement_subscriptions WHERE telegram_user_id=1002')[0][0],0)
        self.assertEqual(before,self.execute('SELECT * FROM reward_awards'))

    def test_dnc_for_dm_only_user_applies_native_optout(self):
        self.crm.mutate(1004,OWNER,'status','dnc',0,'dnc_dm')
        self.assertTrue(self.crm.lead(1004)['opted_out']); self.assertTrue(self.crm.marketing_blocked(1004))

    def test_new_status_never_restores_optout_or_whatsapp_permission(self):
        self.crm.mutate(1002,OWNER,'status','dnc',0,'d1')
        self.crm.mutate(1002,OWNER,'status','new',1,'d2')
        self.assertTrue(self.crm.lead(1002)['opted_out']); self.assertFalse(may_whatsapp(self.crm.lead(1002)))

    def test_stale_write_and_double_click_do_not_overwrite(self):
        self.assertTrue(self.crm.mutate(1002,OWNER,'note','first',0,'n1'))
        self.assertFalse(self.crm.mutate(1002,OWNER,'note','duplicate',1,'n1'))
        self.assertFalse(self.crm.mutate(1002,OWNER,'note','stale',0,'n2'))
        self.assertEqual(self.crm.lead(1002)['last_note'],'first')
        self.assertEqual(len(self.crm.history(1002)),1)

    def test_staff_followup_does_not_send_or_touch_product_tables(self):
        before=self.snapshot()
        due=datetime.now(timezone.utc)+timedelta(hours=2)
        self.crm.mutate(1002,OWNER,'followup',due,0,'fu')
        self.assertIsNotNone(self.crm.lead(1002)['next_followup_at'])
        self.assertEqual(before,self.snapshot())

    def test_past_followup_rejected(self):
        with self.assertRaises(ValueError): self.crm.mutate(1002,OWNER,'followup',datetime.now(timezone.utc)-timedelta(hours=1),0,'past')

    def test_unauthorized_write_has_no_side_effect(self):
        before=self.snapshot()
        with self.assertRaises(PermissionError): self.crm.mutate(1002,TEST,'status','converted',0,'bad')
        self.assertEqual(before,self.snapshot()); self.assertEqual(self.crm.lead(1002)['version'],0)

    def test_export_is_complete_and_formula_safe(self):
        with db_sqlite(self.path) as con:
            con.executemany('INSERT INTO referrals VALUES(?,?,?,?,?,?)',[(20000+i,'x','','','2026-09-10 01:00:00','') for i in range(1200)])
        self.crm.mutate(1002,OWNER,'note','=HYPERLINK("bad")',0,'formula')
        rows=list(csv.DictReader(io.StringIO(self.crm.export_csv().decode('utf-8-sig'))))
        self.assertEqual(len(rows),1210)
        r=next(r for r in rows if r['telegram_user_id']=='1002')
        self.assertTrue(r['last_note'].startswith("'=")); self.assertTrue(r['mobile_number'].startswith("'+"))
        self.assertIn('is_test',r); self.assertIn('verified',r)

    def test_test_identity_is_pinned_and_cannot_be_stolen_by_rename(self):
        self.assertEqual(self.crm.test_user_id(),TEST)
        self.execute("UPDATE referrals SET telegram_username='renamed_test' WHERE telegram_user_id=?",(TEST,))
        self.execute("UPDATE referrals SET telegram_username=? WHERE telegram_user_id=1003",(TEST_USERNAME,))
        self.crm.setup(); self.assertEqual(self.crm.test_user_id(),TEST)
        self.assertFalse(self.crm.lead(1003)['is_test'])

    def test_test_verification_keeps_real_phones_consents_and_prizes_intact(self):
        before=self.snapshot()
        self.crm.record_verification_test(TEST,'+971500000001')
        self.assertEqual(before,self.snapshot())
        self.assertEqual(self.execute('SELECT phone_last_four FROM betroxy_crm_verification_tests')[0][0],'0001')
        self.assertFalse(self.crm.lead(TEST)['verified'])

    def test_direct_contact_verification_preserves_exact_international_phone(self):
        before=self.execute('SELECT * FROM v110_lead_consents')
        self.crm.save_own_contact(1004,1004,'+4412345678','self:1')
        row=self.crm.lead(1004)
        self.assertEqual(row['mobile_number'],'+4412345678')
        self.assertTrue(row['verified'])
        self.assertFalse(row['calls_consent']); self.assertFalse(row['whatsapp_consent'])
        self.assertEqual(before,self.execute('SELECT * FROM v110_lead_consents'))

    def test_direct_verification_rejects_foreign_contact_atomically(self):
        before=self.snapshot()
        with self.assertRaises(ValueError): self.crm.save_own_contact(1004,1002,'+4412345678','self:2')
        self.assertEqual(before,self.snapshot())

    def test_other_users_cannot_use_test_storage(self):
        with self.assertRaises(PermissionError): self.crm.record_verification_test(1002,'+971500000001')

    def test_adtribution_excludes_owner_and_test(self):
        self.assertEqual(sum(r['leads'] for r in self.crm.attribution()),8)

    def test_private_pause_is_explicit_audited_and_defaults_off(self):
        before=self.snapshot(); self.assertFalse(self.crm.reminders_paused())
        self.crm.set_reminders_paused(True,OWNER,'pause'); self.assertTrue(self.crm.reminders_paused())
        self.crm.set_reminders_paused(False,OWNER,'pause'); self.assertTrue(self.crm.reminders_paused())
        self.crm.set_reminders_paused(False,OWNER,'resume'); self.assertFalse(self.crm.reminders_paused())
        self.assertEqual(before,self.snapshot())

    def test_card_escapes_user_text(self):
        self.crm.mutate(1002,OWNER,'note','<b>NOT HTML</b>',0,'html')
        self.assertIn('&lt;b&gt;NOT HTML&lt;/b&gt;',lead_card(self.crm.lead(1002)))


class Stop(Exception): pass
class Button:
    def __init__(self,text,**kw): self.text=text; self.__dict__.update(kw)
    def to_dict(self): return self.__dict__
class Markup:
    def __init__(self,rows,**kw): self.inline_keyboard=rows
    def to_dict(self): return {'inline_keyboard':[[b.to_dict() for b in row] for row in self.inline_keyboard]}


def fake_message(text=''):
    msg=NS(chat_id=OWNER,message_id=41,text=text,reply_to_message=None)
    msg.reply_text=AsyncMock(return_value=msg)
    msg.edit_text=AsyncMock(return_value=msg)
    msg.reply_document=AsyncMock()
    return msg


class UITests(DataFixture):
    def setUp(self):
        super().setUp()
        bot=NS(is_admin=lambda uid:uid==OWNER,logger=Mock())
        api=NS(InlineKeyboardButton=Button,InlineKeyboardMarkup=Markup,ApplicationHandlerStop=Stop,
               ForceReply=lambda **kw:None,ReplyKeyboardRemove=lambda:None,
               KeyboardButton=Button,ReplyKeyboardMarkup=Markup)
        self.ui=AdminUI(bot,self.crm,api)
        self.msg=fake_message()
        self.user=NS(id=OWNER,username='owner',first_name='Owner')
        self.update=NS(effective_user=self.user,effective_message=self.msg,effective_chat=NS(type='private'))
        self.ctx=NS(user_data={},args=[])
    def test_plain_start_only_intercepts_private_admin_without_payload(self):
        self.assertTrue(plain_admin_start(self.update,self.ctx,self.ui.bot.is_admin))
        self.ctx.args=['dailyquiz']; self.assertFalse(plain_admin_start(self.update,self.ctx,self.ui.bot.is_admin))
        self.ctx.args=[]; self.user.id=TEST; self.assertFalse(plain_admin_start(self.update,self.ctx,self.ui.bot.is_admin))
    def test_customer_and_deep_link_start_passes_through(self):
        for payload in ['dailyquiz','megaquiz','rewards','mobile','campaign_test','affiliate_claim']:
            self.ctx.args=[payload]
            asyncio.run(self.ui.admin_start(self.update,self.ctx))
        self.msg.reply_text.assert_not_called()
    def test_original_create_link_and_pixel_callbacks_preserved(self):
        buttons=[b for row in self.ui.home_menu().inline_keyboard for b in row]
        values=[b.callback_data for b in buttons]
        self.assertIn('campaign_add_single',values); self.assertIn('campaign_pixel_manager',values)
    def test_callback_lengths_are_valid_for_large_user_ids(self):
        row=self.crm.lead(1002); row['telegram_user_id']=999999999999999; row['version']=10000
        menu=self.ui.card_menu(row,'unassigned',99999,100001)
        for b in [b for row in menu.inline_keyboard for b in row]:
            self.assertLessEqual(len(getattr(b,'callback_data','').encode()),64)
    def test_typed_text_outside_crm_prompt_passes_through(self):
        self.msg.text='my current campaign text'
        asyncio.run(self.ui.input(self.update,self.ctx)); self.msg.reply_text.assert_not_called()
    def test_prompt_is_bound_to_exact_message_and_chat(self):
        p={'prompt_id':3,'chat_id':OWNER}
        self.msg.reply_to_message=NS(message_id=4); self.assertFalse(matches_prompt(self.msg,p))
        self.msg.reply_to_message.message_id=3; self.assertTrue(matches_prompt(self.msg,p))
        self.msg.chat_id=TEST; self.assertFalse(matches_prompt(self.msg,p))
    def test_normal_quiz_contact_not_consumed(self):
        self.msg.contact=NS(user_id=OWNER,phone_number='+919876543210')
        asyncio.run(self.ui.contact(self.update,self.ctx)); self.msg.reply_text.assert_not_called()
    def test_forwarded_contact_rejected_without_writes(self):
        self.ctx.user_data[VERIFY_PENDING]={'testing':True,'expires':time.monotonic()+100}
        self.msg.contact=NS(user_id=1002,phone_number='+919876543210')
        before=self.snapshot()
        with self.assertRaises(Stop): asyncio.run(self.ui.contact(self.update,self.ctx))
        self.assertEqual(before,self.snapshot())
    def test_test_contact_runs_only_isolated_test(self):
        self.user.id=TEST; self.user.username=TEST_USERNAME
        self.ctx.user_data[VERIFY_PENDING]={'testing':True,'expires':time.monotonic()+100}
        self.msg.contact=NS(user_id=TEST,phone_number='+971500000001')
        before=self.snapshot()
        with self.assertRaises(Stop): asyncio.run(self.ui.contact(self.update,self.ctx))
        self.assertEqual(before,self.snapshot())
        self.assertEqual(self.execute('SELECT COUNT(*) FROM betroxy_crm_verification_tests')[0][0],1)
    def test_test_account_has_no_admin_permission(self):
        self.user.id=TEST
        with self.assertRaises(Stop): asyncio.run(self.ui.command(self.update,self.ctx))
        self.assertEqual(self.msg.reply_text.call_args.args[0],'Admin access required.')
    def test_non_private_admin_records_are_not_exposed(self):
        self.update.effective_chat.type='group'
        with self.assertRaises(Stop): asyncio.run(self.ui.command(self.update,self.ctx))
        self.assertIn('privately',self.msg.reply_text.call_args.args[0])
    def test_standalone_verification_refuses_to_disrupt_active_quiz(self):
        self.execute("INSERT INTO v110_quiz_sessions VALUES(?,'answering')",(OWNER,))
        self.msg.text='/verify_mobile'
        with self.assertRaises(Stop): asyncio.run(self.ui.verify_command(self.update,self.ctx))
        self.assertNotIn(VERIFY_PENDING,self.ctx.user_data)
        self.assertIn('current quiz',self.msg.reply_text.call_args.args[0])
    def test_verification_cancel_clears_both_new_states_only(self):
        self.ctx.user_data.update({PENDING:{},VERIFY_PENDING:{'testing':True},'legacy_flow':'keep'})
        with self.assertRaises(Stop): asyncio.run(self.ui.cancel(self.update,self.ctx))
        self.assertEqual(self.ctx.user_data,{'legacy_flow':'keep'})


class DeliveryGuardTests(unittest.TestCase):
    def make(self,blocked=False):
        raw=Mock(return_value=(True,{'ok':True}))
        safe=NS(_original_tg_send=raw,_installed_v83=NS())
        safe.send_claimed_result=lambda uid,action,key,*a,**kw: safe._original_tg_send(uid,*a,**kw)
        crm=NS(marketing_blocked=Mock(return_value=blocked),reminders_paused=Mock(return_value=False))
        return safe,crm,raw
    def test_only_daily_quiz_marketing_enters_guard(self):
        self.assertTrue(is_marketing('quiz_rewards','daily_quiz_open:2026-09-22'))
        self.assertFalse(is_marketing('reward','daily_quiz_open:2026-09-22'))
        self.assertFalse(is_marketing('quiz_rewards','transactional_winner'))
    def test_dnc_stops_marketing_without_transport_call(self):
        safe,crm,raw=self.make(True); install_guard(safe,crm)
        self.assertFalse(safe.send_claimed_result(1002,'quiz_rewards','daily_quiz_open:2040-09-22','text')['sent'])
        raw.assert_not_called()
    def test_transactional_reward_bypasses_marketing_block(self):
        safe,crm,raw=self.make(True); install_guard(safe,crm)
        safe.send_claimed_result(1002,'reward','reward:1','VOUCHER MESSAGE')
        raw.assert_called_once(); crm.marketing_blocked.assert_not_called()
    def test_direct_interactive_transport_unchanged(self):
        safe,crm,raw=self.make(True); install_guard(safe,crm)
        safe._original_tg_send(1002,'reply',None)
        raw.assert_called_once_with(1002,'reply',None)
        crm.marketing_blocked.assert_not_called()
    def test_pause_waits_before_claim_then_preserves_original_payload(self):
        safe,crm,raw=self.make(); crm.reminders_paused.side_effect=[True,False]
        sleep=Mock(); install_guard(safe,crm,sleep=sleep)
        safe.send_claimed_result(1002,'quiz_rewards','daily_quiz_open:2040-09-22','text',['buttons'])
        sleep.assert_called_once_with(10); raw.assert_called_once_with(1002,'text',['buttons'])
    def test_paused_cutoff_does_not_mark_sent_or_call_provider(self):
        safe,crm,raw=self.make(); crm.reminders_paused.return_value=True
        install_guard(safe,crm)
        result=safe.send_claimed_result(1002,'quiz_rewards','daily_quiz_open:2000-09-22','text')
        self.assertFalse(result['sent']); raw.assert_not_called()
    def test_dnc_rechecked_after_native_pacing_wait(self):
        safe,crm,raw=self.make(); crm.marketing_blocked.side_effect=[False,True]
        install_guard(safe,crm)
        result=safe.send_claimed_result(1002,'quiz_rewards','daily_quiz_open:2040-09-22','text')
        self.assertFalse(result[0]); raw.assert_not_called()
    def test_guard_does_not_install_twice(self):
        safe,crm,raw=self.make(); install_guard(safe,crm); first=safe.send_claimed_result
        install_guard(safe,crm); self.assertIs(first,safe.send_claimed_result)


class PureTests(unittest.TestCase):
    def test_own_contact_required(self):
        self.assertIsNone(own_contact(1,None,'+919876543210'))
        self.assertIsNone(own_contact(1,2,'+919876543210'))
        self.assertEqual(own_contact(1,1,'+971 50 000 0001'),'+971500000001')
    def test_invalid_phone_not_verified(self):
        for phone in ['','123','000000000','9'*16]: self.assertIsNone(normalize_phone(phone))
    def test_followup_relative_and_dubai_timezone(self):
        now=datetime(2026,9,22,7,tzinfo=timezone.utc)
        self.assertEqual(followup_time('2h',now),now+timedelta(hours=2))
        self.assertEqual(followup_time('2026-09-22 15:00',now),datetime(2026,9,22,11,tzinfo=timezone.utc))
        self.assertIsNone(followup_time('clear',now))
    def test_guard_detects_changed_or_missing_core_files(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'core.py';p.write_bytes(b'core\n')
            with patch.object(guard,'PROTECTED',{'core.py':guard.git_blob_sha(b'core\n')}):
                self.assertEqual(guard.verify(d),1)
                p.write_bytes(b'changed\n')
                with self.assertRaises(RuntimeError):guard.verify(d)
                p.unlink()
                with self.assertRaises(RuntimeError):guard.verify(d)


if __name__=='__main__':
    unittest.main()
