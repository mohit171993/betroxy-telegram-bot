"""Additive, private, existing-admin-only CRM; tester-only verification pilot."""
from __future__ import annotations
import asyncio
import html
import io
import logging
import time
from datetime import timezone
from zoneinfo import ZoneInfo
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as K, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler, ApplicationHandlerStop, filters
from betroxy_crm_store import Store, STATUSES, QUEUES, TEST_USERNAME, parse_followup, select_queue

log = logging.getLogger(__name__)
DUBAI = ZoneInfo("Asia/Dubai")
LABELS = {"new": "NEW / UNWORKED", "unassigned": "UNASSIGNED", "all": "ALL LEADS", "mobile": "SAVED MOBILES", "due": "DUE NOW", "followup": "FOLLOW-UP", "interested": "INTERESTED", "converted": "CONVERTED", "not_interested": "NOT INTERESTED", "dnc": "DO NOT CONTACT", "test": "TEST ACCOUNT"}
GUIDE = ("📚 <b>BETROXY TEAM GUIDE</b>\n\n"
         "New/Unworked includes all-time leads with no recorded CRM outcome. Assigning an owner does not remove a New lead. Unassigned means no owner, regardless of outcome.\n\n"
         "Use Previous/Next to browse one record at a time. Status, notes, assignment and follow-up changes have an audit history. Follow-up times are Dubai time.\n\n"
         "Saved mobile, verified mobile and marketing consent are separate. A Telegram ID is never a phone number. WhatsApp opens only with recorded WhatsApp consent. Respect DNC and existing STOP.\n\n"
         "All original BETROXY tools remain under Existing BETROXY Tools. CRM does not purchase prizes, change quizzes, or send extra campaigns.\n\n"
         "The new verification pilot is only for @mohit_97saxena. It stores a separate test result; it does not overwrite the live profile, alter consent or unlock paid features.")


def e(value):
    return html.escape(str(value if value is not None else "—"))


def when(value):
    if not value:
        return "—"
    return value.astimezone(DUBAI).strftime("%d %b %Y %H:%M Dubai")


def cb(text, data):
    return B(text, callback_data="btxcrm:" + data)


def dashboard(people):
    total = len(people)
    verified = sum(bool(p["verified"]) for p in people.values())
    new = select_queue(people, "new")
    text = ("📊 <b>BETROXY TEAM DASHBOARD</b>\n\n"
            f"👥 All-time leads: <b>{total}</b> · 📱 Saved mobile: <b>{sum(bool(p['phone']) for p in people.values())}</b>\n"
            f"✅ Verified: <b>{verified}</b> · ⚠️ Not verified: <b>{total-verified}</b>\n"
            f"🆕 New/Unworked: <b>{len(new)}</b> · 👤 Unassigned: <b>{len(select_queue(people,'unassigned'))}</b>\n"
            f"⏰ Due: <b>{len(select_queue(people,'due'))}</b> · 📞 Follow-up: <b>{len(select_queue(people,'followup'))}</b>\n"
            f"⭐ Interested: <b>{len(select_queue(people,'interested'))}</b> · ✅ Converted: <b>{len(select_queue(people,'converted'))}</b>\n"
            f"🚫 DNC: <b>{len(select_queue(people,'dnc'))}</b>\n\n"
            f"New already assigned: <b>{sum(p['assigned_to'] is not None for p in new)}</b>\n"
            "One identity per Telegram user. Saved numbers do not automatically count as verified.")
    rows = [[cb("🆕 NEW / UNWORKED","q:new:0"), cb("⏰ DUE NOW","q:due:0")],
            [cb("👥 ALL LEADS","q:all:0"), cb("📱 SAVED MOBILES","q:mobile:0")],
            [cb("👤 UNASSIGNED","q:unassigned:0"), cb("📞 FOLLOW-UP","q:followup:0")],
            [cb("⭐ INTERESTED","q:interested:0"), cb("✅ CONVERTED","q:converted:0")],
            [cb("🔎 SEARCH","search"), cb("📥 CSV EXPORT","export")],
            [cb("🚫 DNC","q:dnc:0"), cb("NOT INTERESTED","q:not_interested:0")],
            [B("🎯 AD / CAMPAIGN REPORTS",callback_data="v91_reports:url"),cb("🤖 AUTOMATION STATUS","automation")],
            [cb("🧪 TEST ACCOUNT","test"),cb("📚 TEAM GUIDE","guide")],
            [B("➕ CREATE LINK",callback_data="campaign_add_single"),B("🎯 PIXEL MANAGER",callback_data="campaign_pixel_manager")],
            [B("⚙️ EXISTING BETROXY TOOLS",callback_data="v94_admin_home")]]
    return text, K(rows)


