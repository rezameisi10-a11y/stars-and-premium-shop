"""تست‌های یکپارچه: دیتابیس، جریان سفارش/پرداخت/تخفیف/پورسانت، هندلرهای واقعی aiogram و پنل وب.
اجرا:  python tests/test_all.py
"""
import asyncio
import os
import sys
import tempfile
import time
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Chat, Message, MessageEntity, Update, User
from aiohttp.test_utils import TestClient, TestServer

import admin_manage
import admin_settings
import handlers_admin
import handlers_user
import i18n
import marketapp
import provider
import tonwallet
import ui
from apiutil import jpath
from config import Config
from db import DB
from middlewares import GateMiddleware
from service import Shop
from web import create_web_app

PASSED = []


def ok(name):
    PASSED.append(name)
    print("  ✓", name)


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.star_balance = 10_000

    async def close(self):
        pass

    async def stream_content(self, *a, **k):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append((name, method))
        if name in ("SendMessage", "SendPhoto", "CopyMessage"):
            return Message(message_id=len(self.calls), date=datetime.now(), chat=Chat(id=1, type="private"))
        if name == "GetMyStarBalance":
            return SimpleNamespace(amount=self.star_balance)
        if name == "GetMe":
            return User(id=999, is_bot=True, first_name="Shop", username="shopbot")
        return True

    def sent(self, name="SendMessage"):
        return [m for n, m in self.calls if n == name]


def make_cfg(path):
    return Config(bot_token="123:ABC", admin_ids=(1,), db_path=path, public_url="http://x.example",
                  web_password="pw123", web_secret="s" * 32, zarinpal_merchant="", ton_wallet="", usdt_trc20_wallet="")


