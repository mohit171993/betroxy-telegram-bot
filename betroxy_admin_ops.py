"""Isolated Telegram admin handlers for BETROXY.

Installed in post_init after the original boot. Every unrelated update passes
through to the original application unchanged. No provider purchases, reminder
worker changes, user mass resets, or customer broadcasts are performed here.
"""
from __future__ import annotations

import asyncio
import io
import json
import re
import time
from datetime import datetime, timezone
from html import escape

from betroxy_crm import CRM, QUEUES, STATUSES, TEST_USERNAME, VERSION
from betroxy_crm import display_time, followup_time, lead_card, may_whatsapp, own_contact, normalize_phone

PENDING = 'betroxy_crm_pending_v1'
VIEWS = 'betroxy_crm_views_v1'
VERIFY_PENDING = 'betroxy_crm_verify_pending_v1'
TTL = 600


def private(update) -> bool:
    return str(getattr(getattr(update, 'effective_chat', None), 'type', '')) == 'private'


def plain_admin_start(update, context, authorize) -> bool:
    user = getattr(update, 'effective_user', None)
    return bool(private(update) and user and authorize(user.id) and not getattr(context, 'args', None))


def matches_prompt(message, pending) -> bool:
    reply = getattr(message, 'reply_to_message', None)
    return bool(pending and reply and int(getattr(reply, 'message_id', 0) or 0) == pending['prompt_id']
                and int(getattr(message, 'chat_id', 0) or 0) == pending['chat_id'])


def state_key(message) -> str:
    return f"{message.chat_id}:{message.message_id}"


