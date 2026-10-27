"""تعریف مشترک تنظیمات قابل ویرایش (برای پنل وب و پنل داخل ربات).

هر گروه: (gid, عنوان, مجوز لازم, [(key, label, type, help)])
type: text | number | bool | textarea | secret | url | json | select:a|b|c
"""
import json
import re

from i18n import N_, NW_

GROUPS = [
    ("general", N_("عمومی"), "settings", [
        ("shop_name", N_("نام فروشگاه"), "text", ""),
        ("support_username", N_("یوزرنیم پشتیبانی (بدون @)"), "text", ""),
        ("maintenance", N_("حالت تعمیر (ربات برای کاربران بسته می‌شود)"), "bool", N_("ادمین‌ها همچنان دسترسی دارند")),
        ("force_join", N_("عضویت اجباری در کانال"), "bool", N_("ربات باید ادمین کانال باشد")),
        ("admin_notify_orders", N_("اعلان هر سفارش پرداخت‌شده به ادمین‌ها"), "bool", ""),
        ("default_lang", N_("زبان پیش‌فرض ربات"), "text", N_("کد زبان، مثلاً fa یا en (از بخش «زبان‌ها» می‌توانید زبان جدید اضافه کنید)")),
        ("web_default_lang", N_("زبان پیش‌فرض پنل وب"), "text", N_("کد زبان، مثلاً fa یا en")),
        ("lang_prompt", N_("نمایش انتخاب زبان به کاربر جدید"), "bool", ""),
        ("premium_emoji_enabled", N_("فعال بودن ایموجی پریمیوم روی دکمه‌ها"), "bool",
         N_("فقط وقتی کار می‌کند که مالک ربات تلگرام پریمیوم داشته باشد")),
    ]),
    ("referral", N_("زیرمجموعه‌گیری و ضدتقلب"), "settings", [
        ("referral_percent", N_("درصد پورسانت از سفارش زیرمجموعه (٪)"), "number", N_("از مبلغ هر سفارش تحویل‌شده؛ ۰ = غیرفعال")),
        ("referral_join_bonus", N_("جایزه‌ی ثابت هر زیرمجموعه‌ی تأییدشده (تومان)"), "number", N_("بعد از تأیید عضویت در کانال و گذشت مدت نگهداری؛ ۰ = غیرفعال")),
        ("referral_require_join", N_("شرط عضویت در کانال برای فعال‌شدن جایزه"), "bool",
         N_("نیاز به روشن بودن «عضویت اجباری» و ادمین بودن ربات در کانال")),
        ("referral_recheck_join", N_("بررسی دوباره‌ی عضویت هنگام واریز هر جایزه"), "bool",
         N_("اگر زیرمجموعه کانال را ترک کرده باشد جایزه داده نمی‌شود")),
        ("referral_hold_hours", N_("مدت نگهداری جایزه قبل از واریز (ساعت)"), "number", N_("۰ = فوری. مثلاً ۲۴ برای جلوگیری از تقلب")),
        ("referral_min_order", N_("حداقل مبلغ سفارش برای پورسانت (تومان)"), "number", ""),
        ("referral_max_per_day", N_("حداکثر زیرمجموعه‌ی جدید در روز برای هر معرف"), "number", N_("۰ = نامحدود")),
    ]),
    ("rates", N_("نرخ لحظه‌ای تون و دلار (API)"), "pricing", [
        ("rate_auto", N_("بروزرسانی خودکار نرخ‌ها از API"), "bool", N_("اگر خاموش باشد از نرخ‌های دستی استفاده می‌شود")),
        ("rate_interval_sec", N_("فاصله‌ی بروزرسانی (ثانیه)"), "number", N_("حداقل ۳۰")),
        ("rate_max_age_min", N_("حداکثر عمر مجاز نرخ (دقیقه)"), "number", N_("اگر API بیشتر از این خراب بماند به ادمین هشدار داده می‌شود و نرخ دستی جایگزین می‌شود")),
        ("usd_api_url", N_("آدرس API قیمت دلار/تتر"), "url", N_("مثال: آدرس هر سرویسی که قیمت را به‌صورت JSON می‌دهد")),
        ("usd_api_path", N_("مسیر مقدار در JSON (دلار)"), "text", N_("مثال: data.price یا result.0.last یا [0].price یا برای لیست‌ها [symbol=USDT_IRT].price")),
        ("usd_api_method", N_("متد درخواست API دلار"), "select:GET|POST", ""),
        ("usd_api_body", N_("بدنه‌ی درخواست POST دلار (JSON)"), "json", N_('مثال نوبیتکس: {"srcCurrency":"usdt","dstCurrency":"rls"}')),
        ("usd_api_headers", N_("هدرهای API دلار (JSON)"), "json", N_('مثال: {"Authorization":"Bearer KEY"}')),
        ("usd_api_unit", N_("واحد قیمت دلار در API"), "select:toman|rial", ""),
        ("usd_spread_percent", N_("درصد اضافه/کم روی نرخ دلار (٪)"), "number", N_("برای شبیه‌سازی کارمزد خرید؛ می‌تواند ۰ باشد")),
        ("usdt_toman", N_("نرخ دستی/پشتیبان هر دلار-تتر (تومان)"), "number", N_("وقتی API در دسترس نیست یا خاموش است استفاده می‌شود")),
        ("ton_api_url", N_("آدرس API قیمت تون"), "url", ""),
        ("ton_api_path", N_("مسیر مقدار در JSON (تون)"), "text", N_("مثال: price یا data.TON.quote.USD.price")),
        ("ton_api_method", N_("متد درخواست API تون"), "select:GET|POST", ""),
        ("ton_api_body", N_("بدنه‌ی درخواست POST تون (JSON)"), "json", ""),
        ("ton_api_headers", N_("هدرهای API تون (JSON)"), "json", ""),
        ("ton_api_unit", N_("واحد قیمت تون در API"), "select:toman|rial|usd", N_("اگر دلار باشد با نرخ دلار بالا به تومان تبدیل می‌شود")),
        ("ton_spread_percent", N_("درصد اضافه/کم روی نرخ تون (٪)"), "number", ""),
        ("ton_toman", N_("نرخ دستی/پشتیبان هر تون (تومان)"), "number", ""),
    ]),
    ("pricing", N_("قیمت‌گذاری و سود"), "pricing", [
        ("price_mode_stars", N_("روش محاسبه‌ی قیمت تمام‌شده‌ی استارز"), "select:manual|usd|ton_fixed|ton_api|marketapp",
         N_("manual = تومان هر ستاره · usd = دلار هر ستاره × نرخ دلار · ton_fixed = تون هر ستاره × نرخ تون · ton_api = قیمت از API دلخواه (فراگمنت/تلگرام) · marketapp = قیمت زنده از Marketapp (گروه «Marketapp»)")),
        ("star_buy_rate", N_("قیمت تمام‌شده هر ستاره (تومان) — حالت manual"), "number", ""),
        ("star_usd_price", N_("قیمت هر ستاره به دلار — حالت usd"), "number", N_("مثلاً 0.015")),
        ("star_ton_price", N_("قیمت هر ستاره به تون — حالت ton_fixed"), "number", N_("مثلاً 0.005")),
        ("frag_stars_url", N_("آدرس API قیمت استارز به تون (فراگمنت/تلگرام)"), "url", N_("می‌توانید {qty} را برای تعداد ستاره بگذارید")),
        ("frag_stars_method", N_("متد درخواست (استارز)"), "select:GET|POST", ""),
        ("frag_stars_headers", N_("هدرها (JSON)"), "json", ""),
        ("frag_stars_body", N_("بدنه‌ی درخواست POST (JSON، می‌تواند {qty} داشته باشد)"), "json", ""),
        ("frag_stars_path", N_("مسیر مقدار در JSON پاسخ"), "text", ""),
        ("frag_stars_unit", N_("واحد مقدار پاسخ"), "select:ton|nano|usd|toman", N_("nano = نانوتون (۱۰⁹ نانو = ۱ تون)")),
        ("price_mode_premium", N_("روش محاسبه‌ی قیمت تمام‌شده‌ی پریمیوم"), "select:stars|usd|ton_fixed|ton_api|marketapp",
         N_("stars = معادل ستاره‌ی پریمیوم (۱۰۰۰/۱۵۰۰/۲۵۰۰) × قیمت ستاره · بقیه مثل استارز")),
        ("premium_usd_3", N_("پریمیوم ۳ ماهه به دلار"), "number", ""),
        ("premium_usd_6", N_("پریمیوم ۶ ماهه به دلار"), "number", ""),
        ("premium_usd_12", N_("پریمیوم ۱۲ ماهه به دلار"), "number", ""),
        ("premium_ton_3", N_("پریمیوم ۳ ماهه به تون"), "number", ""),
        ("premium_ton_6", N_("پریمیوم ۶ ماهه به تون"), "number", ""),
        ("premium_ton_12", N_("پریمیوم ۱۲ ماهه به تون"), "number", ""),
        ("frag_premium_url", N_("آدرس API قیمت پریمیوم به تون (فراگمنت/تلگرام)"), "url", N_("می‌توانید {months} را برای تعداد ماه بگذارید")),
        ("frag_premium_method", N_("متد درخواست (پریمیوم)"), "select:GET|POST", ""),
        ("frag_premium_headers", N_("هدرها (JSON)"), "json", ""),
        ("frag_premium_body", N_("بدنه‌ی درخواست POST (JSON، می‌تواند {months} داشته باشد)"), "json", ""),
        ("frag_premium_path", N_("مسیر مقدار در JSON پاسخ"), "text", ""),
        ("frag_premium_unit", N_("واحد مقدار پاسخ"), "select:ton|nano|usd|toman", ""),
        ("price_cache_sec", N_("مدت نگهداری قیمت‌های API (ثانیه)"), "number", N_("حداقل ۳۰")),
        ("margin_percent", N_("درصد سود پایه (٪) — مخفی از کاربر"), "number", N_("کاربر فقط قیمت نهایی را می‌بیند")),
        ("margin_stars", N_("درصد سود مخصوص استارز (٪)"), "text", N_("خالی = همان سود پایه")),
        ("margin_premium", N_("درصد سود مخصوص پریمیوم (٪)"), "text", N_("خالی = همان سود پایه")),
        ("margin_gift", N_("درصد سود مخصوص گیفت (٪)"), "text", N_("خالی = همان سود پایه")),
        ("fee_fixed", N_("کارمزد ثابت هر سفارش (تومان)"), "number", N_("به قیمت نهایی اضافه می‌شود؛ مخفی از کاربر")),
        ("round_step", N_("گرد کردن قیمت نهایی به بالا تا مضرب (تومان)"), "number", N_("مثلاً ۱۰۰۰ یا ۵۰۰ یا ۱۰۰۰۰")),
        ("min_price", N_("حداقل قیمت هر محصول (تومان)"), "number", ""),
        ("price_unit", N_("واحد نمایش قیمت به کاربر"), "select:toman|rial|both", N_("rial = ۱۰ برابر تومان · both = هر دو")),
        ("show_usdt_price", N_("نمایش معادل تتر (USDT) کنار قیمت"), "bool", N_("با نرخ دلار/تتر فعلی؛ اگر نرخ در دسترس نباشد نمایش داده نمی‌شود")),
        ("show_ton_price", N_("نمایش معادل تون (TON) کنار قیمت"), "bool", N_("با نرخ تون فعلی")),
        ("pay_step", N_("مرحله‌ی «پرداخت» جدا در خرید"), "bool", N_("روشن: بعد از انتخاب گیرنده، خلاصه‌ی سفارش با کیف پول / کد تخفیف / دکمه‌ی «پرداخت» (کارت، درگاه، تون، تتر) نشان داده می‌شود · خاموش: همه‌ی روش‌ها یک‌جا")),
        ("min_topup", N_("حداقل شارژ کیف پول (تومان)"), "number", ""),
        ("max_topup", N_("حداکثر شارژ کیف پول (تومان)"), "number", ""),
    ]),
    ("delivery", N_("تحویل خودکار (استارز/پریمیوم)"), "pricing", [
        ("deliver_stars_mode", N_("روش تحویل استارز"), "select:manual|api|marketapp",
         N_("manual = صف دستی ادمین · api = ارسال خودکار از طریق API سرویس‌دهنده · marketapp = خرید خودکار از Fragment با Marketapp و کیف پول TON (گروه «Marketapp») — Bot API تلگرام امکان ارسال استارز ندارد")),
        ("deliver_premium_mode", N_("روش تحویل پریمیوم"), "select:bot|api|manual|marketapp",
         N_("bot = از موجودی ستاره ربات (Bot API) · api = سرویس‌دهنده · manual = دستی · marketapp = خرید از Fragment با Marketapp")),
        ("deliver_gift_mode", N_("روش ارسال گیفت"), "select:auto|manual",
         N_("auto = ارسال خودکار با موجودی ستاره ربات · manual = سفارش وارد صف می‌شود و ادمین دستی می‌فرستد (یا از کارت سفارش «ارسال خودکار با ستاره ربات» را می‌زند)")),
        ("prov_stars_url", N_("آدرس API ارسال استارز"), "url", N_("متغیرها: {username} {user_id} {qty} {order_id}")),
        ("prov_stars_method", N_("متد (ارسال استارز)"), "select:POST|GET", ""),
        ("prov_stars_headers", N_("هدرها (JSON)"), "json", N_('مثال: {"X-API-Key":"KEY"}')),
        ("prov_stars_body", N_("بدنه‌ی درخواست (JSON)"), "json", N_('مثال: {"username":"{username}","amount":{qty},"ref":"{order_id}"}')),
        ("prov_stars_okpath", N_("مسیر وضعیت موفقیت در پاسخ"), "text", N_("خالی = فقط کد HTTP ۲xx کافی است")),
        ("prov_stars_okvalue", N_("مقدار موفقیت"), "text", N_("مثلاً true یا ok یا success؛ خالی = هر مقدار غیرخالی/true")),
        ("prov_premium_url", N_("آدرس API ارسال پریمیوم"), "url", N_("متغیرها: {username} {user_id} {months} {order_id}")),
        ("prov_premium_method", N_("متد (ارسال پریمیوم)"), "select:POST|GET", ""),
        ("prov_premium_headers", N_("هدرها (JSON)"), "json", ""),
        ("prov_premium_body", N_("بدنه‌ی درخواست (JSON)"), "json", ""),
        ("prov_premium_okpath", N_("مسیر وضعیت موفقیت در پاسخ"), "text", ""),
        ("prov_premium_okvalue", N_("مقدار موفقیت"), "text", ""),
        ("prov_timeout", N_("مهلت پاسخ API (ثانیه)"), "number", ""),
        ("prov_balance_url", N_("آدرس API موجودی حساب سرویس‌دهنده (اختیاری)"), "url", ""),
        ("prov_balance_headers", N_("هدرهای API موجودی (JSON)"), "json", ""),
        ("prov_balance_path", N_("مسیر مقدار موجودی در JSON"), "text", ""),
        ("prov_balance_low", N_("هشدار موجودی کم سرویس‌دهنده (کمتر از)"), "number", N_("۰ = غیرفعال")),
        ("star_alert_threshold", N_("هشدار کمبود موجودی ستاره ربات (کمتر از)"), "number", N_("۰ = غیرفعال")),
    ]),
    ("marketapp", N_("Marketapp (قیمت و خرید خودکار از Fragment)"), "pricing", [
        ("mapp_token", N_("توکن API مارکت‌اپ (Authorization)"), "secret", N_("بدون پیشوند Bearer؛ از ربات/سایت Marketapp می‌گیرید")),
        ("mapp_base_url", N_("آدرس پایه‌ی API"), "url", N_("پیش‌فرض https://api.marketapp.org")),
        ("mapp_currency", N_("ارز پرداخت خرید"), "select:GRAM|TON|USDT", N_("مثل مستندات: GRAM؛ برای USDT تراکنش باید از همان کیف پولی فرستاده شود که توکن با آن ساخته شده")),
        ("mapp_price_field", N_("فیلد قیمت برای محاسبه‌ی هزینه"), "select:usd|ton", N_("usd = دلار × نرخ دلار · ton = تون × نرخ تون (نرخ‌ها از گروه «نرخ لحظه‌ای»)")),
        ("mapp_check_recipient", N_("بررسی وجود گیرنده قبل از خرید"), "bool", N_("از endpoint recipient استفاده می‌کند؛ پیشنهاد: روشن")),
        ("mapp_wallet_version", N_("نسخه‌ی کیف پول TON"), "select:v5r1|v4r2", N_("Tonkeeper جدید = v5r1؛ آدرس مشتق‌شده را در «تست کیف پول» با آدرس کیف پولتان تطبیق دهید")),
        ("mapp_wallet_mnemonic", N_("عبارت بازیابی ۲۴ کلمه‌ای کیف پول داغ"), "secret", N_("فقط یک کیف پول جدا با موجودی کم؛ کلمات با فاصله. در دیتابیس/بکاپ ذخیره می‌شود — بکاپ‌ها را خصوصی نگه دارید")),
        ("mapp_wallet_address", N_("آدرس کیف پول (اختیاری، برای تطبیق ایمن)"), "text", N_("اگر پر شود و با آدرس مشتق‌شده نخواند، هیچ تراکنشی امضا نمی‌شود")),
        ("mapp_toncenter_url", N_("آدرس API توِن‌سنتر"), "url", N_("پیش‌فرض https://toncenter.com؛ کلید از گروه «درگاه‌ها» (toncenter_key)")),
        ("mapp_max_ton", N_("سقف ایمنی مبلغ هر تراکنش (TON)"), "number", N_("تراکنش بزرگ‌تر امضا نمی‌شود؛ ۰ = بدون سقف (پیشنهاد نمی‌شود)")),
    ]),
    ("gateways", N_("درگاه‌ها و روش‌های پرداخت"), "settings", [
        ("pm_wallet", N_("پرداخت با کیف پول"), "bool", ""),
        ("pm_card", N_("کارت به کارت"), "bool", ""),
        ("pm_zarinpal", N_("درگاه بانکی (زرین‌پال)"), "bool", N_("نیاز به مرچنت‌کد و آدرس عمومی (دامنه)")),
        ("pm_ton", N_("پرداخت با TON"), "bool", ""),
        ("pm_usdt", N_("پرداخت با USDT (TRC20)"), "bool", ""),
        ("card_number", N_("شماره کارت"), "text", ""),
        ("card_owner", N_("نام صاحب کارت"), "text", ""),
        ("card_note", N_("توضیح اضافه زیر شماره کارت"), "textarea", ""),
        ("zarinpal_merchant", N_("مرچنت‌کد زرین‌پال"), "secret", ""),
        ("ton_wallet", N_("آدرس کیف پول TON"), "text", N_("کاربر باید کد پرداخت را در Comment بنویسد")),
        ("toncenter_key", N_("کلید API توِن‌سنتر (اختیاری)"), "secret", ""),
        ("usdt_wallet", N_("آدرس کیف پول USDT شبکه TRC20 (ترون)"), "text", ""),
        ("trongrid_key", N_("کلید API ترون‌گرید (اختیاری)"), "secret", ""),
        ("pay_timeout_min", N_("مهلت پرداخت (دقیقه)"), "number", N_("حداقل ۵")),
    ]),
    ("group", N_("گروه گزارش‌ها (تاپیک‌ها، گزارش شبانه، بکاپ)"), "settings", [
        ("grp_enabled", N_("فعال بودن ارسال به گروه"), "bool", N_("ابتدا ربات را به گروه اضافه و ادمین کنید، سپس داخل گروه /connect بفرستید")),
        ("grp_chat_id", N_("آیدی عددی گروه"), "text", N_("با /connect داخل گروه خودکار پر می‌شود؛ معمولاً با -100 شروع می‌شود")),
        ("grp_ev_receipts", N_("ارسال رسیدهای کارت‌به‌کارت"), "bool", N_("تاپیک «رسیدها»؛ ادمین‌ها همان‌جا تأیید/رد می‌کنند")),
        ("grp_ev_sales", N_("ارسال خریدها، تحویل سفارش و شارژ کیف پول"), "bool", N_("تاپیک «فروش»")),
        ("grp_ev_users", N_("ارسال کاربران جدید"), "bool", N_("تاپیک «کاربران جدید»")),
        ("grp_ev_alerts", N_("ارسال هشدارها و خطاها"), "bool", N_("تحویل ناموفق، موجودی کم ستاره/سرویس‌دهنده و ...")),
        ("grp_report_on", N_("گزارش شبانه"), "bool", N_("فروش، سود خالص، کاربران جدید و ... در تاپیک «گزارش شبانه»")),
        ("grp_report_time", N_("ساعت ارسال گزارش شبانه (وقت تهران)"), "text", N_("مثال: 23:55")),
        ("grp_backup_hours", N_("ارسال بکاپ هر چند ساعت یک‌بار (۰ = خاموش)"), "number", N_("فایل بکاپ در تاپیک «بکاپ» فرستاده می‌شود؛ گروه باید خصوصی باشد")),
        ("grp_backup_env", N_("همراه بکاپ فایل .env (توکن‌ها) هم ارسال شود"), "bool", N_("پیشنهاد نمی‌شود؛ توکن‌ها در اختیار اعضای گروه قرار می‌گیرد")),
    ]),
    ("miniapp", N_("مینی‌اپ تلگرام"), "settings", [
        ("miniapp_enabled", N_("فعال بودن مینی‌اپ"), "bool", N_("نیاز به آدرس عمومی HTTPS (همان PUBLIC_URL/دامنه‌ی پنل). همه‌ی کارهای کاربر در ربات داخل مینی‌اپ هم هست و ادمین‌ها با یک دکمه وارد پنل کامل مدیریت می‌شوند")),
        ("miniapp_menu_button", N_("دکمه‌ی مینی‌اپ کنار کادر پیام (Menu Button)"), "bool", N_("به‌جای دکمه‌ی پیش‌فرض منوی ربات، دکمه‌ی باز کردن مینی‌اپ نشان داده می‌شود")),
        ("miniapp_title", N_("متن دکمه‌ی مینی‌اپ"), "text", N_("مثال: 🛍 فروشگاه")),
    ]),
    ("vpn", N_("فروش VPN"), "vpn", [
        ("vpn_enabled", N_("فعال بودن فروش VPN"), "bool", N_("پنل‌ها و پلن‌ها را از «🛡 فروش VPN» در ربات یا وب‌پنل بسازید")),
        ("vpn_name_mode", N_("نحوه‌ی انتخاب نام کاربری سرویس"), "select:random|id|ask", N_("random: تصادفی · id: از روی آیدی کاربر · ask: از کاربر بپرسد")),
        ("vpn_prefix", N_("پیشوند نام کاربری سرویس‌ها"), "text", N_("حروف کوچک انگلیسی، عدد و _ ؛ مثال: vpn")),
        ("vpn_test_enabled", N_("اکانت تست رایگان"), "bool", N_("هر کاربر یک‌بار تست می‌گیرد")),
        ("vpn_test_days", N_("مدت اکانت تست (روز)"), "number", ""),
        ("vpn_test_gb", N_("حجم اکانت تست (گیگابایت)"), "number", N_("۰ = نامحدود")),
        ("vpn_test_panel", N_("شناسه‌ی پنل اکانت تست"), "number", N_("۰ = اولین پنل فعال")),
        ("vpn_stack", N_("جمع شدن مدت باقی‌مانده هنگام تمدید"), "bool", N_("روشن: روزهای مانده به مدت جدید اضافه می‌شود")),
        ("vpn_allow_addon", N_("خرید حجم/زمان اضافه توسط کاربر"), "bool", ""),
        ("vpn_allow_revoke", N_("اجازه‌ی تعویض لینک اشتراک توسط کاربر"), "bool", ""),
        ("vpn_remind_days", N_("یادآوری پایان زمان (روز مانده)"), "number", N_("۰ = خاموش")),
        ("vpn_remind_usage", N_("یادآوری پایان حجم (درصد مصرف)"), "number", N_("مثال: ۹۰ ؛ ۰ = خاموش")),
        ("vpn_autodelete_days", N_("حذف خودکار سرویس منقضی بعد از (روز)"), "number", N_("۰ = حذف نشود")),
        ("vpn_sync_min", N_("همگام‌سازی مصرف با پنل‌ها (دقیقه)"), "number", ""),
        ("vpn_notify_admin", N_("اعلان خرید/تمدید VPN به ادمین‌ها"), "bool", ""),
        ("vpn_help_text", N_("متن راهنمای اتصال (زیر کانفیگ نشان داده می‌شود)"), "textarea", N_("مثلاً لینک دانلود برنامه‌ها")),
        ("vpn_agent_enabled", N_("فروش پنل نمایندگی"), "bool", N_("با خرید، ادمین خودکار در پنل ساخته می‌شود")),
        ("vpn_agent_name_mode", N_("نام کاربری ادمین نماینده"), "select:random|ask", N_("random: تصادفی · ask: از خریدار بپرسد")),
        ("vpn_agent_prefix", N_("پیشوند نام کاربری نماینده‌ها"), "text", N_("فقط حروف کوچک انگلیسی، عدد و _ ؛ مثال: agent")),
        ("vpn_agent_remind_days", N_("یادآوری پایان زمان نماینده (روز مانده)"), "number", N_("۰ = خاموش")),
        ("vpn_agent_autodelete_days", N_("حذف خودکار نماینده‌ی منقضی بعد از (روز)"), "number", N_("۰ = حذف نشود")),
        ("vpn_agent_help_text", N_("متن راهنمای نماینده (زیر اطلاعات ورود نشان داده می‌شود)"), "textarea", N_("مثلاً آموزش استفاده از پنل")),
    ]),
    ("texts", N_("متن‌های ربات"), "settings", [
        ("welcome_text", N_("پیام خوش‌آمدگویی"), "textarea", N_("HTML ساده مجاز است: <b> <i> <code>؛ ایموجی پریمیوم هم پشتیبانی می‌شود")),
        ("rules_text", N_("متن قوانین"), "textarea", ""),
        ("support_text", N_("متن بخش پشتیبانی"), "textarea", ""),
        ("maintenance_text", N_("پیام حالت تعمیر"), "textarea", ""),
        ("join_text", N_("پیام عضویت اجباری"), "textarea", ""),
        ("success_text", N_("جمله پایانی پیام تحویل سفارش"), "text", ""),
    ]),
]