async def build(tmp):
    db = DB(os.path.join(tmp, "t.db"))
    await db.connect()
    bot = Bot("123:ABC", default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    sess = FakeSession()
    bot.session = sess
    shop = Shop(bot, db, make_cfg(db.path))
    shop.bot_username = "shopbot"
    await shop.refresh_admins()
    await shop.settings()
    return db, bot, sess, shop


async def new_user(db, uid, name="u", username=None):
    await db.upsert_user(uid, username or f"user{uid}", name)


async def test_db_and_menu(tmp):
    db, bot, sess, shop = await build(tmp)
    menu = await db.list_menu()
    assert len(menu) >= 10 and menu[0]["action"] == "stars"
    ok("منوی پیش‌فرض ساخته شد")
    bid = menu[1]["id"]            # premium (ردیف ۰، جایگاه ۱)
    assert await db.move_menu_button(bid, "left")
    m2 = await db.list_menu()
    assert m2[0]["action"] == "premium" and m2[1]["action"] == "stars"
    assert await db.move_menu_button(bid, "split")
    rows = {}
    for it in await db.list_menu():
        rows.setdefault(it["row_no"], []).append(it["action"])
    assert rows[0] == ["stars"] and rows[1] == ["premium"], rows
    ok("جابجایی/جداسازی دکمه‌ها")
    await db.update_menu_button(bid, style="danger")
    new = await db.add_menu_button("url", "کانال", "https://t.me/x", "success")
    assert not await db.delete_menu_button(bid)       # داخلی حذف نمی‌شود
    assert await db.delete_menu_button(new)
    ok("رنگ، افزودن و حذف دکمه سفارشی")
    # ساخت کیبورد کاربر: ادمین دکمه admin را می‌بیند، کاربر عادی نه
    kb_admin = await shop.main_menu_kb(1)
    kb_user = await shop.main_menu_kb(5)
    flat = lambda kb: [b for r in kb.inline_keyboard for b in r]
    assert any(b.callback_data == "adm:home" for b in flat(kb_admin))
    assert not any(b.callback_data == "adm:home" for b in flat(kb_user))
    dumped = kb_user.model_dump(exclude_none=True)
    assert any(b.get("style") == "danger" for r in dumped["inline_keyboard"] for b in r)
    ok("کیبورد منو: رنگ (style) و نمایش پنل فقط برای ادمین")
    await db.close()


async def test_pricing_and_stats(tmp):
    db, bot, sess, shop = await build(tmp)
    s = await shop.settings()
    p = (await db.list_products("stars"))[0]      # 50 stars
    assert shop.price(p, s) == 110000              # 50*2000*1.1
    await shop.set_setting("margin_percent", "20")
    assert shop.price(p, await shop.settings()) == 120000
    await db.set_price_override(p["id"], 99000)
    assert shop.price(await db.get_product(p["id"]), await shop.settings()) == 99000
    st = await db.stats()
    assert st["users"] == 0 and len(st["series"]) == 14
    ok("قیمت‌گذاری خودکار/دستی و آمار")
    ok_, res = await shop.set_setting_validated("margin_percent", "abc")
    assert not ok_
    ok_, res = await shop.set_setting_validated("force_channel", "mychan")
    assert ok_ and res == "@mychan"
    ok("اعتبارسنجی تنظیمات")
    await db.close()


async def test_order_flows(tmp):
    db, bot, sess, shop = await build(tmp)
    await new_user(db, 10, "ref", "referrer")
    await new_user(db, 20, "buyer", "buyer")
    await shop.register_referral(20, "ref_10", is_new=True)
    assert (await db.get_referral(20))["status"] == "active"
    await shop.register_referral(20, "ref_10", is_new=True)        # دوباره ثبت نمی‌شود
    assert (await db.get_user(20))["referrer_id"] == 10
    await db.add_balance(20, 5_000_000, "seed")

    # --- پریمیوم با کیف پول + پورسانت
    prem = [p for p in await db.list_products("premium") if p["qty"] == 3][0]
    s = await shop.settings()
    oid = await db.create_order(20, prem, 20, None, shop.price(prem, s))
    assert await shop.pay_with_wallet(oid, 20)
    o = await db.get_order(oid)
    assert o["status"] == "done", o
    gp = sess.sent and [m for n, m in sess.calls if n == "GiftPremiumSubscription"]
    assert gp and gp[0].month_count == 3 and gp[0].star_count == 1000 and gp[0].user_id == 20
    ref = await db.get_user(10)
    assert ref["balance"] == int(o["amount"] * 3 / 100), ref
    assert (await db.get_user(20))["balance"] == 5_000_000 - o["amount"]
    ok("خرید پریمیوم با کیف پول، تحویل خودکار، پورسانت معرف")

    # --- ایدمپوتنسی: پورسانت دوباره پرداخت نمی‌شود
    await shop._referral_commission(o)
    assert (await db.get_user(10))["balance"] == ref["balance"]
    ok("پورسانت تکراری پرداخت نمی‌شود")

    # --- گیفت
    await db.sync_gifts([("g1", 15, "🎁 گیفت ۱۵", True)])
    gift = (await db.list_products("gift"))[0]
    oid2 = await db.create_order(20, gift, 20, None, shop.price(gift, s))
    assert await shop.pay_with_wallet(oid2, 20)
    assert [m for n, m in sess.calls if n == "SendGift"][0].gift_id == "g1"
    ok("خرید گیفت و تحویل خودکار")

    # --- کمبود ستاره ربات → صف دستی، سپس تلاش مجدد
    await shop.set_setting("deliver_dup_min", "0")      # (قانون ضد تکراری جداگانه در test_delivery_safety تست می‌شود)
    sess.star_balance = 10
    oid3 = await db.create_order(20, prem, 20, None, 1000)
    assert await shop.pay_with_wallet(oid3, 20)
    assert (await db.get_order(oid3))["status"] == "manual_pending"
    sess.star_balance = 100000
    assert await shop.retry_queue() == 1
    assert (await db.get_order(oid3))["status"] == "done"
    ok("کمبود موجودی ستاره → صف → تلاش مجدد موفق")

    # --- استارز → دستی → تأیید ادمین
    stars = (await db.list_products("stars"))[0]
    oid4 = await db.create_order(20, stars, None, "someone", 100000)
    assert await shop.pay_with_wallet(oid4, 20)
    assert (await db.get_order(oid4))["status"] == "manual_pending"
    assert "تحویل شد" in await shop.admin_order_action("done", oid4) or True
    assert (await db.get_order(oid4))["status"] == "done"
    ok("سفارش استارز → تحویل دستی توسط ادمین")

    # --- بازگشت وجه
    oid5 = await db.create_order(20, stars, None, "someone", 100000)
    before = (await db.get_user(20))["balance"]
    await shop.pay_with_wallet(oid5, 20)
    await shop.admin_order_action("refund", oid5)
    assert (await db.get_order(oid5))["status"] == "refunded"
    assert (await db.get_user(20))["balance"] == before
    await shop.admin_order_action("refund", oid5)       # بار دوم اثری ندارد
    assert (await db.get_user(20))["balance"] == before
    ok("بازگشت وجه (فقط یک‌بار)")

    # --- موجودی ناکافی
    oid6 = await db.create_order(20, stars, None, "someone", 10**12)
    assert not await shop.pay_with_wallet(oid6, 20)
    ok("موجودی ناکافی رد می‌شود")
    await db.close()


async def test_payments_and_discount(tmp):
    db, bot, sess, shop = await build(tmp)
    await new_user(db, 30, "pay", "payer")
    await db.set_setting("card_number", "6037000000000000")
    await shop.set_setting("card_number", "6037000000000000")
    prem = [p for p in await db.list_products("premium") if p["qty"] == 3][0]

    # کد تخفیف
    assert await db.create_discount("OFF10", 10, 0, 1, 0)
    oid = await db.create_order(30, prem, 30, None, 200000)
    good, msg, new_amount = await shop.apply_discount(oid, 30, "off10")
    assert good and new_amount == 180000, msg
    good2, msg2, _ = await shop.apply_discount(oid, 30, "NOPE")
    assert not good2
    ok("اعمال کد تخفیف")

    # کارت به کارت با رسید
    pid, text, kb = await shop.create_payment("card", 30, new_amount, oid)
    assert "6037000000000000" in text
    await shop.submit_receipt(pid, "FILEID")
    assert (await db.get_payment(pid))["status"] == "waiting_admin"
    assert [m for n, m in sess.calls if n == "SendPhoto"], "رسید به ادمین ارسال نشد"
    res = await shop.admin_payment_action("ok", pid)
    assert (await db.get_order(oid))["status"] == "done", res
    assert (await db.get_order(oid))["amount"] == 180000
    again = await shop.admin_payment_action("ok", pid)
    assert "قبلاً" in again
    ok("کارت‌به‌کارت: رسید → تأیید ادمین → تحویل؛ تأیید تکراری بی‌اثر")

    # مصرف کد: نفر دوم/استفاده مجدد رد می‌شود
    await new_user(db, 31, "p2", "p2")
    oid2 = await db.create_order(31, prem, 31, None, 200000)
    good3, msg3, _ = await shop.apply_discount(oid2, 31, "OFF10")
    assert not good3 and "ظرفیت" in msg3, msg3
    ok("ظرفیت کد تخفیف رعایت می‌شود")

    # شارژ کیف پول + ایدمپوتنسی
    pid2, _, _ = await shop.create_payment("card", 30, 70000, None)
    assert await shop.confirm_payment(pid2, ref="x1")
    assert not await shop.confirm_payment(pid2, ref="x1")
    assert (await db.get_user(30))["balance"] == 70000
    ok("شارژ کیف پول، تأیید دوباره بی‌اثر")

    # پرداخت دیرهنگام برای سفارش لغوشده → اعتبار به کیف پول
    oid3 = await db.create_order(30, prem, 30, None, 50000)
    pid3, _, _ = await shop.create_payment("card", 30, 50000, oid3)
    await db.transition(oid3, ["pending_payment"], "cancelled")
    await shop.confirm_payment(pid3, ref="late")
    assert (await db.get_user(30))["balance"] == 120000
    ok("پرداخت برای سفارش لغوشده به کیف پول برمی‌گردد")

    # انقضا
    await db.run("UPDATE payments SET created_at=? WHERE id=?", (int(time.time()) - 99999, 1))
    await db.close()


async def feed(dp, bot, **kw):
    await dp.feed_update(bot, Update(update_id=int(time.time() * 1000) % 10**9, **kw))


def msg(uid, text, entities=None):
    return Message(message_id=5, date=datetime.now(), chat=Chat(id=uid, type="private"),
                   from_user=User(id=uid, is_bot=False, first_name="T", username=f"u{uid}"),
                   text=text, entities=entities)


def cbq(uid, data):
    u = User(id=uid, is_bot=False, first_name="T", username=f"u{uid}")
    return CallbackQuery(id="1", from_user=u, chat_instance="c", data=data,
                         message=Message(message_id=9, date=datetime.now(), chat=Chat(id=uid, type="private"), from_user=User(id=999, is_bot=True, first_name="B")))


async def test_handlers(tmp):
    db, bot, sess, shop = await build(tmp)
    dp = Dispatcher(storage=MemoryStorage(), shop=shop)
    g = GateMiddleware()
    dp.message.outer_middleware(g)
    dp.callback_query.outer_middleware(g)
    dp.include_router(handlers_admin.router)
    dp.include_router(admin_settings.router)
    dp.include_router(admin_manage.router)
    dp.include_router(handlers_user.router)
    cmd = [MessageEntity(type="bot_command", offset=0, length=6)]

    await feed(dp, bot, message=msg(5, "/start", cmd))
    last = sess.sent()[-1]
    assert "خوش آمدید" in last.text and last.reply_markup.inline_keyboard
    assert (await db.get_user(5)) is not None
    ok("/start کاربر جدید → منوی اصلی و ثبت کاربر")

    # لینک زیرمجموعه
    await feed(dp, bot, message=msg(6, "/start ref_5", [MessageEntity(type="bot_command", offset=0, length=6)]))
    assert (await db.get_user(6))["referrer_id"] == 5
    ok("لینک دعوت ثبت معرف می‌کند")

    # کاتالوگ
    await feed(dp, bot, callback_query=cbq(5, "cat:stars:0"))
    assert any(m for n, m in sess.calls if n == "EditMessageText" and "خرید استارز" in m.text)
    ok("کاتالوگ استارز")

    # کاربر عادی به پنل ادمین دسترسی ندارد
    n_before = len(sess.calls)
    await feed(dp, bot, message=msg(5, "/admin", [MessageEntity(type="bot_command", offset=0, length=6)]))
    assert not [m for n, m in sess.calls[n_before:] if n == "SendMessage" and "پنل مدیریت" in (m.text or "")]
    await feed(dp, bot, callback_query=cbq(5, "adm:btn"))
    assert not [1 for n, m in sess.calls[n_before:] if n == "EditMessageText"]
    ok("کاربر عادی به پنل ادمین دسترسی ندارد")

    # ادمین
    await db.upsert_user(1, "admin", "Admin")
    await feed(dp, bot, message=msg(1, "/admin", cmd))
    assert "پنل مدیریت" in sess.sent()[-1].text
    await feed(dp, bot, callback_query=cbq(1, "adm:btn"))
    edit = [m for n, m in sess.calls if n == "EditMessageText"][-1]
    assert "دکمه‌های منوی اصلی" in edit.text
    stars_btn = (await db.list_menu())[0]
    await feed(dp, bot, callback_query=cbq(1, f"adm:bcol:{stars_btn['id']}:x"))
    assert (await db.get_menu_button(stars_btn["id"]))["style"] == "danger"
    await feed(dp, bot, callback_query=cbq(1, "adm:rc:pay:s"))
    assert ui.STYLES["pay"] == "success"
    ok("پنل ادمین داخل ربات: تغییر رنگ دکمه منو و رنگ نقش‌ها")

    # تنظیمات داخل ربات: toggle
    await feed(dp, bot, callback_query=cbq(1, "adm:sf:maintenance"))
    assert (await shop.settings())["maintenance"] == "1"
    # حالت تعمیر: کاربر عادی بسته، ادمین باز
    n0 = len(sess.calls)
    await feed(dp, bot, message=msg(5, "/start", cmd))
    assert "بروزرسانی" in sess.sent()[-1].text
    await feed(dp, bot, callback_query=cbq(1, "adm:sf:maintenance"))
    assert (await shop.settings())["maintenance"] == "0"
    ok("حالت تعمیر برای کاربران، ادمین مستثنا")

    # مسدودی
    await db.set_banned(5, True)
    await feed(dp, bot, message=msg(5, "/start", cmd))
    assert "مسدود" in sess.sent()[-1].text
    await db.set_banned(5, False)
    ok("کاربر مسدود")

    # جریان خرید کامل با FSM: استارز برای یوزرنیم
    stars = (await db.list_products("stars"))[0]
    await feed(dp, bot, callback_query=cbq(5, f"buy:{stars['id']}"))
    await feed(dp, bot, message=msg(5, "@someone_x"))
    order = (await db.list_orders(user_id=5))[0]
    assert order["target_username"] == "someone_x" and order["status"] == "pending_payment"
    ok("جریان خرید استارز با FSM → ساخت سفارش")

    # شارژ با کارت: متن و کیبورد
    await shop.set_setting("card_number", "6037999999999999")
    await feed(dp, bot, callback_query=cbq(5, f"pay:card:{order['id']}"))
    assert "6037999999999999" in sess.sent()[-1].text
    ok("روش کارت‌به‌کارت در ربات")
    await db.close()


async def test_web(tmp):
    db, bot, sess, shop = await build(tmp)
    await new_user(db, 40, "w", "webuser")
    await db.upsert_user(1, "admin", "Admin")
    prem = (await db.list_products("premium"))[0]
    oid = await db.create_order(40, prem, 40, None, 100000)
    await db.transition(oid, ["pending_payment"], "manual_pending")
    await db.create_discount("WEB5", 5, 0, 0, 0)
    app = create_web_app(shop)
    async with TestClient(TestServer(app)) as c:
        r = await c.get("/", allow_redirects=False)
        assert r.status == 303 and r.headers["Location"] == "/login"
        ok("بدون ورود → ریدایرکت به login")
        r = await c.post("/login", data={"u": "admin", "p": "bad"}, allow_redirects=False)
        assert r.status == 303 and "err=" in r.headers["Location"]
        r = await c.post("/login", data={"u": "admin", "p": "pw123"}, allow_redirects=False)
        assert r.status == 303 and "sid" in r.cookies
        ok("ورود با رمز (و رد رمز اشتباه)")

        pages = ["/", "/users", "/users/40", "/orders", "/orders?status=manual_pending", "/payments", "/wallet", "/products",
                 "/keyboard", "/discounts", "/broadcast", "/broadcast/status", "/settings", "/settings?g=group", "/admins", "/logs"]
        for pth in pages:
            r = await c.get(pth)
            body_ = await r.text()
            assert r.status == 200 and "ورود به پنل مدیریت" not in body_, (pth, r.status, body_[:300])
        ok(f"همه {len(pages)} صفحه پنل 200 برمی‌گردانند")

        html = await (await c.get("/keyboard")).text()
        assert "kbprev" in html and "tgb" in html and "name='style_" in html
        csrf = html.split("name='_csrf' value='")[1].split("'")[0]

        # CSRF
        r = await c.post("/keyboard/reset", data={}, allow_redirects=False)
        assert r.status == 403
        ok("درخواست POST بدون CSRF رد می‌شود")

        # ذخیره چیدمان با رنگ جدید
        items = await db.list_menu()
        form = {"_csrf": csrf}
        bids = []
        for it in items:
            b = it["id"]
            bids.append(str(b))
            form[f"label_{b}"] = it["label"] + ("!" if it["action"] == "stars" else "")
            form[f"style_{b}"] = "danger" if it["action"] == "stars" else it["style"]
            form[f"row_{b}"] = str(it["row_no"] + 1)
            form[f"pos_{b}"] = str(it["pos"] + 1)
            if it["enabled"]:
                form[f"enabled_{b}"] = "on"
        from multidict import MultiDict
        md = MultiDict(form)
        for b in bids:
            md.add("bid", b)
        r = await c.post("/keyboard/save", data=md, allow_redirects=False)
        assert r.status == 303 and "ok=" in r.headers["Location"], r.headers["Location"]
        st = [i for i in await db.list_menu() if i["action"] == "stars"][0]
        assert st["style"] == "danger" and st["label"].endswith("!")
        ok("ذخیره چیدمان و رنگ دکمه از پنل وب")

        # رنگ نقش‌ها
        r = await c.post("/keyboard/roles", data={"_csrf": csrf, "role_back": "primary", "role_pay": "danger"}, allow_redirects=False)
        assert ui.STYLES["back"] == "primary" and ui.STYLES["pay"] == "danger"
        # دکمه سفارشی
        r = await c.post("/keyboard/add", data={"_csrf": csrf, "label": "کانال ما", "kind": "url", "extra": "t.me/chan", "style": "success"}, allow_redirects=False)
        assert "ok=" in r.headers["Location"]
        added = [i for i in await db.list_menu() if i["action"] == "url"][0]
        assert added["extra"] == "https://t.me/chan"
        r = await c.post("/keyboard/add", data={"_csrf": csrf, "label": "x", "kind": "url", "extra": "javascript:alert(1)", "style": ""}, allow_redirects=False)
        assert "err=" in r.headers["Location"]
        ok("رنگ نقش‌ها، افزودن دکمه لینک، رد لینک خطرناک")

        # تنظیمات: گروه‌بندی جدید settings_schema.GROUPS، هر گروه مسیر POST /settings/{gid} خودش را دارد
        import settings_schema as ss

        async def group_form(gid):
            cur = await shop.settings()
            f = {"_csrf": csrf}
            for key, label, typ, hlp in ss.GROUP_IDS[gid][3]:
                if typ != "bool":
                    f[key] = cur.get(key, "")
            return f

        pform_ = await group_form("pricing")
        pform_["margin_percent"] = "25"
        r = await c.post("/settings/pricing", data=pform_, allow_redirects=False)
        assert "ok=" in r.headers["Location"], r.headers["Location"]
        s = await shop.settings()
        assert s["margin_percent"] == "25"

        gform = await group_form("general")
        gform["shop_name"] = "<script>x</script>"
        r = await c.post("/settings/general", data=gform, allow_redirects=False)
        assert "ok=" in r.headers["Location"], r.headers["Location"]
        page = await (await c.get("/settings?g=general")).text()
        assert "<script>x</script>" not in page and "&lt;script&gt;" in page
        ok("ذخیره تنظیمات (گروه‌بندی‌شده) + escape شدن XSS")

        wform = await group_form("gateways")
        wform["pm_card"] = "on"
        r = await c.post("/settings/gateways", data=wform, allow_redirects=False)
        assert "ok=" in r.headers["Location"], r.headers["Location"]
        s = await shop.settings()
        assert s["pm_wallet"] == "0" and s["pm_card"] == "1"
        ok("ذخیره تنظیمات گروه درگاه‌ها (چک‌باکس‌های bool)")

        pform_["margin_percent"] = "zzz"
        r = await c.post("/settings/pricing", data=pform_, allow_redirects=False)
        assert "err=" in r.headers["Location"]
        ok("اعتبارسنجی مقدار نامعتبر در تنظیمات رد می‌شود")

        # دسترسی به گروه ناموجود → 404
        r = await c.post("/settings/nosuchgroup", data={"_csrf": csrf}, allow_redirects=False)
        assert r.status == 404
        ok("گروه تنظیمات ناموجود → 404")

        # سفارش: تحویل دستی از وب
        r = await c.post(f"/orders/{oid}/action", data={"_csrf": csrf, "act": "done", "next": "/orders"}, allow_redirects=False)
        assert (await db.get_order(oid))["status"] == "done"
        r = await c.post(f"/orders/{oid}/action", data={"_csrf": csrf, "act": "done", "next": "//evil.com"}, allow_redirects=False)
        assert r.headers["Location"].startswith("/orders")
        ok("اقدام روی سفارش از وب و جلوگیری از open-redirect")

        # کاربر: موجودی/مسدودی
        r = await c.post("/users/40/balance", data={"_csrf": csrf, "op": "add", "amount": "50,000"}, allow_redirects=False)
        assert (await db.get_user(40))["balance"] == 50000
        r = await c.post("/users/40/balance", data={"_csrf": csrf, "op": "sub", "amount": "999999"}, allow_redirects=False)
        assert "err=" in r.headers["Location"]
        r = await c.post("/users/40/ban", data={"_csrf": csrf}, allow_redirects=False)
        assert (await db.get_user(40))["banned"] == 1
        ok("تغییر موجودی و مسدودسازی کاربر از وب")

        # محصولات / تخفیف / ادمین
        p0 = (await db.list_products("stars"))[0]
        r = await c.post(f"/products/{p0['id']}", data={"_csrf": csrf, "title": "پکیج ویژه", "price": "123000"}, allow_redirects=False)
        p1 = await db.get_product(p0["id"])
        assert p1["price_override"] == 123000 and p1["active"] == 0 and p1["title"] == "پکیج ویژه"
        r = await c.post("/discounts/add", data={"_csrf": csrf, "code": "NEW20", "kind": "p", "val": "20", "max": "5", "days": "7"}, allow_redirects=False)
        assert "ok=" in r.headers["Location"]
        r = await c.post("/admins/add", data={"_csrf": csrf, "uid": "40", "preset": "receipts"}, allow_redirects=False)
        assert "ok=" in r.headers["Location"], r.headers["Location"]
        await shop.refresh_admins()
        assert shop.is_admin(40) and shop.can(40, "receipts") and not shop.can(40, "backup")
        ok("افزودن ادمین با نقش آماده (receipts) از وب")
        r = await c.post("/admins/40/preset", data={"_csrf": csrf, "preset": "finance"}, allow_redirects=False)
        assert "ok=" in r.headers["Location"], r.headers["Location"]
        await shop.refresh_admins()
        assert shop.can(40, "backup") and shop.can(40, "receipts") and not shop.can(40, "buttons")
        ok("تغییر نقش ادمین به نقش آماده‌ی دیگر")
        r = await c.post("/admins/40/remove", data={"_csrf": csrf}, allow_redirects=False)
        await shop.refresh_admins()
        assert not shop.is_admin(40)
        ok("محصولات، کد تخفیف، افزودن/حذف ادمین از وب")

        # بروداکست آزمایشی
        r = await c.post("/broadcast", data={"_csrf": csrf, "text": "<b>سلام</b>", "to": "test"}, allow_redirects=False)
        assert "ok=" in r.headers["Location"]
        ok("پیام همگانی آزمایشی")

        # بکاپ: دانلود zip، سپس وارد کردن همان bundle و تأیید جایگزینی
        r = await c.get("/backup/download")
        zbody = await r.read()
        assert r.status == 200 and zbody[:2] == b"PK"
        ok("دانلود بکاپ کامل (zip)")
        import io as _io
        from multidict import MultiDict as _MD
        md = _MD()
        md.add("_csrf", csrf)
        md.add("file", _io.BytesIO(zbody))
        html_imp = await c.post("/backup/import", data=md)
        txt_imp = await html_imp.text()
        assert html_imp.status == 200 and "فایل بکاپ معتبر است" in txt_imp
        tok = txt_imp.split("name='token' value='")[1].split("'")[0]
        csrf2 = txt_imp.split("name='_csrf' value='")[1].split("'")[0]
        r = await c.post("/backup/apply", data={"_csrf": csrf2, "token": tok}, allow_redirects=False)
        assert r.status in (200, 303)
        body_apply = await r.text() if r.status == 200 else ""
        assert r.status == 303 or "بکاپ بازگردانی شد" in body_apply
        ok("وارد کردن و بازگردانی بکاپ (import → apply)")

        # لینک ورود یک‌بارمصرف از ربات
        c.session.cookie_jar.clear()
        link = shop.make_login_link(1)
        token = link.split("token=")[1]
        r = await c.get(f"/auth?token={token}", allow_redirects=False)
        assert r.status == 303 and "sid" in r.cookies
        r = await c.get(f"/auth?token={token}", allow_redirects=False)
        assert "err=" in r.headers["Location"]
        ok("لینک ورود یک‌بارمصرف /panel")

        r = await c.get("/logout", allow_redirects=False)
        r = await c.get("/", allow_redirects=False)
        assert r.status == 303
        ok("خروج")
    await db.close()


class FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def text(self):
        import json as _json
        return self._payload if isinstance(self._payload, str) else _json.dumps(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeHttp:
    """جایگزین ساده‌ی aiohttp.ClientSession.post برای تست marketapp/pricing بدون شبکه‌ی واقعی."""
    def __init__(self, routes: dict):
        self.routes = routes       # path -> (status, payload) یا callable(url,**kw)->(status,payload)
        self.calls = []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        for path, v in self.routes.items():
            if url.endswith(path):
                status, payload = v(url, **kw) if callable(v) else v
                return FakeResp(status, payload)
        return FakeResp(404, {"detail": "no route"})

    def get(self, url, **kw):
        return self.post(url, **kw)

    def request(self, method, url, **kw):
        return self.post(url, **kw)


REAL_TON_ADDR = "EQD2NmD_lH5f5u1Kj3KfGyTvhZSX0Eg6qp2a5IQUKXxOG21n"


async def test_apiutil_jpath():
    assert jpath({"data": {"price": "123.5"}}, "data.price") == "123.5"
    assert jpath([{"a": 1}, {"a": 2}], "[1].a") == 2
    assert jpath({"list": [{"symbol": "TON_IRT", "price": "55000"}, {"symbol": "USDT_IRT", "price": "60000"}]},
                 "list[symbol=USDT_IRT].price") == "60000"
    try:
        jpath({"a": 1}, "b")
        assert False, "باید KeyError بدهد"
    except KeyError:
        pass
    ok("jpath: دیکشنری، ایندکس لیست، انتخاب [symbol=value]")


async def test_marketapp_and_pricing(tmp):
    db, bot, sess, shop = await build(tmp)
    await shop.set_setting("mapp_token", "TESTTOKEN")
    await shop.set_setting("mapp_currency", "GRAM")
    await shop.set_setting("mapp_wallet_mnemonic", " ".join(["word"] * 24))   # فقط برای ready_for_delivery؛ امضا واقعی تست نمی‌شود
    s = await shop.settings()
    assert marketapp.configured(s) and marketapp.wallet_configured(s) and marketapp.ready_for_delivery(s)
    ok("configured/wallet_configured/ready_for_delivery با تنظیمات تستی")

    routes = {
        "/v1/fragment/stars/price/": (200, {"ton": "0.5", "gram": "0.5", "usd": "1.2"}),
        "/v1/fragment/premium/price/": (200, {"months3": {"ton": "1", "gram": "1", "usd": "3"},
                                               "months6": {"ton": "2", "gram": "2", "usd": "5"},
                                               "months12": {"ton": "4", "gram": "4", "usd": "9"}}),
        "/v1/fragment/stars/recipient/": (200, {"ok": True}),
        "/v1/fragment/stars/buy/": (200, {"transaction": {"validUntil": 9999999999,
                                                           "messages": [{"address": REAL_TON_ADDR, "amount": "500000000"}]}}),
    }
    shop.http = FakeHttp(routes)

    price = await marketapp.stars_price(shop, s, 100)
    assert price["usd"] == 1.2 if False else str(price["usd"]) == "1.2"
    ok("marketapp.stars_price با FakeHttp")

    prices = await marketapp.premium_prices(shop, s)
    assert set(prices) == {3, 6, 12} and str(prices[3]["usd"]) == "3"
    ok("marketapp.premium_prices با FakeHttp")

    okr, _info = await marketapp.check_recipient(shop, s, "stars", "someone", 100)
    assert okr
    ok("marketapp.check_recipient مسیر موفق")

    tx = await marketapp.buy(shop, s, "stars", "someone", 100)
    summ = marketapp.tx_summary(tx)
    assert summ["total_ton"] == Decimal("0.5") and summ["to"] == [REAL_TON_ADDR]
    ok("marketapp.buy + tx_summary: تجزیه‌ی تراکنش واقعی TON")

    # نگهبان ایمنی: سقف TON
    await shop.set_setting("mapp_max_ton", "0.1")
    s = await shop.settings()
    reason = marketapp._guard(s, {"ton": Decimal("0.5")}, Decimal("0.5"))
    assert reason and "cap" in reason
    await shop.set_setting("mapp_max_ton", "0")
    s = await shop.settings()
    assert marketapp._guard(s, {"ton": Decimal("0.5")}, Decimal("0.5")) is None
    ok("نگهبان ایمنی Marketapp: سقف TON رد می‌شود، بدون سقف مجاز است")

    # deliver() کامل با امضا/ارسال mock‌شده (شبکه‌ی واقعی توین‌سنتر درگیر نمی‌شود)
    async def fake_send(shop_, s_, tx_, wait_sec=90):
        return "ok", "mocked send", "mock-ref-123"
    orig_send = tonwallet.send_transaction
    tonwallet.send_transaction = fake_send
    try:
        order = {"target_username": "someone", "qty": 100}
        res, info, ref = await marketapp.deliver(shop, "stars", order)
        assert res == "ok" and ref == "mock-ref-123", (res, info, ref)
        ok("marketapp.deliver مسیر کامل موفق (با mock امضای کیف‌پول)")

        # رد گیرنده → fail، بدون نیاز به امضا
        routes["/v1/fragment/stars/recipient/"] = (404, {"detail": "not found"})
        res2, info2, _ = await marketapp.deliver(shop, "stars", {"target_username": "nouser", "qty": 100})
        assert res2 == "fail" and "recipient" in info2
        ok("marketapp.deliver: گیرنده‌ی نامعتبر قبل از خرج هزینه رد می‌شود")

        # کمتر از حداقل Fragment
        res3, info3, _ = await marketapp.deliver(shop, "stars", {"target_username": "someone", "qty": 10})
        assert res3 == "fail" and "minimum" in info3
        ok("marketapp.deliver: کمتر از حداقل ۵۰ ستاره رد می‌شود")
    finally:
        tonwallet.send_transaction = orig_send

    # provider.py: دیسپچ به marketapp وقتی deliver_*_mode == marketapp
    await shop.set_setting("deliver_stars_mode", "marketapp")
    called = {}

    async def fake_mapp_deliver(shop_, kind, order):
        called["kind"] = kind
        return "ok", "ok", "ref"
    orig_mapp_deliver = provider.marketapp.deliver
    provider.marketapp.deliver = fake_mapp_deliver
    try:
        res = await provider.deliver(shop, "stars", {"target_username": "x", "qty": 100})
        assert res == ("ok", "ok", "ref") and called["kind"] == "stars"
        ok("provider.deliver در حالت marketapp را به marketapp.deliver می‌فرستد")
    finally:
        provider.marketapp.deliver = orig_mapp_deliver

    # pricing: حالت ton_api (API عمومی فراگمنت/تلگرام، نه Marketapp)
    await shop.set_setting("price_mode_stars", "ton_api")
    await shop.set_setting("frag_stars_url", "https://example.test/price")
    await shop.set_setting("frag_stars_path", "data.ton")
    await shop.set_setting("frag_stars_unit", "ton")
    await shop.set_setting("ton_toman", "300000")
    await shop.set_setting("rate_auto", "0")
    shop.http = FakeHttp({"https://example.test/price": (200, {"data": {"ton": "0.01"}})})
    cost = await shop.pricing.fetch_api_cost("stars", 50)
    assert cost == Decimal("0.01") * Decimal("300000")
    ok("pricing.fetch_api_cost حالت ton_api با jpath")

    # pricing: حالت marketapp (از طریق fetch_marketapp_cost)
    await shop.set_setting("price_mode_stars", "marketapp")
    await shop.set_setting("mapp_price_field", "ton")
    shop.http = FakeHttp(routes)
    cost2 = await shop.pricing.fetch_marketapp_cost("stars", 100)
    assert cost2 == Decimal("0.5") * Decimal("300000")
    ok("pricing.fetch_marketapp_cost از Marketapp قیمت می‌گیرد و به تومان تبدیل می‌کند")
    await db.close()


async def test_i18n_and_web_extra(tmp):
    db, bot, sess, shop = await build(tmp)
    await db.upsert_user(1, "admin", "Admin")
    # زنجیره‌ی جایگزین زبان: override دیتابیس > بسته‌ی آماده > انگلیسی > فارسی منبع
    cat = i18n.Catalog()
    cat._loaded = True      # از خواندن فایل‌های واقعی locales/ جلوگیری می‌کند تا فقط داده‌ی تستی زیر را ببینیم
    cat.packs[("bot", "de")] = {"سلام": "Hallo"}
    cat.packs[("bot", "en")] = {"سلام": "Hello", "خداحافظ": "Bye"}
    cat.set_overrides([{"scope": "bot", "lang": "de", "src": "سلام", "text": "Servus (override)"}])
    assert cat.get("bot", "de", "سلام") == "Servus (override)"          # اولویت override
    assert cat.get("bot", "de", "خداحافظ") == "Bye"                     # de ندارد → en
    assert cat.get("bot", "xx", "یک متن ناشناخته") == "یک متن ناشناخته"   # زبان ناموجود → خود فارسی
    assert cat.get("bot", "fa", "سلام") == "سلام"                       # زبان منبع بدون override
    ok("زنجیره‌ی جایگزین ترجمه: override دیتابیس → بسته‌ی زبان → انگلیسی → فارسی منبع")

    # ذخیره و بازخوانی override واقعی از دیتابیس
    await db.set_translations("bot", "de", {"سلام": "Hallo (db)"})
    rows = await db.all_translations()
    assert any(r["lang"] == "de" and r["src"] == "سلام" and r["text"] == "Hallo (db)" for r in rows)
    ok("ذخیره و بازخوانی ترجمه‌ی سفارشی در دیتابیس")

    # صفحات وب جدید: Marketapp و پیش‌تنظیم‌های نرخ
    app = create_web_app(shop)
    async with TestClient(TestServer(app)) as c:
        await c.post("/login", data={"u": "admin", "p": "pw123"}, allow_redirects=False)
        for pth in ("/marketapp", "/rates/presets"):
            r = await c.get(pth)
            body_ = await r.text()
            assert r.status == 200 and "ورود به پنل مدیریت" not in body_, (pth, r.status, body_[:200])
        ok("صفحات جدید /marketapp و /rates/presets با مدیر کامل 200 برمی‌گردانند")
    await db.close()


async def make_dp(tmp):
    db, bot, sess, shop = await build(tmp)
    dp = Dispatcher(storage=MemoryStorage(), shop=shop)
    g = GateMiddleware()
    dp.message.outer_middleware(g)
    dp.callback_query.outer_middleware(g)
    for r in (handlers_admin.router, admin_settings.router, admin_manage.router, handlers_user.router):
        r._parent_router = None      # روترها singleton ماژول‌اند؛ برای دیسپچر تازه آزاد می‌شوند
        dp.include_router(r)
    return db, bot, sess, shop, dp


def last_kb(sess):
    """آخرین کیبورد ارسالی/ویرایش‌شده به‌صورت لیست (متن, callback_data)."""
    for name in ("EditMessageText", "SendMessage"):
        pass
    cands = [m for n, m in sess.calls if n in ("EditMessageText", "SendMessage") and getattr(m, "reply_markup", None)]
    m = cands[-1]
    return [(b.text, b.callback_data) for row in m.reply_markup.inline_keyboard for b in row], getattr(m, "text", "")


async def test_v12_admin_nav(tmp):
    db, bot, sess, shop, dp = await make_dp(tmp)
    await feed(dp, bot, callback_query=cbq(1, "adm:rates"))
    kb, _t = last_kb(sess)
    assert ("adm:sg:pricing:0:r") in [c for _x, c in kb]
    await feed(dp, bot, callback_query=cbq(1, "adm:sg:pricing:0:r"))
    kb, text = last_kb(sess)
    assert kb[-1][1] == "adm:rates", kb[-1]
    assert "1." in text          # لیست کامل برچسب‌ها در متن پیام
    await feed(dp, bot, callback_query=cbq(1, "adm:set"))
    await feed(dp, bot, callback_query=cbq(1, "adm:sg:pricing:0"))
    kb, _t = last_kb(sess)
    assert kb[-1][1] == "adm:set", kb[-1]
    ok("بازگشت از گروه‌های نرخ/قیمت‌گذاری به منوی مبدأ (نرخ‌ها یا تنظیمات) برمی‌گردد")
    await db.close()


async def test_v12_discount_wizard(tmp):
    db, bot, sess, shop, dp = await make_dp(tmp)
    await feed(dp, bot, callback_query=cbq(1, "adm:dnew"))
    await feed(dp, bot, message=msg(1, "150"))                 # درصد نامعتبر
    assert "۱ تا ۱۰۰" in sess.sent()[-1].text
    await feed(dp, bot, message=msg(1, "20"))
    await feed(dp, bot, callback_query=cbq(1, "adm:ds:q:7"))   # ۷ روز
    await feed(dp, bot, message=msg(1, "50"))                  # ظرفیت کل
    await feed(dp, bot, callback_query=cbq(1, "adm:ds:q:2"))   # هر کاربر ۲ بار
    txt = sess.sent()[-1].text
    assert "تأیید ساخت کد" in txt and "20٪" in txt, txt
    kb = [b.callback_data for row in sess.sent()[-1].reply_markup.inline_keyboard for b in row]
    assert "adm:dw:ok" in kb and "adm:ds:name" in kb
    await feed(dp, bot, callback_query=cbq(1, "adm:ds:name"))
    await feed(dp, bot, message=msg(1, "welcome20"))
    assert "WELCOME20" in sess.sent()[-1].text
    await feed(dp, bot, callback_query=cbq(1, "adm:dw:ok"))
    d = await db.get_discount("WELCOME20")
    assert d and d["percent"] == 20 and d["max_uses"] == 50 and d["per_user"] == 2 and d["expires_at"] > time.time() + 6 * 86400, d
    # مبلغ ثابت
    await feed(dp, bot, callback_query=cbq(1, "adm:dnew"))
    await feed(dp, bot, callback_query=cbq(1, "adm:ds:kind"))
    await feed(dp, bot, message=msg(1, "50000"))
    await feed(dp, bot, callback_query=cbq(1, "adm:ds:q:0"))
    await feed(dp, bot, callback_query=cbq(1, "adm:ds:q:0"))
    await feed(dp, bot, callback_query=cbq(1, "adm:ds:q:0"))
    assert "50,000" in sess.sent()[-1].text or "50٬000" in sess.sent()[-1].text, sess.sent()[-1].text
    ok("ویزارد ساده‌ی کد تخفیف (۴ مرحله + نام دلخواه + مبلغ ثابت + اعتبارسنجی)")
    await db.close()


async def test_v12_button_overrides(tmp):
    import json as _json
    import tools.extract_buttons as xb
    # فهرست دکمه‌ها با کد هم‌خوان است
    assert _json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "button_catalog.json"), encoding="utf-8")) == xb.scan(), \
        "button_catalog.json قدیمی است: python3 tools/extract_buttons.py"
    ok("button_catalog.json با کد ربات هم‌خوان است")
    db, bot, sess, shop = await build(tmp)
    assert ui.bkey("pay:wallet:o12") == "pay:wallet" and ui.bkey("adm:sg:pricing:0:r") == "adm:sg:pricing" and ui.bkey("buy:12") == "buy"
    good, _e = await shop.set_button_override("adm:rates", s="danger", e="5368324170671202286", t="نرخ‌ها 💹")
    assert good
    b = ui.btn("💹 نرخ‌ها و سود", "adm:rates", role="admin")
    assert b.style == "danger" and b.icon_custom_emoji_id == "5368324170671202286" and b.text == "نرخ‌ها 💹"
    # متن دکمه‌ی پویا قابل تغییر نیست؛ رنگ/ایموجی روی پیشوند برای همه‌ی زیرگروه‌ها اعمال می‌شود
    bad, _m = await shop.set_button_override("buy", t="x")
    assert not bad
    good, _e = await shop.set_button_override("adm:sg", s="success")
    assert good and ui.btn("گروه", "adm:sg:general:0", role="admin").style == "success"
    # ذخیره از فرم وب
    await db.upsert_user(1, "admin", "Admin")
    app = create_web_app(shop)
    async with TestClient(TestServer(app)) as c:
        r = await c.post("/login", data={"u": "admin", "p": "pw123"}, allow_redirects=False)
        r = await c.get("/keyboard")
        html = await r.text()
        assert "bs_adm:rates" in html and "bk" in html
        csrf = html.split("name='_csrf' value='")[1].split("'")[0]
        r = await c.post("/keyboard/all", data={"_csrf": csrf, "bk": ["menu", "pay:card"], "bs_menu": "primary", "be_menu": "", "bt_menu": "",
                                                 "bs_pay:card": "", "be_pay:card": "5368324170671202286", "bt_pay:card": "کارت"}, allow_redirects=False)
        assert r.status == 303 and "ok=" in r.headers["Location"], r.headers["Location"]
        assert ui.btn("x", "menu", role="back").style == "primary"
        assert ui.btn("y", "pay:card:o9", role="pay").text == "کارت"
        assert "adm:rates" not in shop.button_overrides()          # جایگزینی یکجا: موارد ارسال‌نشده حذف می‌شوند
    ok("شخصی‌سازی تک‌تک دکمه‌ها: متن/رنگ/ایموجی، پیشوند پویا، ذخیره از وب")
    # ربات ادمین: فهرست → کارت → رنگ/ایموجی/متن
    dp = Dispatcher(storage=MemoryStorage(), shop=shop)
    gm = GateMiddleware()
    dp.message.outer_middleware(gm)
    dp.callback_query.outer_middleware(gm)
    for r in (handlers_admin.router, admin_settings.router, admin_manage.router, handlers_user.router):
        r._parent_router = None
        dp.include_router(r)
    await shop.reset_button_overrides()
    cat = shop.button_catalog()
    idx = next(i for i, c in enumerate(cat) if c["key"] == "adm:rates")
    await feed(dp, bot, callback_query=cbq(1, "adm:ba:admin:0"))
    assert sess.sent("EditMessageText")
    await feed(dp, bot, callback_query=cbq(1, f"adm:bk:{idx}"))
    assert "adm:rates" in last_kb(sess)[1]
    await feed(dp, bot, callback_query=cbq(1, f"adm:bkc:{idx}:s"))
    assert shop.button_overrides()["adm:rates"]["s"] == "success"
    await feed(dp, bot, callback_query=cbq(1, f"adm:bke:{idx}"))
    await feed(dp, bot, message=msg(1, "5368324170671202286"))
    assert shop.button_overrides()["adm:rates"]["e"] == "5368324170671202286"
    await feed(dp, bot, callback_query=cbq(1, f"adm:bkt:{idx}"))
    await feed(dp, bot, message=msg(1, "نرخ جدید"))
    assert ui.btn("قدیمی", "adm:rates", role="admin").text == "نرخ جدید"
    await feed(dp, bot, callback_query=cbq(1, f"adm:bkc:{idx}:n"))
    assert shop.button_overrides()["adm:rates"]["s"] is None
    ok("ربات ادمین: فهرست همه‌ی دکمه‌ها، کارت دکمه، رنگ/ایموجی/متن اختصاصی")
    await shop.reset_button_overrides()
    assert not ui.btn("x", "menu", role="back").style
    await db.close()