class AdminUI:
    def __init__(self, bot, crm, tg):
        self.bot, self.crm, self.tg = bot, crm, tg
        self.Button, self.Markup = tg.InlineKeyboardButton, tg.InlineKeyboardMarkup
        self.Stop = tg.ApplicationHandlerStop

    def b(self, label, data):
        if len(data.encode('utf-8')) > 64:
            raise ValueError('Telegram callback exceeds 64 bytes')
        return self.Button(label, callback_data=data)

    def markup(self, rows):
        return self.Markup(rows)

    def home_menu(self):
        b = lambda label, data: self.b(label, 'bcrm:' + data)
        return self.markup([
            [b('🆕 NEW / UNWORKED','q:new'), b('⏰ DUE NOW','q:due')],
            [b('👥 ALL LEADS','q:all'), b('👤 UNASSIGNED','q:unassigned')],
            [b('📞 FOLLOW-UP','q:followup'), b('⭐ INTERESTED','q:interested')],
            [b('🔎 SEARCH','search'), b('📥 CSV EXPORT','export')],
            [b('📱 SAVED MOBILES','q:mobile'), b('🔐 VERIFICATION','verification')],
            [b('✅ CONVERTED','q:converted'), b('🎯 AD PERFORMANCE','attribution')],
            [b('🤖 AUTOMATION STATUS','automation'), b('📚 TEAM GUIDE','guide')],
            [self.b('➕ CREATE LINK','campaign_add_single'), self.b('🎯 PIXEL MANAGER','campaign_pixel_manager')],
            [b('🛠 ALL EXISTING BETROXY TOOLS','legacy')],
        ])

    def back(self):
        return self.markup([[self.b('⬅️ TEAM DASHBOARD', 'bcrm:home')]])

    async def ack(self, query, text=None):
        try:
            await query.answer(text=text)
        except Exception as exc:
            # An expired acknowledgement is not proof that the original code or
            # an unrelated callback is broken. CRM writes are version/dedupe gated.
            self.bot.logger.info('BETROXY_CRM_ACK_SKIPPED type=%s', type(exc).__name__)

    async def show(self, message, text, markup, edit=False):
        kwargs = dict(text=text, parse_mode='HTML', reply_markup=markup, disable_web_page_preview=True)
        if edit:
            try:
                return await message.edit_text(**kwargs)
            except Exception as exc:
                if 'message is not modified' in str(exc).lower():
                    return message
                if 'message to edit not found' not in str(exc).lower() and 'message can\'t be edited' not in str(exc).lower():
                    raise
        return await message.reply_text(**kwargs)

    async def home(self, message, edit=False):
        c = await asyncio.to_thread(self.crm.counts)
        text = (
            '📊 <b>BETROXY TEAM DASHBOARD</b>\n\n'
            f"👥 All-time: <b>{c['total']}</b> · 📱 Saved mobile: <b>{c['mobile']}</b>\n"
            f"✅ Verified: <b>{c['verified']}</b> · ⚠️ Not verified: <b>{c['unverified']}</b>\n"
            f"🆕 Unworked: <b>{c['new']}</b> · 👤 Unassigned: <b>{c['unassigned']}</b> · ⏰ Due: <b>{c['due']}</b>\n"
            f"📞 Follow-up: <b>{c['followup']}</b> · ⭐ Interested: <b>{c['interested']}</b> · ✅ Converted: <b>{c['converted']}</b>\n"
            f"🧪 Test accounts: <b>{c['test_accounts']}</b> · 🚫 DNC: <b>{c['dnc']}</b>\n\n"
            'Old and new records are included. Existing BETROXY tools remain below.'
        )
        await self.show(message, text, self.home_menu(), edit)

    async def command(self, update, context):
        user = update.effective_user
        if not user or not self.bot.is_admin(user.id):
            await update.effective_message.reply_text('Admin access required.')
            raise self.Stop
        if not private(update):
            await update.effective_message.reply_text('Open the bot privately to view admin records.')
            raise self.Stop
        context.user_data.pop(PENDING, None)
        await self.home(update.effective_message)
        raise self.Stop

    async def admin_start(self, update, context):
        if plain_admin_start(update, context, self.bot.is_admin):
            context.user_data.pop(PENDING, None)
            await self.home(update.effective_message)
            raise self.Stop
        # Crucial: /start payloads, normal customers and affiliate flows continue.

    def card_menu(self, row, queue, index, total):
        uid, version = int(row['telegram_user_id']), int(row['version'])
        b = self.b
        suffix = f'{uid}:{version}'
        rows = [
            [b('🙋 ASSIGN TO ME', f'bcrm:assign:{suffix}'), b('📝 NOTE', f'bcrm:note:{suffix}')],
            [b('⏰ FOLLOW-UP', f'bcrm:followup:{suffix}'), b('🕘 HISTORY', f'bcrm:history:{uid}:0')],
            [b('📞 CONTACTED', f'bcrm:set:{uid}:contacted:{version}'), b('⭐ INTERESTED', f'bcrm:set:{uid}:interested:{version}')],
            [b('✅ CONVERTED', f'bcrm:set:{uid}:converted:{version}'), b('📵 NO ANSWER', f'bcrm:set:{uid}:no_answer:{version}')],
            [b('🚫 DNC', f'bcrm:dnc:{suffix}'), b('↩️ NEW', f'bcrm:set:{uid}:new:{version}')],
        ]
        if row['assigned_to'] is not None:
            rows.append([b('👤 UNASSIGN', f'bcrm:unassign:{suffix}')])
        if may_whatsapp(row):
            rows.append([self.Button('💬 OPEN WHATSAPP', url='https://wa.me/' + normalize_phone(row['mobile_number'])[1:])])
        navigation = []
        if index > 0:
            navigation.append(b('◀ PREVIOUS', f'bcrm:page:{queue}:{index-1}'))
        if index + 1 < total:
            navigation.append(b('NEXT ▶', f'bcrm:page:{queue}:{index+1}'))
        if navigation:
            rows.append(navigation)
        rows.append([b('🔄 REFRESH', f'bcrm:page:{queue}:{index}'), b('⬅️ DASHBOARD','bcrm:home')])
        return self.markup(rows)

    async def queue(self, query, context, queue, index=0, search='', edit=True):
        if queue == 'search':
            previous = context.user_data.get(VIEWS, {}).get(state_key(query.message), {})
            search = search or previous.get('search', '')
            if not search:
                await self.show(query.message, 'Search context expired. Please search again.', self.back(), edit)
                return
        actual = 'all' if queue == 'search' else queue
        row, index, total = await asyncio.to_thread(self.crm.page, actual, index, search)
        title = {'new':'NEW / UNWORKED · ALL TIME','unassigned':'UNASSIGNED · ALL TIME','test':'PINNED TEST ACCOUNT'}.get(queue, queue.replace('_',' ').upper()+' · ALL TIME')
        text = '<b>' + title + f'</b>\nRecord {index+1 if row else 0} of {total}\n\n'
        if row:
            text += lead_card(row)
            markup = self.card_menu(row, queue, index, total)
        else:
            text += 'No matching records.'
            markup = self.back()
        sent = await self.show(query.message, text, markup, edit)
        views = context.user_data.setdefault(VIEWS, {})
        views[state_key(sent)] = {'queue': queue, 'index': index, 'search': search}
        while len(views) > 30:
            views.pop(next(iter(views)))

    async def refresh_context(self, query, context):
        state = context.user_data.get(VIEWS, {}).get(state_key(query.message))
        if state:
            await self.queue(query, context, state['queue'], state['index'], state.get('search',''))
        else:
            await self.home(query.message, edit=True)

    async def prompt(self, query, context, action, uid=None, version=None):
        text = {
            'search':'Reply to this message with a Telegram ID, username, saved phone, or name.',
            'note':'Reply to this message with a note (1–1000 characters). No customer message will be sent.',
            'followup':'Reply to this message with 2h, 1d, or YYYY-MM-DD HH:MM (Dubai). Send clear to remove the follow-up. This is a staff task, not an automated customer message.',
        }[action]
        sent = await query.message.reply_text(text, reply_markup=self.tg.ForceReply(selective=True))
        context.user_data[PENDING] = {'action': action, 'uid': uid, 'version': version,
                                     'prompt_id': sent.message_id, 'chat_id': sent.chat_id,
                                     'expires': time.monotonic()+TTL}

    async def input(self, update, context):
        pending = context.user_data.get(PENDING)
        msg, user = update.effective_message, update.effective_user
        if not private(update) or not user or not self.bot.is_admin(user.id) or not matches_prompt(msg, pending):
            return
        if time.monotonic() > pending['expires']:
            context.user_data.pop(PENDING, None)
            await msg.reply_text('That CRM request expired. Open /admin and start it again.')
            raise self.Stop
        raw = str(msg.text or '').strip()
        try:
            if pending['action'] == 'search':
                if not 1 <= len(raw) <= 100:
                    raise ValueError('Use 1–100 characters for search.')
                row, index, total = await asyncio.to_thread(self.crm.page, 'all', 0, raw)
                text = f'<b>SEARCH RESULTS</b>\nRecord {1 if row else 0} of {total}\n\n'
                text += lead_card(row) if row else 'No matching records.'
                markup = self.card_menu(row, 'search', index, total) if row else self.back()
                sent = await self.show(msg, text, markup)
                context.user_data.setdefault(VIEWS,{})[state_key(sent)] = {'queue':'search','index':0,'search':raw}
            else:
                value = followup_time(raw) if pending['action'] == 'followup' else raw
                done = await asyncio.to_thread(self.crm.mutate, pending['uid'], user.id, pending['action'], value,
                                               pending['version'], f'msg:{msg.chat_id}:{msg.message_id}')
                await msg.reply_text('Saved ✅' if done else 'This record changed or the action was already saved. Please reopen the lead.')
            context.user_data.pop(PENDING,None)
        except ValueError as exc:
            await msg.reply_text(str(exc) + '\nReply to the original prompt again, or /cancel.')
        raise self.Stop

    async def cancel(self, update, context):
        if not private(update):
            return
        cancelled_crm = context.user_data.pop(PENDING, None)
        cancelled_verify = context.user_data.pop(VERIFY_PENDING, None)
        if cancelled_crm or cancelled_verify:
            await update.effective_message.reply_text('This CRM/verification request was cancelled.', reply_markup=self.tg.ReplyKeyboardRemove())
            raise self.Stop

    async def verification_panel(self, query):
        text = (
            '🔐 <b>VERIFICATION</b>\n\n'
            'Saved mobile, Telegram-contact verification and marketing consent are separate records.\n'
            'A phone is marked verified only when the current stored number matches its Telegram-contact verification.\n\n'
            'IBETIN/Fantzo-style verification uses Telegram contact sharing, not an SMS OTP. No new global gate has been added to BETROXY.\n\n'
            'Optional self-service command: /verify_mobile\n'
            f'Test only: @{TEST_USERNAME} can use /verifytest. This does not reset or replace live verification.\n\n'
            'Real SMS/WhatsApp OTP is not configured in this release.'
        )
        await self.show(query.message, text, self.markup([
            [self.b('✅ VERIFIED','bcrm:q:verified'),self.b('⚠️ NOT VERIFIED','bcrm:q:unverified')],
            [self.b('🧪 TEST ACCOUNT','bcrm:q:test'),self.b('🚫 DO NOT CONTACT','bcrm:q:dnc')],
            [self.b('⬅️ DASHBOARD','bcrm:home')],
        ]), True)

    async def verify_command(self, update, context):
        if not private(update) or not update.effective_user:
            await update.effective_message.reply_text('Use this verification command in the bot’s private chat.')
            raise self.Stop
        testing = str(update.effective_message.text or '').split()[0].split('@')[0].lower() == '/verifytest'
        uid = int(update.effective_user.id)
        if testing:
            pinned = await asyncio.to_thread(self.crm.test_user_id)
            if uid != pinned or str(update.effective_user.username or '').lower() != TEST_USERNAME:
                await update.effective_message.reply_text('This test is reserved for the pinned @'+TEST_USERNAME+' account.')
                raise self.Stop
        else:
            # Do not take over an active daily/weekly quiz or registration flow.
            for table in ('v110_quiz_sessions','mega_quiz_sessions'):
                rows = await asyncio.to_thread(self.crm.rows, f'SELECT flow_state FROM {table} WHERE telegram_user_id=%s',(uid,))
                if rows and rows[0].get('flow_state') not in (None,'','complete'):
                    await update.effective_message.reply_text('Finish the current quiz/registration flow first. Its existing verification button continues to work.')
                    raise self.Stop
        context.user_data[VERIFY_PENDING] = {'testing':testing,'expires':time.monotonic()+TTL}
        text = ('🧪 TEST ONLY: share your own Telegram-linked phone. Only its last four digits and test time are recorded. Live verification, quizzes and prizes are untouched.' if testing else
                '📱 Verify the phone linked to your Telegram account. Share your own contact below. This uses Telegram verification, not SMS OTP, and does not subscribe you to calls or WhatsApp promotions.')
        markup = self.tg.ReplyKeyboardMarkup([[self.tg.KeyboardButton('📱 Verify My Mobile',request_contact=True)]],resize_keyboard=True,one_time_keyboard=True)
        await update.effective_message.reply_text(text+'\nUse /cancel to leave this step.',reply_markup=markup)
        raise self.Stop

    async def contact(self, update, context):
        pending = context.user_data.get(VERIFY_PENDING)
        if not pending or not private(update) or not update.effective_user:
            return  # Existing Daily Quiz contact handler must receive normal contacts.
        user, msg = update.effective_user, update.effective_message
        if time.monotonic() > pending['expires']:
            context.user_data.pop(VERIFY_PENDING,None)
            await msg.reply_text('Verification request expired. Start it again.',reply_markup=self.tg.ReplyKeyboardRemove())
            raise self.Stop
        contact = getattr(msg,'contact',None)
        mobile = own_contact(user.id, getattr(contact,'user_id',None),getattr(contact,'phone_number',None))
        if not mobile:
            await msg.reply_text('Please use Verify My Mobile and share your own Telegram-linked contact. Forwarded contacts and typed numbers cannot verify this step.')
            raise self.Stop
        if pending['testing']:
            if str(user.username or '').lower() != TEST_USERNAME:
                context.user_data.pop(VERIFY_PENDING,None)
                await msg.reply_text('Test identity changed; please ask the owner to review it.')
                raise self.Stop
            await asyncio.to_thread(self.crm.record_verification_test,user.id,mobile)
            text = '✅ TEST PASSED: own-contact verification. Live verification, quiz sessions, consent and prizes were not changed.'
        else:
            # A native flow started after /verify_mobile takes precedence.
            for table in ('v110_quiz_sessions','mega_quiz_sessions'):
                rows = await asyncio.to_thread(self.crm.rows,f'SELECT flow_state FROM {table} WHERE telegram_user_id=%s',(int(user.id),))
                if rows and rows[0].get('flow_state') not in (None,'','complete'):
                    context.user_data.pop(VERIFY_PENDING,None)
                    return
            # Write the exact canonical number to the existing contact/proof
            # tables in one transaction. No quiz/session/marketing changes.
            saved = await asyncio.to_thread(self.crm.save_own_contact,user.id,contact.user_id,mobile,
                                            f'verify:{msg.chat_id}:{msg.message_id}')
            if not saved:
                await msg.reply_text('Could not save verification. Please try the existing quiz verification flow.')
                raise self.Stop
            text = '✅ Mobile verified: +••••••'+mobile[-4:]+'. No marketing consent was added. Your existing BETROXY functions are unchanged.'
        context.user_data.pop(VERIFY_PENDING,None)
        await msg.reply_text(text,reply_markup=self.tg.ReplyKeyboardRemove())
        raise self.Stop

    async def callback(self, update, context):
        query, user = update.callback_query, update.effective_user
        if not query:
            return
        if not user or not self.bot.is_admin(user.id) or not private(update):
            await self.ack(query,'Private admin access required.')
            raise self.Stop
        await self.ack(query)
        parts = str(query.data or '').split(':')
        action = parts[1] if len(parts)>1 else ''
        try:
            if action == 'home':
                await self.home(query.message,True)
            elif action == 'legacy':
                # Runtime menu includes all existing category/banner/report overlays.
                rows = [list(r) for r in self.bot.admin_menu().inline_keyboard]
                rows.append([self.b('⬅️ NEW TEAM DASHBOARD','bcrm:home')])
                await self.show(query.message,'🛠 <b>EXISTING BETROXY TOOLS</b>\n\nAll original functions and their original handlers are preserved.',self.markup(rows),True)
            elif action in {'q','page'}:
                queue = parts[2]
                index = int(parts[3]) if len(parts)>3 else 0
                if queue not in QUEUES and queue != 'search':
                    raise ValueError('Unknown work queue')
                await self.queue(query,context,queue,index)
            elif action == 'search':
                await self.prompt(query,context,'search')
            elif action in {'note','followup'}:
                await self.prompt(query,context,action,int(parts[2]),int(parts[3]))
            elif action == 'dnc':
                uid, version = int(parts[2]),int(parts[3])
                await self.show(query.message,'🚫 <b>MARK DO NOT CONTACT?</b>\n\nThis records DNC and applies the existing marketing opt-out flags. It does not remove this lead, its quiz history, rewards or consent audit.',self.markup([
                    [self.b('CONFIRM DO NOT CONTACT',f'bcrm:set:{uid}:dnc:{version}')],
                    [self.b('CANCEL','bcrm:home')]]),True)
            elif action in {'assign','unassign','set'}:
                uid = int(parts[2])
                operation = 'status' if action == 'set' else action
                value = parts[3] if action == 'set' else '@'+str(user.username) if user.username else str(user.first_name or user.id)
                version = int(parts[4] if action == 'set' else parts[3])
                done = await asyncio.to_thread(self.crm.mutate,uid,user.id,operation,value,version,'cb:'+query.id)
                await self.refresh_context(query,context)
                if not done:
                    await query.message.reply_text('The record changed or this action was already applied. The latest state has been loaded.')
            elif action == 'history':
                uid, page = int(parts[2]),max(0,int(parts[3]))
                entries = await asyncio.to_thread(self.crm.history,uid,page)
                lines = [f'🕘 <b>CRM HISTORY · {uid}</b>',f'Page {page+1}','']
                for entry in entries:
                    lines += [display_time(entry['created_at'])+' · '+escape(entry['action']),
                              f"Actor: <code>{entry['actor_id']}</code> · {escape(entry['detail'][:150])}",'']
                if not entries:
                    lines.append('No recorded CRM actions on this page. Historical quiz/user data was not relabelled as staff work.')
                nav = []
                if page:
                    nav.append(self.b('◀ PREVIOUS',f'bcrm:history:{uid}:{page-1}'))
                if len(entries)==10:
                    nav.append(self.b('NEXT ▶',f'bcrm:history:{uid}:{page+1}'))
                rows = [nav] if nav else []
                rows.append([self.b('⬅️ DASHBOARD','bcrm:home')])
                await self.show(query.message,'\n'.join(lines),self.markup(rows),True)
            elif action == 'export':
                data = await asyncio.to_thread(self.crm.export_csv)
                if len(data)>45*1024*1024:
                    raise ValueError('The complete export is too large for one Telegram document. No truncated export was sent.')
                await query.message.reply_document(document=io.BytesIO(data),filename='BETROXY_all_time_leads.csv',caption='All-time CRM records. Saved phone, verification and consent are separate columns. Private admin export.')
            elif action == 'verification':
                await self.verification_panel(query)
            elif action == 'attribution':
                rows = await asyncio.to_thread(self.crm.attribution)
                lines = ['🎯 <b>LEAD SOURCE / AD TRACKING</b>','Test account and owner excluded.','']
                for row in rows:
                    payload = str(row['start_payload'] or 'No payload recorded')[:55]
                    lines.append(f"{escape(row['source'])} · <code>{escape(payload)}</code>\nLeads {row['leads']} · verified {row['verified']} · converted {row['converted']}")
                lines += ['', 'These are bot-record metrics, not a claim of ad spend or confirmed paid-ad acquisition. Native URL/pixel/referral reports remain available below.']
                await self.show(query.message,'\n'.join(lines),self.markup([
                    [self.b('📈 CAMPAIGN TRACKER','campaign_home'),self.b('🔗 URL REPORTS','v91_reports:url')],
                    [self.b('⬅️ DASHBOARD','bcrm:home')]]),True)
            elif action == 'reminders':
                if parts[2] not in {'pause','resume'}:
                    raise ValueError('Invalid reminder control')
                await asyncio.to_thread(self.crm.set_reminders_paused, parts[2]=='pause', user.id, 'cb:'+query.id)
                await self.automation(query)
            elif action == 'automation':
                await self.automation(query)
            elif action == 'guide':
                await self.show(query.message,
                    '📚 <b>TEAM GUIDE</b>\n\n'
                    '<b>New / Unworked:</b> all historical and new records without a staff outcome. Assignment alone does not remove them.\n\n'
                    '<b>Unassigned:</b> every record without an owner, across all statuses; DNC records remain visible for audit.\n\n'
                    '<b>Saved mobile ≠ verified ≠ consent.</b> Use WhatsApp only where consent is recorded and DNC is off. Never treat a Telegram ID as a mobile number.\n\n'
                    '<b>Notes / follow-up:</b> reply to the exact prompt. Follow-up times use Dubai time; these are staff tasks, not customer messages.\n\n'
                    '<b>DNC:</b> stops marketing eligibility through existing opt-out flags. Changing a CRM status never restores consent.\n\n'
                    '<b>CSV:</b> exports all records with verification and test-account labels. Keep it private.\n\n'
                    '<b>Existing tools:</b> campaign links, pixels, banners, reports, affiliate/referral tools, quizzes and rewards use their original handlers.',self.back(),True)
            else:
                raise ValueError('Unknown admin action. Reopen /admin.')
        except (ValueError, IndexError, PermissionError) as exc:
            await query.message.reply_text(str(exc)[:300])
        except Exception as exc:
            self.bot.logger.error('BETROXY_CRM_ACTION_FAILED action=%s type=%s',action,type(exc).__name__)
            await query.message.reply_text('The CRM action could not complete. No retry has been queued. Existing BETROXY tools remain accessible through /start with a fresh message.')
        raise self.Stop

    async def automation(self, query):
        # Only the new private-marketing pause gate is writable. Public posts,
        # final results and payouts never use this pause flag.
        import daily_quiz_schedule as schedule
        import daily_quiz_alerts as alerts
        since = datetime.now(timezone.utc).replace(microsecond=0)
        from datetime import timedelta
        since -= timedelta(hours=24)
        rows = await asyncio.to_thread(self.crm.rows,
            "SELECT channel,COUNT(*) AS n FROM engagement_log WHERE status='sent' AND sent_at>=%s GROUP BY channel",(since,))
        sent = ', '.join(escape(str(r['channel'] or 'unknown'))+': '+str(r['n']) for r in rows) or '0'
        paused = await asyncio.to_thread(self.crm.reminders_paused)
        text = (
            '🤖 <b>EXISTING AUTOMATION · STATUS</b>\n\n'
            f"Private quiz reminder queue: <b>{'PAUSED' if paused else 'NOT PAUSED'}</b>\n"
            f"Daily schedule configured: <b>{'ON' if schedule.SCHEDULE_ENABLED else 'OFF'}</b>\n"
            f"Public results configured: <b>{'ON' if schedule.RESULT_CHANNEL_ENABLED else 'OFF'}</b>\n"
            f"Public channel: <code>{escape(str(alerts.v110.CHANNEL_CHAT))}</code>\n"
            f"Recorded private sends, last 24h: <b>{sent}</b>\n\n"
            'Daily Quiz: 10:00–21:00 IST · result 21:05 IST.\n'
            'Sunday Mega: 10:00–21:00 IST · result 21:10 IST.\n'
            'Existing private queue: one reminder per user/day, bot preferred; eligible Business DMs retain the 7-day policy and 10-minute minimum.\n\n'
            '<b>The control below affects only the private quiz reminder queue.</b> Public posts, quiz timing, result announcements and payouts remain on their original paths. Resume keeps existing pacing and the daily cutoff; it does not start an extra campaign. Pause applies from the next unclaimed reminder; an in-flight delivery may still finish.'
        )
        await self.show(query.message,text,self.markup([
            [self.b('▶ RESUME PRIVATE REMINDERS' if paused else '⏸ PAUSE PRIVATE REMINDERS', 'bcrm:reminders:'+('resume' if paused else 'pause'))],
            [self.b('⬅️ DASHBOARD','bcrm:home')]]),True)


