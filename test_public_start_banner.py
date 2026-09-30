import asyncio
import unittest
import struct
from types import SimpleNamespace

import public_start_banner as psb


class FakeBot:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def send_photo(self, **kwargs):
        if self.fail:
            raise RuntimeError("telegram down")
        photo = kwargs["photo"]
        self.calls.append({"chat_id": kwargs["chat_id"], "keys": sorted(kwargs),
                           "photo": photo if isinstance(photo, str) else "file"})
        return SimpleNamespace(photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="big")])


def update(chat_type="private"):
    return SimpleNamespace(effective_chat=SimpleNamespace(id=42, type=chat_type),
                           effective_message=object())


class PublicStartBannerTests(unittest.TestCase):
    def setUp(self):
        psb._cached_file_id = None

    def run_start(self, bot, args=None, chat_type="private"):
        asyncio.run(psb.send_public_start_banner(update(chat_type), SimpleNamespace(bot=bot, args=args or [])))

    def test_banner_file_is_1280x720_jpeg(self):
        data = psb.BANNER_PATH.read_bytes()
        self.assertEqual(data[:2], b"\xff\xd8")
        i = 2
        while i < len(data):
            marker, length = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[i + 5:i + 9])
                break
            i += 2 + length
        self.assertEqual((width, height), (1280, 720))

    def test_sends_plain_photo_then_reuses_cached_file_id(self):
        bot = FakeBot()
        self.run_start(bot)
        self.run_start(bot, ["campaign"])
        self.assertEqual(bot.calls[0], {"chat_id": 42, "keys": ["chat_id", "photo"], "photo": "file"})
        self.assertEqual(bot.calls[1]["photo"], "big")

    def test_failure_never_raises_and_skips_non_private_and_stop(self):
        self.run_start(FakeBot(fail=True))
        bot = FakeBot()
        self.run_start(bot, chat_type="group")
        self.run_start(bot, ["stopreminders"])
        self.assertEqual(bot.calls, [])

    def test_install_registers_one_early_handler(self):
        added, handlers = [], {}

        def add_handler(handler, group):
            added.append((handler, group))
            handlers.setdefault(group, []).append(handler)

        app = SimpleNamespace(add_handler=add_handler, handlers=handlers)
        psb.install(app)
        psb.install(app)
        self.assertEqual(len(added), 1)
        handler, group = added[0]
        self.assertEqual(group, psb.HANDLER_GROUP)
        self.assertIn("start", handler.commands)
        self.assertFalse(getattr(handler, "block", True) is False)


if __name__ == "__main__":
    unittest.main()
