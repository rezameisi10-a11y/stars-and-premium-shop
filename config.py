import hashlib
import hmac
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: tuple
    db_path: str = "shop.db"
    public_url: str = ""            # آدرس عمومی سرور، مثل https://bot.example.com (برای پنل وب و کال‌بک زرین‌پال)
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    web_user: str = "admin"
    web_password: str = ""          # خالی = ورود با رمز غیرفعال؛ فقط لینک یک‌بارمصرف از داخل ربات
    web_secret: str = ""
    zarinpal_merchant: str = ""
    ton_wallet: str = ""
    toncenter_key: str = ""
    usdt_trc20_wallet: str = ""
    trongrid_key: str = ""

    @property
    def cookie_secure(self) -> bool:
        return self.public_url.startswith("https://")


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN در فایل .env تنظیم نشده است.")
    try:
        admins = tuple(int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x)
    except ValueError:
        raise SystemExit("ADMIN_IDS باید فقط شامل آیدی‌های عددی با کاما باشد.")
    if not admins:
        raise SystemExit("ADMIN_IDS در فایل .env تنظیم نشده است (آیدی عددی ادمین‌ها، با کاما جدا شود).")
    secret = os.getenv("WEB_SECRET", "").strip() or hmac.new(token.encode(), b"web-panel", hashlib.sha256).hexdigest()
    return Config(
        bot_token=token,
        admin_ids=admins,
        db_path=os.getenv("DB_PATH", "shop.db"),
        public_url=os.getenv("PUBLIC_URL", "").strip().rstrip("/"),
        web_host=os.getenv("WEB_HOST", "0.0.0.0"),
        web_port=int(os.getenv("WEB_PORT", "8080")),
        web_user=os.getenv("WEB_USER", "admin").strip() or "admin",
        web_password=os.getenv("WEB_PASSWORD", ""),
        web_secret=secret,
        zarinpal_merchant=os.getenv("ZARINPAL_MERCHANT", "").strip(),
        ton_wallet=os.getenv("TON_WALLET", "").strip(),
        toncenter_key=os.getenv("TONCENTER_KEY", "").strip(),
        usdt_trc20_wallet=os.getenv("USDT_TRC20_WALLET", "").strip(),
        trongrid_key=os.getenv("TRONGRID_KEY", "").strip(),
    )
