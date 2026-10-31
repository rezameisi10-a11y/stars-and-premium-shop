#!/usr/bin/env python3
"""استخراج رشته‌های قابل‌ترجمه‌ی پروژه (متن فارسی = کلید) و ساخت locales/_source.{bot,web}.json.

قواعد (به‌صورت ایستا و با AST، بدون اجرای کد):
  ربات (scope=bot):
    - tr("…")  _t("…")  say(uid, "…")  و هر N_("…") در فایل‌های BOT_N_FILES
  پنل وب (scope=web):
    - _("…")  T("…") در فایل‌های وب و هر NW_("…") در کل پروژه
    - N_("…") در فایل‌های WEB_N_FILES
  فایل service.py (وضعیت‌ها، نام روش‌های پرداخت ...) هم برای ربات و هم برای وب است.

استفاده:
    python3 tools/extract_strings.py            # نوشتن فایل‌های _source
    python3 tools/extract_strings.py --check    # فقط گزارش (کد خروج ≠ ۰ اگر مشکلی باشد)
    python3 tools/extract_strings.py --orphans  # فهرست متن‌های فارسی بدون علامت (برای بازبینی)
"""
import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERSIAN = re.compile(r"[؀-ۿ]")

WEB_FILES = ["web.py", "web_users.py", "web_shop.py", "web_marketing.py", "web_system.py", "webkit.py"]
BOT_FILES = ["handlers_user.py", "handlers_ai.py", "handlers_vpn.py", "vpn_service.py", "vpn_agent.py", "middlewares.py", "service.py", "ui.py", "pricing.py", "provider.py"]
# N_ در این فایل‌ها → ربات / وب / هر دو
N_BOT = {"db.py", "handlers_user.py"}
N_WEB = {"settings_schema.py", "ui.py", "pricing.py", "provider.py", "handlers_admin.py", "admin_common.py",
         "admin_settings.py", "admin_manage.py", "backup.py", "web.py", "web_users.py", "web_shop.py",
         "web_marketing.py", "web_system.py", "webkit.py"}
N_BOTH = {"service.py"}
SCAN = sorted(f for f in os.listdir(ROOT) if f.endswith(".py"))

BOT_CALLS = {"tr": 0, "_t": 0, "say": 1, "txt_is": 0}
WEB_CALLS = {"_": 0, "T": 0}


def parse(fn):
    with open(os.path.join(ROOT, fn), encoding="utf-8") as f:
        return ast.parse(f.read(), fn)


def const(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def callname(node):
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def collect():
    bot, web = {}, {}
    problems = []          # فراخوانی‌هایی که آرگومانشان ثابت نیست یا f-string است
    marked = set()         # شناسه‌ی (فایل، خط، ستون) ثابت‌هایی که علامت خورده‌اند

    def add(d, s, fn, ln):
        if s and s.strip():
            d.setdefault(s, f"{fn}:{ln}")

    for fn in SCAN:
        tree = parse(fn)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            nm = callname(node)
            if nm in ("N_", "NW_") and node.args:
                a = node.args[0]
                s = const(a)
                if s is None:
                    problems.append(f"{fn}:{node.lineno}: {nm}() با آرگومان غیرثابت")
                    continue
                marked.add((fn, a.lineno, a.col_offset))
                if nm == "NW_":
                    add(web, s, fn, node.lineno)
                elif fn in N_BOTH:
                    add(bot, s, fn, node.lineno); add(web, s, fn, node.lineno)
                elif fn in N_BOT:
                    add(bot, s, fn, node.lineno)
                elif fn in N_WEB:
                    add(web, s, fn, node.lineno)
                else:
                    add(bot, s, fn, node.lineno); add(web, s, fn, node.lineno)
                continue
            if nm in BOT_CALLS and isinstance(node.func, (ast.Name, ast.Attribute)) and fn != "tools":
                if fn not in BOT_FILES and nm != "say":
                    continue
                i = BOT_CALLS[nm]
                if len(node.args) > i:
                    a = node.args[i]
                    s = const(a)
                    if s is not None:
                        marked.add((fn, a.lineno, a.col_offset))
                        add(bot, s, fn, node.lineno)
                    elif isinstance(a, ast.JoinedStr):
                        problems.append(f"{fn}:{node.lineno}: {nm}(f-string) غیرقابل ترجمه")
            if fn in WEB_FILES and nm in WEB_CALLS and isinstance(node.func, ast.Name):
                if node.args:
                    a = node.args[0]
                    s = const(a)
                    if s is not None:
                        marked.add((fn, a.lineno, a.col_offset))
                        add(web, s, fn, node.lineno)
                    elif isinstance(a, ast.JoinedStr):
                        problems.append(f"{fn}:{node.lineno}: {nm}(f-string) غیرقابل ترجمه")
    return bot, web, problems, marked


def orphans(marked, files):
    """متن‌های فارسی که داخل هیچ علامتی نیستند (فقط برای بازبینی دستی)."""
    out = []
    for fn in files:
        tree = parse(fn)
        doc_lines = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body \
                    and isinstance(n.body[0], ast.Expr) and const(n.body[0].value) is not None:
                doc_lines.add(n.body[0].value.lineno)
        for n in ast.walk(tree):
            s = const(n)
            if s is None or not PERSIAN.search(s):
                continue
            if (fn, n.lineno, n.col_offset) in marked or n.lineno in doc_lines:
                continue
            out.append((fn, n.lineno, s))
    return out


def main():
    check = "--check" in sys.argv
    show_orph = "--orphans" in sys.argv
    bot, web, problems, marked = collect()
    if show_orph:
        files = sys.argv[sys.argv.index("--orphans") + 1:] or WEB_FILES + BOT_FILES
        files = [f for f in files if not f.startswith("-")] or WEB_FILES + BOT_FILES
        for fn, ln, s in orphans(marked, files):
            print(f"{fn}:{ln}: {s[:90]!r}")
        return 0
    for p in problems:
        print("⚠", p)
    print(f"bot: {len(bot)} رشته | web: {len(web)} رشته")
    if check:
        return 1 if problems else 0
    os.makedirs(os.path.join(ROOT, "locales"), exist_ok=True)
    for scope, d in (("bot", bot), ("web", web)):
        path = os.path.join(ROOT, "locales", f"_source.{scope}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(list(d), f, ensure_ascii=False, indent=1)
        print("نوشته شد:", os.path.relpath(path, ROOT))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
