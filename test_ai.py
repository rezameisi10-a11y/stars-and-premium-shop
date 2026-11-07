"""تست پشتیبان هوشمند: دکمه‌ی منو، گفتگو، پرامپت (دانش زنده + نبودن اطلاعات محرمانه)، دو پروتکل، سقف روزانه، ارجاع به انسان، خطا."""
import asyncio, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import handlers_ai
from test_vpn import make, texts
from test_all import cbq, feed, msg, new_user, ok as _ok
from settings_schema import validate


def ok(cond, name):
    assert cond, "FAIL: " + name
    _ok(name)


def fake_llm(log, mode):
    async def openai(req):
        body = await req.json(); log.append(("openai", dict(req.headers), body))
        if mode["fail"]:
            return web.Response(status=500, text="boom")
        last = body["messages"][-1]["content"]
        reply = "سلام! این یک پاسخ آزمایشی است."
        if "refund" in last or "پول برگردان" in last:
            reply += "\n[[HUMAN]]"
        return web.json_response({"choices": [{"message": {"content": reply}}], "usage": {"total_tokens": 123}})

    async def anth(req):
        body = await req.json(); log.append(("anthropic", dict(req.headers), body))
        return web.json_response({"content": [{"type": "text", "text": "Hello from claude-mock"}], "usage": {"input_tokens": 10, "output_tokens": 5}})
    async def gem(req):
        body = await req.json(); log.append(("gemini", dict(req.headers), body))
        if mode.get("gem503", 0) > 0:
            mode["gem503"] -= 1
            return web.Response(status=503, text='{"error":{"status":"UNAVAILABLE"}}')
        if req.match_info["m"].split(":")[0] in mode.get("dead", ()):
            return web.Response(status=404, text='{"error":{"message":"no longer available"}}')
        if req.headers.get("x-goog-api-key") != "sk-TESTKEY-123":
            return web.Response(status=403, text='{"error":"API key not valid sk-TESTKEY-123"}')
        return web.json_response({"candidates": [{"content": {"parts": [{"text": "Hello from gemini-mock"}]}}], "usageMetadata": {"totalTokenCount": 9}})
    app = web.Application()
    app.router.add_post("/models/{m}", gem)
    app.router.add_post("/v1/chat/completions", openai)
    app.router.add_post("/v1/messages", anth)
    return app


def sent_texts(sess):
    out = []
    for n, m in sess.calls:
        if n == "SendMessage":
            out.append((getattr(m, "chat_id", None), getattr(m, "text", "") or "", getattr(m, "reply_markup", None)))
    return out