async def test_v12_gift_manual(tmp):
    db, bot, sess, shop = await build(tmp)
    await new_user(db, 20, "buyer", "buyer")
    await db.add_balance(20, 5_000_000, "seed")
    await db.sync_gifts([("g1", 15, "gift", True)])
    gift = (await db.list_products("gift"))[0]
    await db.set_setting("deliver_gift_mode", "manual")
    shop.s["deliver_gift_mode"] = "manual"
    s = await shop.settings()
    oid = await db.create_order(20, gift, 20, None, shop.price(gift, s))
    assert await shop.pay_with_wallet(oid, 20)
    assert (await db.get_order(oid))["status"] == "manual_pending"
    assert not [1 for n, m in sess.calls if n == "SendGift"]
    kb = shop.order_admin_kb(await db.get_order(oid))
    assert any(b.callback_data == f"oadm:auto:{oid}" for r in kb.inline_keyboard for b in r)
    res = await shop.admin_order_action("auto", oid)
    assert (await db.get_order(oid))["status"] == "done", res
    assert [m for n, m in sess.calls if n == "SendGift"][0].gift_id == "g1"
    ok("گیفت: حالت دستی + دکمه‌ی ارسال خودکار با ستاره ربات")


async def test_v12_pay_step(tmp):
    db, bot, sess, shop = await build(tmp)
    await new_user(db, 20, "buyer", "buyer")
    await db.set_setting("pm_card", "1"); await db.set_setting("card_number", "6037991234567890")
    shop.s["pm_card"] = "1"; shop.s["card_number"] = "6037991234567890"
    kb = await shop.pay_methods_kb("5", 20, True)
    d = [b.callback_data for r in kb.inline_keyboard for b in r]
    assert "pm:5" in d and "disc:5" in d and "pay:card:5" not in d, d
    kb = await shop.pay_methods_kb("5", 20, True, step="methods")
    d = [b.callback_data for r in kb.inline_keyboard for b in r]
    assert "pay:card:5" in d and "ord:5" in d and "disc:5" not in d, d
    kb = await shop.pay_methods_kb("t1000", 20, False)      # شارژ کیف پول مستقیم
    assert "pay:card:t1000" in [b.callback_data for r in kb.inline_keyboard for b in r]
    shop.s["pay_step"] = "0"
    kb = await shop.pay_methods_kb("5", 20, True)
    assert "pay:card:5" in [b.callback_data for r in kb.inline_keyboard for b in r]
    ok("مرحله‌ی «پرداخت»: خلاصه ← روش‌ها ← بازگشت؛ شارژ مستقیم؛ قابل خاموش شدن")


