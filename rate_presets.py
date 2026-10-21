"""پیش‌تنظیم‌های API قیمت تتر/تون به ریال یا دلار (بیت‌پین، نوبیتکس، والکس، تترلند، کوین‌گکو، بایننس).
این آدرس‌ها پیشنهادی‌اند؛ ممکن است سرویس‌دهنده آن‌ها را تغییر دهد — بعد از اعمال حتماً دکمه‌ی «تست API» را بزنید.
هر مورد: id → (برچسب، کلید usd|ton، url، method، body، path، unit)"""
PRESETS = {
    "nobitex_usdt": ("Nobitex — USDT/IRR", "usd", "https://api.nobitex.ir/market/stats", "POST",
                     '{"srcCurrency":"usdt","dstCurrency":"rls"}', "stats.usdt-rls.latest", "rial"),
    "nobitex_ton": ("Nobitex — TON/IRR", "ton", "https://api.nobitex.ir/market/stats", "POST",
                    '{"srcCurrency":"ton","dstCurrency":"rls"}', "stats.ton-rls.latest", "rial"),
    "bitpin_usdt": ("Bitpin — USDT/IRT", "usd", "https://api.bitpin.ir/api/v1/mkt/tickers/", "GET", "", "[symbol=USDT_IRT].price", "toman"),
    "bitpin_ton": ("Bitpin — TON/IRT", "ton", "https://api.bitpin.ir/api/v1/mkt/tickers/", "GET", "", "[symbol=TON_IRT].price", "toman"),
    "wallex_usdt": ("Wallex — USDT/TMN", "usd", "https://api.wallex.ir/v1/markets", "GET", "", "result.symbols.USDTTMN.stats.lastPrice", "toman"),
    "wallex_ton": ("Wallex — TON/TMN", "ton", "https://api.wallex.ir/v1/markets", "GET", "", "result.symbols.TONTMN.stats.lastPrice", "toman"),
    "tetherland_usdt": ("Tetherland — USDT", "usd", "https://api.tetherland.com/currencies", "GET", "", "data.currencies.USDT.price", "toman"),
    "coingecko_ton_usd": ("CoinGecko — TON/USD (با نرخ دلار تبدیل می‌شود)", "ton",
                          "https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd", "GET", "", "the-open-network.usd", "usd"),
    "binance_ton_usd": ("Binance — TON/USDT (با نرخ دلار تبدیل می‌شود)", "ton", "https://api.binance.com/api/v3/ticker/price?symbol=TONUSDT", "GET", "", "price", "usd"),
}


def preset_settings(pid: str) -> dict:
    lab, key, url, method, body, path, unit = PRESETS[pid]
    return {f"{key}_api_url": url, f"{key}_api_method": method, f"{key}_api_body": body, f"{key}_api_path": path,
            f"{key}_api_unit": unit, f"{key}_api_headers": "", "rate_auto": "1"}
