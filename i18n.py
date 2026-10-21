"""چندزبانگی: ترجمه‌ی رشته‌ها به سبک gettext با «متن فارسی» به‌عنوان کلید.

- ربات (scope=bot) و پنل وب (scope=web) هرکدام کاتالوگ جدا دارند.
- بسته‌های آماده در locales/<lang>.<scope>.json (کلید = متن فارسی منبع).
- ادمین از پنل وب/ربات هر زبان جدیدی اضافه و هر ترجمه را ویرایش می‌کند (جدول translations در دیتابیس)؛
  ویرایش‌های دیتابیس بر بسته‌های آماده اولویت دارند.
- زنجیره‌ی جایگزین: زبان انتخابی ← انگلیسی ← متن فارسی منبع.
"""
import contextvars
import json
import logging
import os

log = logging.getLogger("i18n")

LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")
SRC_LANG = "fa"

# (code, نام بومی, پرچم, راست‌به‌چپ)
BUILTIN_LANGS = [
    ("fa", "فارسی", "🇮🇷", 1), ("en", "English", "🇬🇧", 0), ("ar", "العربية", "🇸🇦", 1),
    ("ru", "Русский", "🇷🇺", 0), ("tr", "Türkçe", "🇹🇷", 0), ("es", "Español", "🇪🇸", 0),
    ("de", "Deutsch", "🇩🇪", 0), ("fr", "Français", "🇫🇷", 0), ("hi", "हिन्दी", "🇮🇳", 0),
    ("id", "Bahasa Indonesia", "🇮🇩", 0), ("zh", "中文", "🇨🇳", 0),
]


def N_(s):
    """علامت‌گذاری رشته برای استخراج (بدون ترجمه در لحظه‌ی تعریف)."""
    return s


def NW_(s):
    """علامت‌گذاری رشته‌ای که فقط در کاتالوگ پنل وب لازم است (پیام‌های مدیریتی)."""
    return s


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _fmt(text, kw):
    if not kw:
        return text
    try:
        return text.format_map(_Safe(kw))
    except (ValueError, IndexError, KeyError):
        return text


class Catalog:
    def __init__(self):
        self.packs: dict[tuple[str, str], dict[str, str]] = {}
        self.over: dict[tuple[str, str], dict[str, str]] = {}
        self.sources: dict[str, list[str]] = {"bot": [], "web": []}
        self._loaded = False

    # ---------- بارگذاری ----------
    def load(self):
        if self._loaded:
            return
        self._loaded = True
        # پوشه‌ی locales؛ و (برای آپلودهای تخت‌شده در گیت‌هاب) خود پوشه‌ی برنامه
        app_dir = os.path.dirname(LOCALE_DIR)
        files = []
        for d in (app_dir, LOCALE_DIR):
            if os.path.isdir(d):
                files += [(fn, os.path.join(d, fn)) for fn in sorted(os.listdir(d))]
        for fn, path in files:
            if not fn.endswith(".json"):
                continue
            base = fn[:-5]
            if not (base.startswith("_source.") or base.split(".")[-1] in ("bot", "web")):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError) as e:
                log.warning("locale %s unreadable: %s", fn, e)
                continue
            if base.startswith("_source."):
                self.sources[base.split(".", 1)[1]] = list(data)
                continue
            try:
                lang, scope = base.split(".", 1)
            except ValueError:
                continue
            if scope in ("bot", "web") and isinstance(data, dict):
                self.packs[(scope, lang)] = {k: v for k, v in data.items() if isinstance(v, str) and v}

    def set_overrides(self, rows):
        """rows: لیست dict با کلیدهای scope, lang, src, text (از دیتابیس)."""
        over: dict[tuple[str, str], dict[str, str]] = {}
        for r in rows:
            if r["text"]:
                over.setdefault((r["scope"], r["lang"]), {})[r["src"]] = r["text"]
        self.over = over

    # ---------- جستجو ----------
    def lookup(self, scope, lang, src):
        """ترجمه‌ی ثبت‌شده برای دقیقاً همین زبان (بدون جایگزین)؛ وگرنه None."""
        v = self.over.get((scope, lang), {}).get(src)
        if v is None and lang != SRC_LANG:
            v = self.packs.get((scope, lang), {}).get(src)
        return v

    def get(self, scope, lang, src):
        self.load()
        if lang == SRC_LANG:
            return self.over.get((scope, lang), {}).get(src, src)
        for lg in (lang, "en"):
            v = self.lookup(scope, lg, src)
            if v:
                return v
        return src

    def tr(self, scope, lang, src, **kw):
        txt = self.get(scope, lang, src)
        if kw:
            try:
                return txt.format_map(_Safe(kw))
            except (ValueError, IndexError, KeyError):
                return _fmt(src, kw)
        return txt

    def translator(self, scope, lang):
        def _t(src, **kw):
            return self.tr(scope, lang, src, **kw)
        _t.lang = lang
        return _t

    def variants(self, scope, src):
        """همه‌ی ترجمه‌های موجود یک رشته (برای تطبیق متن دکمه‌های کیبورد پاسخ در هر زبان)."""
        self.load()
        out = {src}
        for (sc, _lg), d in list(self.packs.items()) + list(self.over.items()):
            if sc == scope and src in d:
                out.add(d[src])
        return out

    def coverage(self, scope, lang):
        """(ترجمه‌شده, کل) برای نمایش درصد پیشرفت در پنل."""
        self.load()
        srcs = self.sources.get(scope, [])
        if lang == SRC_LANG:
            return len(srcs), len(srcs)
        done = sum(1 for s in srcs if self.lookup(scope, lang, s))
        return done, len(srcs)


CATALOG = Catalog()

# ---------- پنل وب: زبان درخواست جاری ----------
_web_lang: contextvars.ContextVar[str] = contextvars.ContextVar("web_lang", default=SRC_LANG)


def set_web_lang(lang: str):
    return _web_lang.set(lang)


def cur_web_lang() -> str:
    return _web_lang.get()


def _(src, **kw):
    """ترجمه برای پنل وب بر اساس زبان درخواست جاری."""
    return CATALOG.tr("web", _web_lang.get(), src, **kw)


def bot_t(lang):
    return CATALOG.translator("bot", lang or SRC_LANG)


def valid_lang_code(code: str) -> bool:
    code = (code or "").strip()
    return 2 <= len(code) <= 10 and all(c.isalnum() or c in "-_" for c in code)
