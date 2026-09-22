"""Read-only regression guard for the locked BETROXY product modules."""
from pathlib import Path
import hashlib

BASE_COMMIT = '83cc196164584e8f2c01d8a4689e1c6d16c1d740'
PROTECTED = {
    'bot.py':'080430ad389e86f5ff6f79414a3be73177a2201a',
    'production.py':'a053dc022fde4bc53aaa53ab1c7413537f0ea766',
    'production_channel_authority.py':'1dae8afaeaee049547246b7bf5ec10a74958fd5c',
    'reward_receipt_confirmation_authority.py':'69758f69d25057b9f7209007c5c7937942cc8f14',
    'reward_receipt_confirmation.py':'5c32c8e59fe831e6a101f3c87ae966decd26de0b',
    'reward_code_display_fix.py':'77d6fce30c64e0251e181733d4c592c4e1c626cc',
    'daily_reward_provider_failure_recovery.py':'86752508c498d7a227751a097752a9e9a62970d4',
    'daily_quiz_schedule.py':'5a9c1dc60469d767906ee7e29ec847d7744297a6',
    'daily_quiz_alerts.py':'0fc8be79f4523cba7df435e4b265d882f3ea73e9',
    'safe_reminder_delivery.py':'0d7c093a3bcdec97004df0152e883c05a39a0083',
    'public_quiz_channel_schedule.py':'9ea43f409447823996f2436c2759b356d2d8b8df',
    'business_weekly_reminder_policy.py':'49ac90b7a30dccd2af8761a44344015975f6bf3c',
    'quiz_mobile_verification_overlay.py':'8ab025615f38589f3239635570de16dc200cd669',
    'v110_betroxy_daily_challenge.py':'339c2789fddc58628aaae43a3d5a4f10553bb7f4',
    'daily_quiz_opening_guard.py':'b06abfca223a4c95c640f8ad87f42a594c4142e4',
    'daily_quiz_admin_rewards.py':'f9b32659ac6080538737e9646720dd26688eadd3',
    'weekly_mega_quiz.py':'7a32769aafe9169a76d8dc051312cdcb94e4f0a2',
    'weekly_mega_quiz_additions.py':'199b5ce99fa417563351aac69fde12645967a2a7',
    'weekly_mega_channel_schedule.py':'d41724478c89902a55805eef533b034b39980590',
    'weekly_mega_channel_authority_fix.py':'c59dd77d8c7fcadc8f50303b14b89c8689e980f9',
    'clean_customer_menu.py':'d7fd1bc263cc73b1b48706897b2dedc01e5860fe',
    'unified_customer_menu.py':'766f28f625c9712f5d75d166f9d7b082fd1bb36b',
    'channel_media_manager.py':'1c64cde83b5f024f82ea25cd386e7e5f80ddeefd',
    'banner_manager.py':'11691c8f1712f880b3e57e7117227a65bedcf4c2',
    'v91_admin_categories_reporting_hub.py':'84bea4957128c0ce79061947489768d876923aac',
    'v94_admin_mode_cleanup.py':'9b0cb3ef34b25def449fad91ad0010bcd2b96712',
    'v47_pixel_manager_menu_bootstrap.py':'53d66195dee9c45aaa4f909e61045de94cd0eec6',
    'v49_telegram_business_inbox_bootstrap.py':'d524cdc3bdd0a9face0f761ec91da3b12306abaa',
}


def git_blob_sha(raw: bytes) -> str:
    return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()


def verify(root=None):
    root = Path(root or Path(__file__).parent)
    mismatches = [name for name, sha in PROTECTED.items()
                  if not (root/name).is_file() or git_blob_sha((root/name).read_bytes()) != sha]
    if mismatches:
        raise RuntimeError('Protected BETROXY modules differ from the inspected baseline: '+', '.join(mismatches))
    return len(PROTECTED)


if __name__ == '__main__':
    print('BETROXY_CORE_FILE_GUARD protected_files='+str(verify())+' unchanged=all base='+BASE_COMMIT)