def card(p):
    verification = "✅ VERIFIED (Telegram contact)" if p["verified"] else "⚠️ NOT VERIFIED"
    warning = "\n🧪 <b>DESIGNATED TEST ACCOUNT</b>" if p["is_test"] else ""
    return (f"<b>{e(p['status'].replace('_',' ').upper())}</b>{warning}\n"
            f"👤 <b>{e((p['name'] or 'User')[:100])}</b> · @{e(p['username'] or '—')}\n"
            f"🆔 <code>{p['uid']}</code>\n📱 <code>{e(p['phone'] or 'Not captured')}</code>\n"
            f"🔐 {verification}\n"
            f"☎️ Calls consent: <b>{'Yes' if p['calls'] else 'Not recorded'}</b> · WhatsApp: <b>{'Yes' if p['whatsapp'] else 'Not recorded'}</b>\n"
            f"📥 Source: {e(p['sources'][:140])}\n🎯 Start/campaign payload: <code>{e((p['campaign'] or 'Not recorded')[:100])}</code>\n"
            f"👨‍💼 Assigned: <b>{e(p['assigned_to'] or 'Unassigned')}</b>\n"
            f"🕒 First: {when(p['first_seen'])}\nLast: {when(p['last_seen'])}\n"
            f"⏰ Follow-up: {when(p['next_followup'])}\n📝 {e((p['note'] or 'No note')[:320])}")


def card_keys(p, queue, index, count):
    tail = f":{p['uid']}:{queue}:{index}"
    rows = [[cb("🙋 ASSIGN TO ME","assign"+tail),cb("UNASSIGN","unassign"+tail)],
            [cb("📝 NOTE","note"+tail),cb("⏰ FOLLOW-UP","followup"+tail),cb("🕘 HISTORY","history"+tail)],
            [cb("📞 CONTACTED","s:contacted"+tail),cb("📵 NO ANSWER","s:no_answer"+tail)],
            [cb("⭐ INTERESTED","s:interested"+tail),cb("✅ CONVERTED","s:converted"+tail)],
            [cb("NOT INTERESTED","s:not_interested"+tail),cb("🚫 DNC","s:dnc"+tail),cb("↩️ NEW","s:new"+tail)]]
    if p["whatsapp"] and p["phone"] and p["status"] != "dnc":
        import re
        digits = re.sub(r"\D", "", p["phone"])
        if 8 <= len(digits) <= 15:
            rows.append([B("💬 OPEN WHATSAPP",url="https://wa.me/"+digits)])
    nav = []
    if index > 0:
        nav.append(cb("◀ PREVIOUS",f"q:{queue}:{index-1}"))
    if index + 1 < count:
        nav.append(cb("NEXT ▶",f"q:{queue}:{index+1}"))
    if nav:
        rows.append(nav)
    rows.append([cb("⬅️ DASHBOARD","home")])
    return K(rows)


