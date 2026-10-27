"""ساخت تصویر QR با پس‌زمینه (مثل میرزا): چند پس‌زمینه‌ی آماده + تصویر دلخواه ادمین. بدون Pillow همان QR ساده برگردانده می‌شود."""
import io
import os

PRESETS = {
    "ocean": ("🌊 اقیانوس", (14, 165, 233), (30, 58, 138)),
    "sunset": ("🌇 غروب", (251, 146, 60), (190, 24, 93)),
    "forest": ("🌲 جنگل", (52, 211, 153), (6, 78, 59)),
    "night": ("🌙 شب", (51, 65, 85), (2, 6, 23)),
    "rose": ("🌸 رز", (251, 113, 133), (136, 19, 55)),
    "royal": ("👑 سلطنتی", (167, 139, 250), (49, 46, 129)),
    "mint": ("🍃 نعنا", (167, 243, 208), (13, 148, 136)),
    "mono": ("⚪️ خاکستری", (229, 231, 235), (107, 114, 128)),
}
SIZE = 900


def _gradient(c1, c2):
    from PIL import Image
    n = 64                                   # گرادیان کوچک را بزرگ می‌کنیم (سریع‌تر از پیکسل‌به‌پیکسل)
    img = Image.new("RGB", (n, n))
    px = img.load()
    for y in range(n):
        for x in range(n):
            t = (x + y) / (2 * n - 2)
            px[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    return img.resize((SIZE, SIZE), Image.BICUBIC)


def plain_qr(data: str) -> bytes | None:
    try:
        import segno
    except ImportError:
        return None
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="png", scale=8, border=3, dark="#111111", light="#ffffff")
    return buf.getvalue()


def render(data: str, bg: str = "", custom_path: str = "") -> bytes | None:
    """bg: '' (ساده) | نام پیش‌فرض | 'custom' (فایل custom_path)."""
    if not bg or (bg not in PRESETS and bg != "custom"):
        return plain_qr(data)
    try:
        import segno
        from PIL import Image, ImageDraw
    except ImportError:
        return plain_qr(data)
    try:
        if bg == "custom":
            if not (custom_path and os.path.exists(custom_path)):
                return plain_qr(data)
            base = Image.open(custom_path).convert("RGB")
            w, h = base.size                                  # برش مرکزی مربع
            m = min(w, h)
            base = base.crop(((w - m) // 2, (h - m) // 2, (w - m) // 2 + m, (h - m) // 2 + m)).resize((SIZE, SIZE))
        else:
            base = _gradient(PRESETS[bg][1], PRESETS[bg][2])
        qr_buf = io.BytesIO()
        segno.make(data, error="m").save(qr_buf, kind="png", scale=10, border=1, dark="#111111", light="#ffffff")
        qr = Image.open(qr_buf).convert("RGB")
        side = 560
        qr = qr.resize((side, side), Image.NEAREST)
        pad = 36
        card = Image.new("RGBA", (side + pad * 2, side + pad * 2), (0, 0, 0, 0))
        ImageDraw.Draw(card).rounded_rectangle((0, 0, card.size[0] - 1, card.size[1] - 1), radius=44, fill=(255, 255, 255, 255))
        card.paste(qr, (pad, pad))
        base = base.convert("RGBA")
        base.alpha_composite(card, ((SIZE - card.size[0]) // 2, (SIZE - card.size[1]) // 2))
        out = io.BytesIO()
        base.convert("RGB").save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return plain_qr(data)
