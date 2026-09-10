import threading
import time

import bot
import v103_reward_test_menu_fix as v103

from telegram import ReplyKeyboardRemove
from telegram.ext import Application, MessageHandler, filters

v102 = v103.v102
v101 = v103.v101
v100 = v103.v100
v99 = v103.v99
v98 = v103.v98
v97 = v103.v97
v96 = v103.v96
v93 = v103.v93
v89 = v103.v89
v88 = v103.v88
v85 = v103.v85
v83 = v103.v83

_prev_add_handler = Application.add_handler
_installed_apps = set()


async def _v104_contact_handler(update, context):
    user = update.effective_user
    msg = update.effective_message
    chat = update.effective_chat
    contact = getattr(msg, 'contact', None) if msg else None
    if not user or not msg or not contact or not chat or str(getattr(chat, 'type', '')) != 'private':
        return

    contact_uid = getattr(contact, 'user_id', None)
    # If Telegram supplies a user_id, it must be the sender's own id. When
    # request_contact omits user_id, the explicit contact share is still treated
    # as consent, but only in the private OfficialBot chat.
    if contact_uid is not None and int(contact_uid) != int(user.id):
        await msg.reply_text(
            'For privacy, please use the 📱 Share My Mobile button to share your own number.',
            reply_markup=ReplyKeyboardRemove(),
        )
        bot.logger.warning('V104_MOBILE_CAPTURE rejected uid=%s username=%s reason=contact_uid_mismatch', user.id, user.username)
        return

    row = v89._save_mobile(user.id, getattr(contact, 'phone_number', ''), 'officialbot_contact_v104')
    if not row:
        await msg.reply_text('I could not validate that mobile number. Please tap Verify Mobile and try again.', reply_markup=ReplyKeyboardRemove())
        bot.logger.warning('V104_MOBILE_CAPTURE failed uid=%s username=%s reason=normalize_failed', user.id, user.username)
        return

    digits = ''.join(ch for ch in str(row.get('mobile_number') or '') if ch.isdigit())
    masked = ('••••••' + digits[-4:]) if len(digits) >= 4 else 'saved'
    await msg.reply_text(
        f'✅ Mobile verified for rewards: {masked}\n\nYou can now return to Reward Center and run the PhonePe ₹30 test.',
        reply_markup=ReplyKeyboardRemove(),
    )
    bot.logger.warning('V104_MOBILE_CAPTURE saved uid=%s username=%s consent=yes', user.id, user.username)


def _v104_add_handler(self, handler, group=0):
    app_id = id(self)
    if app_id not in _installed_apps:
        _installed_apps.add(app_id)
        # Install before generic chat handlers so contact updates cannot be swallowed.
        _prev_add_handler(
            self,
            MessageHandler(filters.CONTACT & ~filters.UpdateType.BUSINESS_MESSAGE, _v104_contact_handler),
            group=-100,
        )
    return _prev_add_handler(self, handler, group=group)


def _startup_mobile_diagnostic():
    try:
        lead = v102._find_test_user()
        if not lead:
            bot.logger.warning('V104_MOBILE_DIAGNOSTIC target_found=no')
            return
        uid = int(lead['telegram_user_id'])
        row = v89._mobile_row(uid) or {}
        has_mobile = bool(row.get('mobile_number'))
        has_consent = bool(row.get('mobile_consent_at'))
        bot.logger.warning(
            'V104_MOBILE_DIAGNOSTIC target_found=yes username=@%s reachable=%s profile_row=%s mobile_present=%s consent=%s',
            v102.TEST_USERNAME,
            bool(lead.get('reachable_bot')),
            bool(row),
            has_mobile,
            has_consent,
        )
    except Exception:
        bot.logger.exception('V104_MOBILE_DIAGNOSTIC_FAILED')


Application.add_handler = _v104_add_handler
# Preserve V103/V102 reward routing.
v97._reward_center_keyboard = v102._v102_reward_center_keyboard
v89._reward_center_keyboard = v102._v102_reward_center_keyboard
bot.callback_handler = v102.v102_callback_handler

bot.logger.warning('V104_MOBILE_CAPTURE_FIX active=on private_contact_priority=-100 test_account=@mohit_97saxena')


if __name__ == '__main__':
    v97._startup_diagnostic()
    v96._startup_diagnostic()
    v93._startup_pdf_diagnostic()
    v101._phonepe_probe()
    _startup_mobile_diagnostic()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name='betroxy-engagement-worker', daemon=True).start()
    bot.logger.warning('V104 polling handover delay=12s')
    time.sleep(12)
    bot.main()