GROUP_IDS = {g[0]: g for g in GROUPS}
FIELDS = {k: (label, typ, hlp) for _, _, _, fields in GROUPS for k, label, typ, hlp in fields}
GROUP_OF = {k: gid for gid, _, _, fields in GROUPS for k, *_ in fields}
# تنظیم قدیمی تک‌کانال (از پنل جدید «کانال‌های عضویت اجباری» مدیریت می‌شود؛ هنوز معتبر است تا نصب‌های قبلی کار کنند)
FIELDS["force_channel"] = (N_("کانال عضویت اجباری"), "text", N_("مثال: @mychannel"))
GROUP_OF["force_channel"] = "general"
EDITABLE_KEYS = set(FIELDS)
TEXT_KEYS = {"welcome_text", "rules_text", "support_text", "maintenance_text", "join_text", "success_text"}

DEFAULTS = {
    "btn_ovr": "", "force_channels": "",
    "miniapp_ui": "", "miniapp_enabled": "1", "miniapp_menu_button": "1", "miniapp_title": "🛍 فروشگاه",
    "vpn_enabled": "1", "vpn_name_mode": "random", "vpn_prefix": "vpn", "vpn_test_enabled": "0", "vpn_test_days": "1",
    "vpn_test_gb": "0.5", "vpn_test_panel": "0", "vpn_stack": "1", "vpn_allow_addon": "1", "vpn_allow_revoke": "1",
    "vpn_remind_days": "3", "vpn_remind_usage": "90", "vpn_autodelete_days": "0", "vpn_sync_min": "10", "vpn_notify_admin": "1",
    "vpn_help_text": "", "vpn_agent_enabled": "1", "vpn_agent_name_mode": "random", "vpn_agent_prefix": "agent", "vpn_agent_remind_days": "3",
    "vpn_agent_autodelete_days": "0", "vpn_agent_help_text": "",
    "grp_enabled": "0", "grp_chat_id": "", "grp_topics": "", "grp_ev_receipts": "1", "grp_ev_sales": "1", "grp_ev_users": "1",
    "grp_ev_alerts": "1", "grp_report_on": "1", "grp_report_time": "23:55", "grp_backup_hours": "24", "grp_backup_env": "0",
    "grp_last_report": "", "grp_last_backup": "0",
    "default_lang": "fa", "web_default_lang": "fa", "lang_prompt": "1", "premium_emoji_enabled": "1",
    "referral_join_bonus": "0", "referral_require_join": "1", "referral_recheck_join": "1",
    "referral_hold_hours": "0", "referral_min_order": "0", "referral_max_per_day": "0",
    "rate_auto": "0", "rate_interval_sec": "120", "rate_max_age_min": "30",
    "usd_api_url": "", "usd_api_path": "", "usd_api_headers": "", "usd_api_unit": "toman", "usd_spread_percent": "0",
    "ton_api_url": "", "ton_api_path": "", "ton_api_headers": "", "ton_api_unit": "toman", "ton_spread_percent": "0",
    "price_mode_stars": "manual", "star_usd_price": "0.015", "star_ton_price": "0",
    "frag_stars_url": "", "frag_stars_method": "GET", "frag_stars_headers": "", "frag_stars_body": "",
    "frag_stars_path": "", "frag_stars_unit": "ton",
    "price_mode_premium": "stars", "premium_usd_3": "0", "premium_usd_6": "0", "premium_usd_12": "0",
    "premium_ton_3": "0", "premium_ton_6": "0", "premium_ton_12": "0",
    "frag_premium_url": "", "frag_premium_method": "GET", "frag_premium_headers": "", "frag_premium_body": "",
    "frag_premium_path": "", "frag_premium_unit": "ton", "price_cache_sec": "120",
    "margin_stars": "", "margin_premium": "", "margin_gift": "", "fee_fixed": "0", "round_step": "1000", "min_price": "1000",
    "pay_step": "1", "deliver_gift_mode": "auto", "deliver_stars_mode": "manual", "deliver_premium_mode": "bot",
    "prov_stars_url": "", "prov_stars_method": "POST", "prov_stars_headers": "", "prov_stars_body": "",
    "prov_stars_okpath": "", "prov_stars_okvalue": "",
    "prov_premium_url": "", "prov_premium_method": "POST", "prov_premium_headers": "", "prov_premium_body": "",
    "prov_premium_okpath": "", "prov_premium_okvalue": "", "prov_timeout": "30",
    "prov_balance_url": "", "prov_balance_headers": "", "prov_balance_path": "", "prov_balance_low": "0",
    "usd_api_method": "GET", "usd_api_body": "", "ton_api_method": "GET", "ton_api_body": "",
    "price_unit": "toman", "show_usdt_price": "1", "show_ton_price": "1",
    "mapp_token": "", "mapp_base_url": "https://api.marketapp.org", "mapp_currency": "GRAM", "mapp_price_field": "usd",
    "mapp_check_recipient": "1", "mapp_wallet_version": "v5r1", "mapp_wallet_mnemonic": "", "mapp_wallet_address": "",
    "mapp_toncenter_url": "https://toncenter.com", "mapp_max_ton": "50",
    "zarinpal_merchant": "", "ton_wallet": "", "toncenter_key": "", "usdt_wallet": "", "trongrid_key": "",
}

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
SAMPLE_VARS = {"username": "sample_user", "user_id": "123456789", "qty": "50", "months": "3", "order_id": "1"}
_VAR_RE = re.compile(r"\{(username|user_id|qty|months|order_id)\}")