class UI:
    def __init__(self, store, bot, production):
        self.store, self.bot, self.production = store, bot, production

    def authorized(self, update):
        return bool(update.effective_user and update.effective_chat and str(update.effective_chat.type) == "private" and self.bot.is_admin(update.effective_user.id))

    async def ack(self, q, text=None):
        try:
            await q.answer(text=text)
        except Exception as exc:
            log.warning("BTX_CRM_ACK_SKIPPED type=%s", type(exc).__name__)

    async def display(self, message, text, markup=None, edit=False):
        if edit:
            try:
                return await message.edit_text(text,parse_mode="HTML",reply_markup=markup,disable_web_page_preview=True)
            except Exception as exc:
                if "message is not modified" in str(exc).lower():
                    return None
                log.warning("BTX_CRM_EDIT_FALLBACK type=%s",type(exc).__name__)
        return await message.reply_text(text,parse_mode="HTML",reply_markup=markup,disable_web_page_preview=True)

    async def home(self, update, context):
        if not self.authorized(update):
            return
        context.user_data.pop("btxcrm_pending",None)
        context.user_data.pop("btxcrm_search",None)
        text, keys = dashboard(await asyncio.to_thread(self.store.snapshot))
        await self.display(update.effective_message,text,keys)
        raise ApplicationHandlerStop

    async def show_queue(self, message, context, queue, index, edit=True, preferred_uid=None):
        people = await asyncio.to_thread(self.store.snapshot)
        rows = select_queue(people,queue,context.user_data.get("btxcrm_search","") if queue == "all" else "")
        if preferred_uid is not None:
            index = next((i for i,p in enumerate(rows) if p["uid"] == preferred_uid),index)
        index = min(max(0,index),max(0,len(rows)-1))
        if not rows:
            await self.display(message,f"<b>{LABELS[queue]} · ALL TIME</b>\n\nNo records in this queue.",K([[cb("⬅️ DASHBOARD","home")]]),edit)
            return
        p = rows[index]
        await self.display(message,f"<b>{LABELS[queue]} · {index+1} of {len(rows)}</b>\n\n"+card(p),card_keys(p,queue,index,len(rows)),edit)

    async def automation(self):
        p = self.production
        active = bool(getattr(p.daily_schedule,"SCHEDULE_ENABLED",False))
        return ("🤖 <b>BETROXY AUTOMATION STATUS</b>\n\n"
                f"Existing quiz scheduler configured: <b>{'ON' if active else 'OFF'}</b>\n"
                f"Public result posting configured: <b>{'ON' if p.daily_schedule.RESULT_CHANNEL_ENABLED else 'OFF'}</b>\n"
                f"Posting channel: <b>{e(p.v110.CHANNEL_CHAT)}</b>\n\n"
                "Daily quiz: 10:00–21:00 IST; results 21:05.\nDaily banners: 10:00 / 16:00 / 19:00 IST.\n"
                "Sunday Mega: 10:00–21:00 IST; results 21:10.\nWeekly banners: Wed/Fri/Sat 19:30; Sun 10:05/16:05/19:05 IST.\n\n"
                "Private reminders still use the existing slow queue, limits and seven-day Business policy. This screen does not toggle automation or start another worker.\n"
                "Configuration is not proof of delivery; use Existing BETROXY Tools for delivery reports.")

    async def callback(self, update, context):
        q = update.callback_query
        if not q:
            return
        data = str(q.data or "")
        if not data.startswith("btxcrm:"):
            return
        await self.ack(q)
        if not self.authorized(update):
            raise ApplicationHandlerStop
        parts = data.split(":")[1:]
        try:
            action = parts[0]
            if action == "home":
                context.user_data.pop("btxcrm_search",None)
                context.user_data.pop("btxcrm_pending",None)
                text, keys = dashboard(await asyncio.to_thread(self.store.snapshot))
                await self.display(q.message,text,keys,True)
            elif action == "q" and len(parts) == 3:
                queue,index = parts[1],int(parts[2])
                if queue not in QUEUES:
                    raise ValueError("Invalid queue")
                await self.show_queue(q.message,context,queue,index)
            elif action == "guide":
                await self.display(q.message,GUIDE,K([[cb("⬅️ DASHBOARD","home")]]),True)
            elif action == "automation":
                await self.display(q.message,await self.automation(),K([[cb("🔄 REFRESH","automation"),cb("⬅️ DASHBOARD","home")]]),True)
            elif action == "search":
                context.user_data["btxcrm_pending"] = {"action":"search","expires":time.monotonic()+600}
                await self.display(q.message,"🔎 Send a Telegram ID, username, name or saved mobile.\nUse /cancelcrm to cancel.")
            elif action == "export":
                raw = await asyncio.to_thread(self.store.export)
                await q.message.reply_document(document=io.BytesIO(raw),filename="BETROXY_All_Time_Leads.csv",caption="All-time CRM export. Includes separate verification, consent and test-account fields. Treat personal contact data as confidential.")
            elif action == "test":
                state = await asyncio.to_thread(self.store.test_state)
                await self.display(q.message,"🧪 <b>VERIFICATION PILOT</b>\n\n"
                    f"Tester: <b>@{TEST_USERNAME}</b>\nPinned Telegram ID: <code>{self.store.test_uid}</code>\n"
                    f"Separate pilot result: <b>{'Verified' if state else 'Not yet tested'}</b>\n"
                    f"Time: {when(state.get('verified_at'))}\n\n"
                    "The tester can send /verifytest to this bot. Only their own Telegram-linked contact is accepted. Existing live verification, phones, consent and prizes are unchanged.\n\n"
                    "This is Telegram contact verification, not an SMS OTP.",K([[cb("VIEW TEST LEAD","q:test:0"),cb("⬅️ DASHBOARD","home")]]),True)
            else:
                status = None
                if action == "s":
                    if len(parts) != 5:
                        raise ValueError("Invalid status action")
                    status,uid,queue,index = parts[1],int(parts[2]),parts[3],int(parts[4])
                else:
                    if len(parts) != 4:
                        raise ValueError("Invalid action")
                    uid,queue,index = int(parts[1]),parts[2],int(parts[3])
                if queue not in QUEUES:
                    raise ValueError("Invalid queue")
                if action in {"note","followup"}:
                    context.user_data["btxcrm_pending"] = {"action":action,"uid":uid,"queue":queue,"index":index,"expires":time.monotonic()+600}
                    prompt = "Send the note (maximum 1,000 characters)." if action == "note" else "Send the follow-up time as YYYY-MM-DD HH:MM in Dubai time."
                    await self.display(q.message,prompt+"\nUse /cancelcrm to cancel.")
                elif action == "history":
                    entries = await asyncio.to_thread(self.store.history,uid)
                    lines = ["🕘 <b>CRM AUDIT HISTORY</b>", f"User: <code>{uid}</code>",""]
                    for entry in entries:
                        lines.append(f"{when(entry['created_at'])} · Admin {entry['actor_user_id']} · {e(entry['action'])}\n{e(entry['detail'][:180])}")
                    await self.display(q.message,"\n".join(lines) if entries else "No CRM changes recorded yet.")
                elif action in {"s","assign","unassign"}:
                    method,value = ("status",status) if action == "s" else ("assign",update.effective_user.id if action == "assign" else None)
                    await asyncio.to_thread(self.store.change,uid,update.effective_user.id,method,value)
                    await self.show_queue(q.message,context,queue,index,preferred_uid=uid)
                else:
                    raise ValueError("Unknown action")
        except (ValueError,PermissionError) as exc:
            await self.display(q.message,"⚠️ "+e(str(exc)))
        except Exception as exc:
            log.exception("BTX_CRM_ACTION_FAILED action=%s type=%s",parts[0],type(exc).__name__)
            await self.display(q.message,"⚠️ The action could not be completed. No prize purchase or verification reset was attempted. Reopen /crm and check History before repeating a change.")
        raise ApplicationHandlerStop

    async def clear_pending(self, update, context):
        # Never consume /start or /stop; the locked handler still receives them.
        context.user_data.pop("btxcrm_pending",None)
        context.user_data.pop("btxverify_pending",None)

    async def cancel(self, update, context):
        if not self.authorized(update) and int(update.effective_user.id) != self.store.test_uid:
            return
        await self.clear_pending(update,context)
        await update.effective_message.reply_text("New admin/test action cancelled. Existing BETROXY records are unchanged.",reply_markup=ReplyKeyboardRemove())
        raise ApplicationHandlerStop

    async def input(self, update, context):
        state = context.user_data.get("btxcrm_pending")
        if not state or not self.authorized(update):
            return
        if state["expires"] < time.monotonic():
            context.user_data.pop("btxcrm_pending",None)
            await self.display(update.effective_message,"This admin input expired. Reopen /crm.")
            raise ApplicationHandlerStop
        text = str(update.effective_message.text or "").strip()
        try:
            if state["action"] == "search":
                if not text or len(text) > 100:
                    raise ValueError("Search must contain 1–100 characters")
                context.user_data["btxcrm_search"] = text
                await self.show_queue(update.effective_message,context,"all",0,False)
            else:
                value = parse_followup(text) if state["action"] == "followup" else text
                await asyncio.to_thread(self.store.change,state["uid"],update.effective_user.id,state["action"],value)
                await self.show_queue(update.effective_message,context,state["queue"],state["index"],False,state["uid"])
            context.user_data.pop("btxcrm_pending",None)
        except (ValueError,PermissionError) as exc:
            await self.display(update.effective_message,"⚠️ "+e(str(exc)))
        except Exception:
            log.exception("BTX_CRM_INPUT_FAILED")
            context.user_data.pop("btxcrm_pending",None)
            await self.display(update.effective_message,"Action interrupted. Check History in /crm before retrying.")
        raise ApplicationHandlerStop

    async def verify_begin(self, update, context):
        if not update.effective_chat or str(update.effective_chat.type) != "private" or not update.effective_user or update.effective_user.id != self.store.test_uid:
            return
        if await asyncio.to_thread(self.store.test_busy):
            await update.effective_message.reply_text("Please finish the current quiz question before starting the verification test.")
            raise ApplicationHandlerStop
        # A separate voluntary test command; never replaces a product entry gate.
        context.user_data["btxverify_pending"] = time.monotonic()+600
        await update.effective_message.reply_text("🧪 BETROXY verification test\n\nShare your own Telegram-linked mobile using the button below. This stores only a separate test result; your live profile, quiz registration, marketing consent and reward history are not reset.\n\nUse /cancelcrm or /stop to leave this test.",reply_markup=ReplyKeyboardMarkup([[KeyboardButton("✅ VERIFY TEST MOBILE",request_contact=True)]],resize_keyboard=True,one_time_keyboard=True))
        raise ApplicationHandlerStop

    async def verify_contact(self, update, context):
        deadline = context.user_data.get("btxverify_pending",0)
        if not deadline or not update.effective_user or update.effective_user.id != self.store.test_uid or not update.effective_chat or str(update.effective_chat.type) != "private":
            return
        msg = update.effective_message
        if time.monotonic() > deadline:
            context.user_data.pop("btxverify_pending",None)
            await msg.reply_text("Test expired. Send /verifytest to try again.",reply_markup=ReplyKeyboardRemove())
            raise ApplicationHandlerStop
        contact = getattr(msg,"contact",None)
        try:
            if not contact:
                await msg.reply_text("Use VERIFY TEST MOBILE. Typing a number does not verify it.")
            else:
                await asyncio.to_thread(self.store.save_test,update.effective_user.id,getattr(contact,"user_id",None),contact.phone_number)
                context.user_data.pop("btxverify_pending",None)
                await msg.reply_text("✅ Test mobile verified via Telegram contact.\n\nOnly the separate pilot record was updated. Continue using the existing BETROXY menu.",reply_markup=ReplyKeyboardRemove())
        except (PermissionError,ValueError,TypeError):
            await msg.reply_text("Verification failed. Share your own Telegram-linked contact using the button.")
        raise ApplicationHandlerStop

    def register(self, app):
        if app.bot_data.get("btxcrm_installed"):
            return
        app.bot_data["btxcrm_installed"] = True
        app.add_handler(CommandHandler(["start","stop","cancel"],self.clear_pending),group=-10004)
        app.add_handler(CommandHandler(["crm","admin"],self.home),group=-10003)
        app.add_handler(CommandHandler("cancelcrm",self.cancel),group=-10003)
        app.add_handler(CommandHandler("verifytest",self.verify_begin),group=-10003)
        app.add_handler(CallbackQueryHandler(self.callback,pattern=r"^btxcrm:"),group=-10003)
        normal = ~filters.UpdateType.BUSINESS_MESSAGE & filters.ChatType.PRIVATE
        app.add_handler(MessageHandler(normal & (filters.CONTACT | (filters.TEXT & ~filters.COMMAND)),self.verify_contact),group=-10002)
        app.add_handler(MessageHandler(normal & filters.TEXT & ~filters.COMMAND,self.input),group=-10001)
        log.warning("BTX_CRM_HANDLERS_READY admin_private_only=on verification=tester_only existing_handlers_preserved=on")