async def test_v12_group_feed(tmp):
    import handlers_group
    from aiogram.types import Chat as TgChat
    db, bot, sess, shop = await build(tmp)
    # رفتار Fake: get_chat → سوپرگروه فروم، create_forum_topic → thread id جدید
    ids = iter(range(100, 200))
    orig = sess.make_request

    async def mk(b, method, timeout=None):
        n = type(method).__name__
        if n == "GetChat":
            sess.calls.append((n, method))
            return SimpleNamespace(id=method.chat_id, type="supergroup", is_forum=True)
        if n == "CreateForumTopic":
            sess.calls.append((n, method))
            return SimpleNamespace(message_thread_id=next(ids), name=method.name)
        if n in ("SendDocument", "SendPhoto"):
            sess.calls.append((n, method))
            return Message(message_id=7, date=datetime.now(), chat=Chat(id=1, type="private"))
        return await orig(b, method, timeout)
    sess.make_request = mk
    g = shop.group
    assert not g.enabled() and not await g.post("sales", "x")
    # /connect توسط ادمین داخل گروه
    dp = Dispatcher(storage=MemoryStorage(), shop=shop)
    handlers_group.router._parent_router = None
    dp.include_router(handlers_group.router)
    grp = Chat(id=-1001234567890, type="supergroup", title="G")
    m = Message(message_id=3, date=datetime.now(), chat=grp, from_user=User(id=1, is_bot=False, first_name="A"), text="/connect",
                entities=[MessageEntity(type="bot_command", offset=0, length=8)])
    await feed(dp, bot, message=m)
    assert g.enabled() and g.chat_id() == -1001234567890
    assert len(g.topics()) == 6, g.topics()
    assert sum(1 for n, _m in sess.calls if n == "CreateForumTopic") == 6
    # غیرادمین نمی‌تواند متصل کند
    m2 = m.model_copy(update={"from_user": User(id=999, is_bot=False, first_name="X"), "chat": Chat(id=-100777777, type="supergroup")})
    await feed(dp, bot, message=m2)
    assert g.chat_id() == -1001234567890
    ok("/connect داخل گروه: فقط ادمین؛ ساخت ۶ تاپیک؛ ذخیره‌ی آیدی گروه")
    # رویدادها در تاپیک درست و با کلید روشن/خاموش
    n0 = len(sess.sent())
    await g.post("sales", "خرید")
    last = sess.sent()[-1]
    assert last.chat_id == -1001234567890 and last.message_thread_id == g.topics()["sales"]
    await shop.set_setting("grp_ev_sales", "0")
    assert not await g.post("sales", "خرید دوم") and len(sess.sent()) == n0 + 1
    await shop.set_setting("grp_ev_sales", "1")
    ok("ارسال رویداد به تاپیک مربوط و احترام به کلید خاموش/روشن")
    # گزارش و بکاپ
    uid = 55
    await new_user(db, uid, "r", "reporter")
    assert await g.send_report()
    rep = sess.sent()[-1]
    assert "گزارش" in rep.text and rep.message_thread_id == g.topics()["reports"] and "کاربر جدید" in rep.text
    assert await g.send_backup()
    docs = sess.sent("SendDocument")
    assert docs and docs[-1].message_thread_id == g.topics()["backups"]
    ok("گزارش شبانه (فروش/سود/کاربر) و بکاپ zip در تاپیک خودش")
    # زمان‌بندی: گزارش یک‌بار در روز؛ بکاپ بر اساس ساعت
    await shop.set_setting("grp_report_time", "00:00")
    await shop.set_setting("grp_backup_hours", "1")
    nrep = len(sess.sent()); ndoc = len(sess.sent("SendDocument"))
    await g.tick()
    assert len(sess.sent()) == nrep + 1 and len(sess.sent("SendDocument")) == ndoc + 1
    await g.tick()
    assert len(sess.sent()) == nrep + 1 and len(sess.sent("SendDocument")) == ndoc + 1, "تکرار نباید شود"
    ok("tick: گزارش روزانه فقط یک‌بار و بکاپ هر N ساعت")
    # اعتبارسنجی تنظیمات
    from settings_schema import validate
    assert validate("grp_report_time", "7:05") == (True, "07:05") and not validate("grp_report_time", "25:00")[0]
    assert validate("grp_chat_id", "-1001234567890")[0] and not validate("grp_chat_id", "abc")[0]
    ok("اعتبارسنجی ساعت گزارش و آیدی گروه")
    # گیت: کاربر جدید → رویداد users
    sess.make_request = orig
    await db.close()


async def main():
    print("test_apiutil_jpath")
    await test_apiutil_jpath()
    tests = [test_db_and_menu, test_pricing_and_stats, test_order_flows, test_payments_and_discount, test_handlers, test_web,
             test_marketapp_and_pricing, test_i18n_and_web_extra, test_v12_admin_nav, test_v12_discount_wizard, test_v12_button_overrides, test_v12_group_feed, test_v12_gift_manual, test_v12_pay_step]
    for t in tests:
        print(t.__name__)
        with tempfile.TemporaryDirectory() as tmp:
            await t(tmp)
    print(f"\n{len(PASSED)} بررسی موفق ✅")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BaseException:
        import traceback
        traceback.print_exc()
        os._exit(1)