async def install(application, bot):
    import telegram as tg
    from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, MessageHandler, filters
    if application.bot_data.get('betroxy_crm_installed_v1'):
        return
    crm = CRM(bot.get_db, bot.is_admin, bot.ADMIN_ID)
    counts = await asyncio.to_thread(crm.setup)
    # Attach namespace-only helpers; never patch core product functions or menus.
    from types import SimpleNamespace
    api = SimpleNamespace(InlineKeyboardButton=tg.InlineKeyboardButton,InlineKeyboardMarkup=tg.InlineKeyboardMarkup,
                          ForceReply=tg.ForceReply,KeyboardButton=tg.KeyboardButton,ReplyKeyboardMarkup=tg.ReplyKeyboardMarkup,
                          ReplyKeyboardRemove=tg.ReplyKeyboardRemove,ApplicationHandlerStop=ApplicationHandlerStop)
    ui = AdminUI(bot,crm,api)
    # Validate actual Telegram markup before exposing any handler.
    ui.home_menu().to_dict()
    import safe_reminder_delivery as safe_delivery
    from betroxy_crm_delivery_guard import install_guard
    install_guard(safe_delivery, crm)
    application.add_handler(CommandHandler(['admin','crm'],ui.command),group=-240)
    application.add_handler(CommandHandler('start',ui.admin_start),group=-240)
    application.add_handler(CommandHandler('cancel',ui.cancel),group=-240)
    application.add_handler(CommandHandler(['verify_mobile','verifytest'],ui.verify_command),group=-240)
    application.add_handler(CallbackQueryHandler(ui.callback,pattern=r'^bcrm:'),group=-240)
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.BUSINESS_MESSAGE,ui.input),group=-240)
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.CONTACT & ~filters.UpdateType.BUSINESS_MESSAGE,ui.contact),group=-240)
    application.bot_data['betroxy_crm_installed_v1'] = VERSION
    bot.logger.info('BETROXY_CRM_READY release=%s total=%s verified=%s saved_mobile=%s test_accounts=%s legacy_routes=unchanged sms_otp=not_configured startup_customer_sends=0',
                    VERSION,counts['total'],counts['verified'],counts['mobile'],counts['test_accounts'])