def prepare(production):
    bot = production.bot
    store = Store(bot.get_db,bot.is_admin)
    people = store.setup()
    ui = UI(store,bot,production)
    old_main = bot.main
    # DNC is an additional veto; existing consent/pacing/eligibility still run.
    import safe_reminder_delivery as delivery
    old_send = delivery.send_claimed_result
    def guarded_send(user_id,*args,**kwargs):
        try:
            blocked = store.suppressed(user_id)
        except Exception:
            log.warning("BTX_CRM_DNC_CHECK_UNAVAILABLE send_skipped=on")
            return {"sent":False,"status":"crm_guard_unavailable","retried":0,"permanent":False,"rate_limited":False}
        if blocked:
            return {"sent":False,"status":"crm_dnc","retried":0,"permanent":False,"rate_limited":False}
        return old_send(user_id,*args,**kwargs)
    delivery.send_claimed_result = guarded_send

    def install_at_main():
        old_post_init = bot.post_init
        async def post_init(app):
            ui.register(app)
            await old_post_init(app)
            log.warning("BTX_CRM_READY leads=%s tester_pinned=%s core_routes=unchanged new_pollers=0 sms_otp=off",len(people),store.test_uid)
        bot.post_init = post_init
        # Add a shortcut, without removing or renaming any existing admin button.
        import v94_admin_mode_cleanup as v94
        import v91_admin_categories_reporting_hub as v91
        old_menu = v94.v94_admin_menu
        def menu():
            return K([[cb("📊 TEAM CRM & VERIFICATION","home")]]+[list(r) for r in old_menu().inline_keyboard])
        v94.v94_admin_menu = menu
        v91.v91_admin_menu = menu
        bot.admin_menu = menu
        return old_main()
    bot.main = install_at_main
    log.warning("BTX_CRM_PREPARED legacy_tables=read_only new_tables=4 tester=%s",store.test_uid)
    return store,ui