def render_template(tpl: str, vals: dict) -> str:
    """جایگزینی فقط متغیرهای مجاز ({username} ...)؛ آکولادهای JSON دست‌نخورده می‌مانند."""
    return _VAR_RE.sub(lambda m: str(vals.get(m.group(1), "")), tpl or "")


def select_options(typ: str):
    return typ.split(":", 1)[1].split("|") if typ.startswith("select:") else []


def validate(key: str, value: str):
    """(ok, پیام خطا یا مقدار نرمال‌شده)"""
    if key not in FIELDS:
        return False, NW_("تنظیم نامعتبر است.")
    typ = FIELDS[key][1]
    value = (value or "").strip()
    if typ == "bool":
        return True, "1" if value in ("1", "true", "on", "yes") else "0"
    if typ.startswith("select:"):
        return (True, value) if value in select_options(typ) else (False, NW_("گزینه‌ی نامعتبر است."))
    if typ == "number" or key in ("margin_stars", "margin_premium", "margin_gift"):
        if typ != "number" and value == "":
            return True, ""
        v = value.translate(_DIGITS).replace(",", "").replace("٬", "").replace("٫", ".")
        try:
            f = float(v)
        except ValueError:
            return False, NW_("فقط عدد وارد کنید.")
        if f < 0 or f > 1e12:
            return False, NW_("عدد خارج از محدوده است.")
        if key in ("rate_interval_sec", "price_cache_sec") and f < 30:
            return False, NW_("حداقل ۳۰ ثانیه.")
        if key == "round_step" and f < 1:
            return False, NW_("حداقل ۱.")
        return True, str(int(f)) if f == int(f) else str(f)
    if key == "mapp_wallet_mnemonic" and value:
        words = value.lower().split()
        if len(words) != 24:
            return False, NW_("عبارت بازیابی باید دقیقاً ۲۴ کلمه باشد.")
        try:
            import tonwallet
            if tonwallet.AVAILABLE:
                from pytoniq_core.crypto.keys import mnemonic_is_valid
                if not mnemonic_is_valid(words):
                    return False, NW_("عبارت بازیابی معتبر نیست (یک کلمه اشتباه است).")
        except Exception:  # noqa: BLE001
            pass
        return True, " ".join(words)
    if key == "mapp_wallet_address" and value:
        try:
            import tonwallet
            if tonwallet.AVAILABLE:
                tonwallet.Address(value)
        except Exception:  # noqa: BLE001
            return False, NW_("آدرس TON معتبر نیست.")
    if typ == "url":
        if value and not re.match(r"^https?://", value):
            return False, NW_("آدرس باید با http:// یا https:// شروع شود.")
        return True, value
    if typ == "json":
        if value:
            try:
                json.loads(render_template(value, SAMPLE_VARS) if "{" in value else value)
            except ValueError:
                return False, NW_("JSON معتبر نیست.")
        return True, value
    if key in ("default_lang", "web_default_lang"):
        value = value.lower()
        if not re.fullmatch(r"[a-z]{2,3}(-[a-zA-Z0-9]{2,8})?", value):
            return False, NW_("کد زبان نامعتبر است (مثل fa یا en یا pt-br).")
    if key == "grp_chat_id" and value:
        import groupfeed
        cid = groupfeed.parse_chat_id(value)
        if cid is None:
            return False, NW_("آیدی گروه باید عدد باشد (مثل -1001234567890).")
        return True, str(cid)
    if key == "grp_report_time":
        import groupfeed
        m = groupfeed.HHMM.match(value.translate(_DIGITS))
        if not m:
            return False, NW_("ساعت را به‌صورت HH:MM بنویسید (مثل 23:55).")
        return True, f"{int(m.group(1)):02d}:{m.group(2)}"
    if key == "force_channel" and value:
        value = value if value.startswith("@") else "@" + value.lstrip("@")
    if key == "support_username":
        value = value.lstrip("@")
    if key == "card_number":
        value = value.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    if typ in ("text", "secret") and len(value) > 600 and key not in TEXT_KEYS:
        return False, NW_("متن خیلی طولانی است.")
    if len(value) > 3500:
        return False, NW_("متن خیلی طولانی است.")
    return True, value
