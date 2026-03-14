from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .database import create_db_and_tables
from .config import get_settings
from .scheduler import start_scheduler, shutdown_scheduler
from .bot.main import create_bot_application
from .logging_conf import setup_logging
from .services.ai_service import AIService
from .services.news_parser import NewsParser
from .api.web import router as web_router

settings = get_settings()
bot_app = None

import logging

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_app
    # Startup
    setup_logging()
    create_db_and_tables()
    
    # Start Scheduler
    start_scheduler()
    
    # Start Telegram Bot
    if settings.TELEGRAM_TOKEN:
        import asyncio as _aio
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                bot_app = create_bot_application()
                
                # Initialize Services
                bot_app.bot_data["ai_service"] = AIService()
                bot_app.bot_data["news_parser"] = NewsParser()
                await bot_app.bot_data["news_parser"].init_session()
                     
                await bot_app.initialize()
                await bot_app.start()
                
                # 清除上一次殘留的 polling 連線（解決 --reload 時的 Conflict 問題）
                await bot_app.bot.delete_webhook(drop_pending_updates=True)
                
                # Set Bot Commands (Persistent Menu)
                from telegram import BotCommand
                commands = [
                    BotCommand("start", "主選單 Menu"),
                    BotCommand("news", "財經新聞 News"),
                    BotCommand("info", "個股分析 Analysis"),
                    BotCommand("esti", "估值報告 Valuation"),
                    BotCommand("research", "AI 研究 Research"),
                    BotCommand("google_news", "Google 新聞"),
                    BotCommand("chat", "AI 聊天 Chat"),
                    BotCommand("subscribe", "訂閱新聞 Subscribe"),
                    BotCommand("unsubscribe", "取消訂閱 Unsubscribe"),
                    BotCommand("help", "幫助 Help")
                ]
                await bot_app.bot.set_my_commands(commands)
                
                await bot_app.updater.start_polling(
                    drop_pending_updates=True,
                    allowed_updates=["message", "callback_query"],
                )
                print(f"Bot started.")
                break  # Success, exit retry loop
            except Exception as e:
                logger.error(f"Telegram bot attempt {attempt}/{max_attempts} failed: {e}")
                # Clean up partial state before retry
                if bot_app:
                    try:
                        await bot_app.shutdown()
                    except Exception:
                        pass
                    bot_app = None
                if attempt < max_attempts:
                    logger.info(f"Retrying in 5 seconds...")
                    await _aio.sleep(5)
                else:
                    print("Telegram bot failed to start, continuing with web interface only.")
    
    print(f"Started {settings.APP_NAME}")
    yield
    # Shutdown
    if bot_app:
        try:
            if bot_app.bot_data.get("news_parser"):
                await bot_app.bot_data["news_parser"].close()
        except Exception as e:
            logger.warning(f"Error closing news_parser: {e}")
        try:
            if bot_app.updater and bot_app.updater.running:
                await bot_app.updater.stop()
        except Exception as e:
            logger.warning(f"Error stopping updater: {e}")
        try:
            await bot_app.stop()
        except Exception as e:
            logger.warning(f"Error stopping bot app: {e}")
        try:
            await bot_app.shutdown()
        except Exception as e:
            logger.warning(f"Error shutting down bot app: {e}")
        print("Bot stopped.")
        
    shutdown_scheduler()
    print("Shutting down...")

app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan,
    debug=settings.DEBUG
)

# Mount Static
app.mount("/static", StaticFiles(directory="analysis_bot/static"), name="static")

# Include Routers
app.include_router(web_router)

@app.get("/health")
def health_check():
    return {"status": "ok"}