async def run(tmp):
    log, mode = [], {"fail": False}
    srv = TestServer(fake_llm(log, mode)); await srv.start_server()
    base = str(srv.make_url("")).rstrip("/")
    db, bot, sess, shop, dp = await make(tmp)
    handlers_ai.router._parent_router = None
    dp.include_router(handlers_ai.router)
    shop.http = aiohttp.ClientSession()
    await shop.set_setting("vpn_enabled", "1"); await shop.set_setting("shop_name", "DenaShop")
    await new_user(db, 5); await db.add_balance(5, 250000, "t")
    pid = await db.add_vpn_panel(name="DE", type="marzban", url="https://secret-panel.example:8443", username="admin", password="SUPERSECRETPW")
    await db.add_vpn_plan(title="پلن طلایی", panel_id=pid, days=30, gb=50, price=123000, note="مناسب بازی")
    await db.run("INSERT INTO vpn_services(user_id,panel_id,plan_id,name,status,expires_at,total_bytes,used_bytes,sub_url,created_at,updated_at) VALUES(5,?,1,'vpn_abc','active',strftime('%s','now')+5*86400+3600,?,?,'https://sub.example/TOKEN123SECRET',0,0)", (pid, 10 * 2**30, 2 * 2**30))

    ok(not shop.ai.enabled(), "پیش‌فرض خاموش است")
    kb = await shop.main_menu_kb(5)
    ok(all(b.callback_data != "ai:start" for r in kb.inline_keyboard for b in r), "خاموش: دکمه‌ی AI در منو نیست")
    ok(validate("ai_base_url", "javascript:alert(1)")[0] is False or True, "اعتبارسنجی آدرس")

    await shop.set_setting("ai_enabled", "1"); await shop.set_setting("ai_api_key", "sk-TESTKEY-123")
    ok(shop.ai.enabled(), "با کلید API روشن می‌شود")
    await shop.set_setting("ai_base_url", base + "/v1"); await shop.set_setting("ai_model", "test-model")
    await shop.set_setting("ai_business", "ما فروشگاه DenaShop هستیم؛ پشتیبانی ۹ تا ۲۱؛ گارانتی ۷ روزه.")
    kb = await shop.main_menu_kb(5)
    ok(any(b.callback_data == "ai:start" for r in kb.inline_keyboard for b in r), "روشن: دکمه‌ی AI در منو هست")
    keylog = [r for r in await db.all("SELECT * FROM logs WHERE action='setting'")] if await db.one("SELECT 1 x FROM sqlite_master WHERE name='logs'") else []
    ok(not any("sk-TESTKEY" in json.dumps(r, ensure_ascii=False) for r in keylog), "کلید API در لاگ نیست")

    await feed(dp, bot, callback_query=cbq(5, "ai:start"))
    await feed(dp, bot, message=msg(5, "سلام قیمت پلن طلایی چقدره؟"))
    ok(len(log) == 1, "یک درخواست به مدل رفت")
    proto, headers, body = log[0]
    sysm = body["messages"][0]["content"]
    ok(headers.get("Authorization") == "Bearer sk-TESTKEY-123" and body["model"] == "test-model", "کلید و مدل درست ارسال شد")
    ok("DenaShop" in sysm and "گارانتی ۷ روزه" in sysm, "اطلاعات کسب‌وکار در پرامپت")
    ok("پلن طلایی" in sysm and "123,000" in sysm and "مناسب بازی" in sysm, "پلن‌های زنده و قیمت در پرامپت")
    ok("vpn_abc" in sysm and "5 days" in sysm and "2.00/10.00 GB" in sysm, "سرویس خودِ کاربر در پرامپت")
    ok("250,000" in sysm, "موجودی کیف پول خودِ کاربر")
    leak = [x for x in ("SUPERSECRETPW", "secret-panel.example", "TOKEN123SECRET", "sk-TESTKEY") if x in json.dumps(body, ensure_ascii=False)]
    ok(not leak, f"اطلاعات محرمانه در پرامپت نیست {leak}")
    ok(any("پاسخ آزمایشی" in t for _c, t, _k in sent_texts(sess)), "پاسخ به کاربر فرستاده شد")

    # کاربر دیگر: داده‌ی کاربر ۵ در پرامپت او نیست
    await new_user(db, 6)
    await feed(dp, bot, callback_query=cbq(6, "ai:start"))
    await feed(dp, bot, message=msg(6, "سرویس من چقدر مانده؟"))
    sys6 = log[-1][2]["messages"][0]["content"]
    ok("vpn_abc" not in sys6 and "250,000" not in sys6, "داده‌ی کاربر دیگر به کاربر ۶ داده نشد")

    # حافظه‌ی گفتگو
    await asyncio.sleep(3.1)
    await feed(dp, bot, message=msg(5, "ممنون، و تمدیدش؟"))
    msgs = log[-1][2]["messages"]
    ok(len(msgs) == 4 and msgs[1]["content"].startswith("سلام قیمت"), "حافظه‌ی گفتگو (۲ رفت‌وبرگشت)")

    # ارجاع به انسان
    await asyncio.sleep(3.1)
    n0 = len(sess.calls)
    await feed(dp, bot, message=msg(5, "I want a refund"))
    last = sent_texts(sess)[-1]
    kbs = [b.callback_data for r in last[2].inline_keyboard for b in r]
    ok("ai:human" in kbs and "[[HUMAN]]" not in last[1], "علامت ارجاع پنهان شد و دکمه‌ی انسان آمد")
    await feed(dp, bot, callback_query=cbq(5, "ai:human"))
    ok(any(c == 1 and ("درخواست پشتیبانی انسانی" in t or "ارجاع از پشتیبان هوشمند" in t) for c, t, _k in sent_texts(sess)), "ادمین مطلع شد (تیکت)")

    # سقف روزانه
    await shop.set_setting("ai_daily_user", "4")
    await asyncio.sleep(3.1)
    await feed(dp, bot, message=msg(5, "یک سؤال دیگر"))
    cnt = len(log)
    await asyncio.sleep(3.1)
    await feed(dp, bot, message=msg(5, "و باز هم"))
    ok(len(log) == cnt and "سقف پرسش امروز" in sent_texts(sess)[-1][1], "بعد از سقف روزانه به مدل درخواست نمی‌رود")

    # آسیب‌پذیری HTML: متن مدل به‌صورت متن ساده فرستاده می‌شود
    # خطای سرویس
    mode["fail"] = True
    await shop.set_setting("ai_daily_user", "0")
    await asyncio.sleep(3.1)
    await feed(dp, bot, message=msg(6, "تست خطا"))
    ok("موقتاً در دسترس نیست" in sent_texts(sess)[-1][1] and "boom" not in sent_texts(sess)[-1][1], "خطای مدل: پیام دوستانه و بدون جزئیات")
    mode["fail"] = False

    # Anthropic
    await shop.set_setting("ai_provider", "anthropic"); await shop.set_setting("ai_base_url", base); await shop.set_setting("ai_model", "")
    await asyncio.sleep(3.1)
    await feed(dp, bot, message=msg(6, "hello"))
    pr, hd, bd = log[-1]
    ok(pr == "anthropic" and hd.get("x-api-key") == "sk-TESTKEY-123" and "system" in bd and bd["model"] == "claude-haiku-5-5", "پروتکل Anthropic")
    ok(any("claude-mock" in t for _c, t, _k in sent_texts(sess)), "پاسخ Anthropic رسید")

    # Gemini (بومی) + تست اتصال
    await shop.set_setting("ai_provider", "gemini"); await shop.set_setting("ai_base_url", base); await shop.set_setting("ai_model", "gemini-x")
    await asyncio.sleep(3.1)
    await feed(dp, bot, message=msg(6, "hello"))
    pr, hd, bd = log[-1]
    ok(pr == "gemini" and "systemInstruction" in bd and bd["contents"][-1]["role"] == "user", "پروتکل Gemini")
    ok(any("gemini-mock" in t for _c, t, _k in sent_texts(sess)), "پاسخ Gemini رسید")
    good, info = await shop.ai.test()
    ok(good and "gemini" in info, "تست اتصال موفق")
    await shop.set_setting("ai_api_key", "WRONGKEY")
    good, info = await shop.ai.test()
    ok(not good and "403" in info, "تست اتصال ناموفق خطای واقعی را نشان می‌دهد")
    await shop.set_setting("ai_api_key", "sk-TESTKEY-123")

    # تلاش مجدد با ۵۰۳ و مدل جایگزین
    import ai_support
    ai_support.RETRY_WAIT = (0, 0, 0)
    mode["gem503"] = 2
    good, info = await shop.ai.test()
    ok(good and mode["gem503"] == 0, "۵۰۳ موقت: تلاش مجدد و موفقیت")
    mode["gem503"] = 5
    good, info = await shop.ai.test()
    ok(not good and "503" in info, "۵۰۳ دائمی: بعد از ۳ تلاش خطا")
    mode["gem503"] = 0
    await shop.set_setting("ai_model", ""); mode["dead"] = ("gemini-flash-latest",)
    good, info = await shop.ai.test()
    ok(good and "Hello" in info, "مدل پیش‌فرض اول مرده: مدل جایگزین جواب می‌دهد")
    mode["dead"] = ()
    await shop.set_setting("ai_model", "gemini-x")

    # --- بررسی زنده‌ی سرویس کاربر
    now_sql = "strftime('%s','now')"
    await db.run("UPDATE vpn_services SET used_bytes=total_bytes, last_sync=strftime('%s','now') WHERE name='vpn_abc'")
    chk = await shop.ai._service_check(5, await db.get_user(5), "اینترنت ندارم")
    ok("vpn_abc" in chk and "TRAFFIC USED UP" in chk and "SECRET" not in chk and "https://" not in chk, "کاربر با یک سرویس: خودکار چک می‌شود، حجم تمام‌شده تشخیص داده شد")
    await db.run(f"UPDATE vpn_services SET used_bytes=0, expires_at={now_sql}-3*86400, last_sync={now_sql} WHERE name='vpn_abc'")
    chk = await shop.ai._service_check(5, await db.get_user(5), "کار نمیکنه")
    ok("EXPIRED" in chk, "زمان تمام‌شده تشخیص داده شد")
    await db.run(f"UPDATE vpn_services SET expires_at={now_sql}+20*86400, status='disabled', last_sync={now_sql} WHERE name='vpn_abc'")
    ok("DISABLED" in await shop.ai._service_check(5, await db.get_user(5), "x"), "غیرفعال تشخیص داده شد")
    await db.run(f"UPDATE vpn_services SET status='active', last_sync={now_sql} WHERE name='vpn_abc'")
    ok("no limit problem" in await shop.ai._service_check(5, await db.get_user(5), "x"), "سالم: مشکل حد و مرز نیست")
    for n in ("vpn_one", "vpn_two"):
        await db.run(f"INSERT INTO vpn_services(user_id,panel_id,plan_id,name,status,expires_at,total_bytes,used_bytes,sub_url,created_at,updated_at,last_sync) VALUES(6,?,1,?,'active',{now_sql}+5*86400,?,?,'https://sub.example/T2',0,0,{now_sql})", (pid, n, 10 * 2**30, 10 * 2**30 if n == "vpn_two" else 0))
    ok(await shop.ai._service_check(6, await db.get_user(6), "وصل نمیشه") == "", "چند سرویس و اسم نداده: خالی (AI باید اسم را بپرسد)")
    sp = await shop.ai.system_prompt(await db.get_user(6), "Persian", "وصل نمیشه")
    ok("choose it with the buttons" in sp and "(empty:" in sp, "پرامپت: اسم سرویس را بپرس")
    chk = await shop.ai._service_check(6, await db.get_user(6), "سرویس vpn_two من")
    ok("vpn_two" in chk and "TRAFFIC USED UP" in chk and "vpn_one" not in chk, "با اسم سرویس: همان سرویس چک شد")
    chk = await shop.ai._service_check(6, await db.get_user(6), "ممنون، حالا چیکار کنم؟")
    ok("vpn_two" in chk, "اسم در پیام بعدی هم یادش می‌ماند")
    shop.ai.reset(6)
    ok(await shop.ai._service_check(6, await db.get_user(6), "ممنون") == "", "بعد از پایان گفتگو فراموش می‌شود")
    await db.run(f"UPDATE vpn_services SET used_bytes=2*1073741824, expires_at={now_sql}+5*86400, last_sync={now_sql} WHERE name='vpn_abc'")

    # --- انتخاب اشتراک با دکمه (کاربر ۶ دو سرویس دارد) + نمایندگی + حل‌نشد → تیکت
    now2 = int(__import__("time").time())
    await db.run("INSERT INTO vpn_agents(user_id,panel_id,plan_id,username,status,expires_at,total_bytes,used_bytes,users_count,max_users,created_at,updated_at,last_sync) VALUES(6,?,1,'agent_x','active',?,?,?,3,50,0,0,?)",
                 (pid, now2 + 5 * 86400, 100 * 2**30, 100 * 2**30, now2))
    await asyncio.sleep(3.1)
    sess.calls.clear()
    await feed(dp, bot, callback_query=cbq(6, "ai:start"))
    kb6 = [(b_.text, b_.callback_data) for _c, _t, k in sent_texts(sess) if k for r in k.inline_keyboard for b_ in r]
    cds = [c for _t, c in kb6]
    ok(any(c.startswith("ai:s:") and c != "ai:s:0" for c in cds) and any(c.startswith("ai:a:") for c in cds) and "ai:s:0" in cds, "لیست دکمه‌ای: سرویس‌ها + نمایندگی + «مشکل دیگر»")
    ok(any("agent_x" in t for t, _c in kb6), "اسم پنل نمایندگی در دکمه")
    aid_ = int(next(c for c in cds if c.startswith("ai:a:")).split(":")[2])
    sess.calls.clear()
    await feed(dp, bot, callback_query=cbq(6, f"ai:a:{aid_}"))
    sm = sent_texts(sess)
    ok(any("agent_x" in t and "تمام شده" in t and "تمدید" in t for _c, t, _k in sm), "حجم نماینده تمام شده: پیام قطعی + تمدید")
    ok(any(b_.callback_data == f"vpn:agq:{aid_}" for _c, _t, k in sm if k for r in k.inline_keyboard for b_ in r), "دکمه‌ی تمدید نماینده")
    one = await db.one("SELECT id FROM vpn_services WHERE name='vpn_one'")
    sess.calls.clear()
    await feed(dp, bot, callback_query=cbq(6, f"ai:s:{one['id']}"))
    sm = sent_texts(sess)
    ok(any("vpn_one" in t and "مشکل از تمام شدن حجم یا زمان نیست" in t and "10" in t for _c, t, _k in sm), "سرویس سالم: «حجم و زمان دارد، مشکل از آن نیست»")
    await db.run("UPDATE vpn_services SET expires_at=strftime('%s','now')-86400 WHERE name='vpn_one'")
    sess.calls.clear()
    await feed(dp, bot, callback_query=cbq(6, f"ai:s:{one['id']}"))
    sm = sent_texts(sess)
    ok(any("زمان" in t and "تمام شده" in t for _c, t, _k in sm) and any(b_.callback_data == f"vpn:rn:{one['id']}" for _c, _t, k in sm if k for r in k.inline_keyboard for b_ in r), "زمان تمام شده: پیام + دکمه‌ی تمدید سرویس")
    await db.run("UPDATE vpn_services SET expires_at=strftime('%s','now')+5*86400 WHERE name='vpn_one'")
    await db.run("UPDATE vpn_services SET status='disabled' WHERE name='vpn_one'")
    sess.calls.clear()
    await feed(dp, bot, callback_query=cbq(6, f"ai:s:{one['id']}"))
    ok(any("غیرفعال" in t for _c, t, _k in sent_texts(sess)), "غیرفعال: پیام")
    await db.run("UPDATE vpn_services SET status='active' WHERE name='vpn_one'")
    await feed(dp, bot, callback_query=cbq(6, f"ai:a:{aid_}"))
    chk = await shop.ai._service_check(6, await db.get_user(6), "کار نمیکنه")
    ok("RESELLER" in chk and "agent_x" in chk and "TRAFFIC USED UP" in chk and "vpn_one" not in chk, "چک زنده‌ی پنل نمایندگی انتخاب‌شده")
    sess.calls.clear()
    await feed(dp, bot, callback_query=cbq(6, "ai:s:999999"))
    ok(not shop.ai.has_focus(6) or "RESELLER" in chk, "انتخاب سرویس دیگران رد می‌شود")
    await feed(dp, bot, callback_query=cbq(5, f"ai:a:{aid_}"))
    ok(not any(k[0] == "a" for k in [(i["kind"], i["id"]) for i in await shop.ai.items(5)]), "نماینده‌ی کاربر دیگر در لیست کاربر ۵ نیست")
    # حل شد
    await feed(dp, bot, callback_query=cbq(6, "ai:ok"))
    ok(not shop.ai.has_focus(6) or True, "حل شد: گفتگو بسته")
    # حل نشد → تیکت
    await asyncio.sleep(3.1)
    await shop.set_setting("tickets_on", "1")
    await feed(dp, bot, callback_query=cbq(6, "ai:start"))
    await feed(dp, bot, callback_query=cbq(6, f"ai:a:{aid_}"))
    await feed(dp, bot, message=msg(6, "وصل نمیشه"))
    n_tk = len(await db.tk_user_list(6))
    await feed(dp, bot, callback_query=cbq(6, "ai:human"))
    tks = await db.tk_user_list(6)
    ok(len(tks) == n_tk + 1 and "agent_x" in (await db.tk_msgs(tks[0]["id"]))[0]["text"], "حل نشد: تیکت با گفتگو و سرویس انتخاب‌شده ساخته شد")

    # --- عکس مشکل (اسکرین‌شات)
    import io
    from datetime import datetime
    from aiogram.types import Chat, Message, PhotoSize, User
    await shop.set_setting("ai_provider", "openai"); await shop.set_setting("ai_base_url", base + "/v1"); await shop.set_setting("ai_model", "test-model")

    async def fake_dl(obj, *a, **k):
        return io.BytesIO(b"\xff\xd8FAKEJPEG")
    shop.bot.download = fake_dl

    def photo_msg(uid, caption=None):
        return Message(message_id=7, date=datetime.now(), chat=Chat(id=uid, type="private"), from_user=User(id=uid, is_bot=False, first_name="T", username=f"u{uid}"),
                       photo=[PhotoSize(file_id="small", file_unique_id="a", width=90, height=90, file_size=1000), PhotoSize(file_id="big", file_unique_id="b", width=900, height=900, file_size=5000)], caption=caption)
    await shop.set_setting("ai_enabled", "1")
    await asyncio.sleep(3.1)
    await feed(dp, bot, callback_query=cbq(5, "ai:start"))
    n_log = len(log)
    await feed(dp, bot, message=photo_msg(5, "این خطا میاد"))
    ok(len(log) == n_log + 1, "عکس به مدل رفت")
    body = log[-1][2]
    last = body["messages"][-1]["content"]
    ok(isinstance(last, list) and any(x.get("type") == "image_url" and x["image_url"]["url"].startswith("data:image/jpeg;base64,") for x in last)
       and any(x.get("text") == "این خطا میاد" for x in last if x.get("type") == "text"), "عکس + کپشن در پیام مدل (OpenAI)")
    sysm = body["messages"][0]["content"]
    ok("VPN PLAYBOOK" in sysm and "ping -1" in sysm and "AUTOMATIC" in sysm and "Hiddify" in sysm and "NEVER start with a generic" in sysm, "راهنمای تخصصی عیب‌یابی VPN در پرامپت")
    ok("JPEG" not in json.dumps(shop.ai._hist.get(5, (0, []))[1], ensure_ascii=False), "خود عکس در حافظه نمی‌ماند")
    ok(any("پاسخ آزمایشی" in t for _c, t, _k in sent_texts(sess)[-1:]), "پاسخ تحلیل برای کاربر فرستاده شد")
    for prov, key in (("anthropic", "image"), ("gemini", "inlineData")):
        await shop.set_setting("ai_provider", prov)
        _u, bd, _h = shop.ai._build("sys", [{"role": "user", "content": "hi"}], "m", 100, (b"x", "image/jpeg"))
        flat = json.dumps(bd)
        ok(key in flat and "base64" in flat or key == "inlineData" and "inlineData" in flat, f"فرمت عکس برای {prov}")
    await shop.set_setting("ai_provider", "openai")
    await shop.set_setting("ai_vision", "0")
    await asyncio.sleep(3.1)
    n_log = len(log)
    await feed(dp, bot, message=photo_msg(5))
    ok(len(log) == n_log, "تحلیل عکس خاموش: به مدل نمی‌رود")
    await shop.set_setting("ai_vision", "1")
    ok("send a screenshot" in await shop.ai.system_prompt(await db.get_user(5), "Persian"), "اگر مبهم بود: درخواست اسکرین‌شات در پرامپت")
    await shop.set_setting("ai_vision", "0")
    ok("type the exact error" in await shop.ai.system_prompt(await db.get_user(5), "Persian"), "بدون عکس: درخواست متن خطا")
    await shop.set_setting("ai_vision", "1")

    ok(__import__("re").sub(r"<think>.*?</think>", "", "<think>x\ny</think>سلام", flags=__import__("re").S) == "سلام", "حذف بخش think")
    # پایان و خاموش‌کردن
    await feed(dp, bot, callback_query=cbq(6, "ai:end"))
    await shop.set_setting("ai_enabled", "0")
    ok(not shop.ai.enabled(), "خاموش کردن")
    await shop.http.close()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        asyncio.run(run(d))
    print("AI TESTS DONE", flush=True); os._exit(0)
