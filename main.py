import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

import admin_manage
import admin_settings
import admin_vpn
import admin_vpn_agent
import handlers_admin
import handlers_ai
import handlers_group
import handlers_user
import handlers_vpn
import ui
from config import load_config
from db import DB
from middlewares import GateMiddleware
from service import Shop
from web import create_web_app


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    db = DB(cfg.db_path)
    await db.connect()

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    # اگر تلگرام ایموجی پریمیوم دکمه‌ها را نپذیرد، خودکار بدون ایموجی دوباره ارسال می‌شود
    bot.session.middleware(ui.emoji_fallback_middleware)
    shop = Shop(bot, db, cfg)
    await shop.start()

    dp = Dispatcher(storage=MemoryStorage(), shop=shop)
    gate = GateMiddleware()
    dp.message.outer_middleware(gate)
    dp.callback_query.outer_middleware(gate)
    # اول ادمین (فرمان‌ها و استیت‌های ادمین مقدم باشند)، بعد کاربر
    dp.include_router(handlers_group.router)
    dp.include_router(handlers_admin.router)
    dp.include_router(admin_settings.router)
    dp.include_router(admin_manage.router)
    dp.include_router(admin_vpn.router)
    dp.include_router(admin_vpn_agent.router)
    dp.include_router(handlers_user.router)
    dp.include_router(handlers_vpn.router)
    dp.include_router(handlers_ai.router)

    runner = web.AppRunner(create_web_app(shop))
    await runner.setup()
    await web.TCPSite(runner, cfg.web_host, cfg.web_port).start()
    logging.info("Web panel on %s:%s (public: %s)", cfg.web_host, cfg.web_port, cfg.public_url or "-")

    bg = asyncio.create_task(shop.background_loop())
    poll = None
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        poll = asyncio.create_task(dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()))
        wait_restart = asyncio.create_task(shop.restart_event.wait())
        await asyncio.wait({poll, wait_restart}, return_when=asyncio.FIRST_COMPLETED)
        if shop.restart_requested:
            logging.warning("Restart requested (backup restore) — stopping; the service manager will start the bot again.")
            try:
                await dp.stop_polling()
            except RuntimeError:
                pass
        wait_restart.cancel()
        if not poll.done():
            await asyncio.wait_for(poll, 15)
        elif poll.exception():
            raise poll.exception()
    finally:
        if poll and not poll.done():
            poll.cancel()
        bg.cancel()
        await runner.cleanup()
        await shop.stop()
        await db.close()
        await bot.session.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
