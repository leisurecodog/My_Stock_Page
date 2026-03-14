from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ConversationHandler, MessageHandler, filters
from ..config import get_settings
from .handlers import (
    start_command, help_command, info_command, esti_command, 
    news_command, news_button_handler,
    chat_command, subscribe_command, unsubscribe_command,
    watch_command,
    name_command,
    google_news_start, google_news_handle,
    research_start, research_handle, research_finish, cancel,
    menu_stock_info_start, menu_stock_esti_start, handle_ticker_info, handle_ticker_esti, menu_settings_handler,
    chat_start, chat_handle,
    ASK_RESEARCH, ASK_GOOGLE_NEWS, ASK_TICKER_INFO, ASK_TICKER_ESTI, ASK_CHAT
)
from ..services.news_parser import NewsParser
from .jobs import check_news_job

settings = get_settings()

def create_bot_application() -> Application:
    """Create and configure the Telegram Bot Application."""
    if not settings.TELEGRAM_TOKEN:
        print("Warning: TELEGRAM_TOKEN not set. Bot will not start properly.")
    
    application = (
        Application.builder()
        .token(settings.TELEGRAM_TOKEN)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(10.0)
        .build()
    )
    
    # Initialize Services
    application.bot_data["news_parser"] = NewsParser()

    # Commands
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("info", info_command))
    application.add_handler(CommandHandler("esti", esti_command))
    application.add_handler(CommandHandler("news", news_command))
    application.add_handler(CommandHandler("chat", chat_command))
    application.add_handler(CommandHandler("subscribe", subscribe_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    application.add_handler(CommandHandler("watch", watch_command))
    application.add_handler(CommandHandler("name", name_command))

    # Conversation: Google News
    google_news_conv = ConversationHandler(
        entry_points=[CommandHandler("google_news", google_news_start)],
        states={
            ASK_GOOGLE_NEWS: [MessageHandler(filters.TEXT & ~filters.COMMAND, google_news_handle)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(google_news_conv)

    # Conversation: Research
    research_conv = ConversationHandler(
        entry_points=[
            CommandHandler("research", research_start),
            MessageHandler(filters.Regex("^🔎 檔案 Summary$"), research_start)
        ],
        states={
            ASK_RESEARCH: [
                MessageHandler(filters.Document.ALL | (filters.TEXT & ~filters.COMMAND), research_handle),
                CallbackQueryHandler(research_finish, pattern="^research_done$") # If done via button if implemented, or command
            ]
        },
        fallbacks=[CommandHandler("rq", research_finish), CommandHandler("cancel", cancel)]
    )
    application.add_handler(research_conv)

    # Conversation: Analysis Flow (Menu)
    analysis_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^📊 公司介紹/分析$"), menu_stock_info_start),
            MessageHandler(filters.Regex("^📈 估值報告$"), menu_stock_esti_start)
        ],
        states={
            ASK_TICKER_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ticker_info)],
            ASK_TICKER_ESTI: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ticker_esti)]
        },
        fallbacks=[CommandHandler("cancel", cancel), MessageHandler(filters.Regex("^cancel$"), cancel)]
    )
    application.add_handler(analysis_conv)
    
    # Conversation: Google News
    google_news_conv = ConversationHandler(
        entry_points=[
            CommandHandler("google_news", google_news_start),
            MessageHandler(filters.Regex("^🔍 Google 新聞$"), google_news_start)
        ],
        states={
            ASK_GOOGLE_NEWS: [MessageHandler(filters.TEXT & ~filters.COMMAND, google_news_handle)]
        },
        fallbacks=[CommandHandler("cancel", cancel), MessageHandler(filters.Regex("^cancel$"), cancel)]
    )
    application.add_handler(google_news_conv)

    # Conversation: Chat
    chat_conv = ConversationHandler(
        entry_points=[
             MessageHandler(filters.Regex("^💬 AI 聊天$"), chat_start)
        ],
        states={
            ASK_CHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handle)]
        },
        fallbacks=[
            CommandHandler("cancel", cancel), 
            MessageHandler(filters.Regex("^(cancel|exit)$"), cancel) # Handled in chat_handle too, but fallback acts as safety
        ]
    )
    application.add_handler(chat_conv)

    # Menu Button Handlers (Stateless)
    application.add_handler(MessageHandler(filters.Regex("^📰 最新新聞$"), news_command))
    application.add_handler(MessageHandler(filters.Regex("^⚙️ 設定/訂閱$"), menu_settings_handler))
    # AI Research Button - redirect to research_start (which is entry point for another conv)
    # But research_conv entry_points currently only has CommandHandler. Need to add Regex handler there.
    
    # Callbacks
    application.add_handler(CallbackQueryHandler(news_button_handler, pattern="^news_"))

    # Jobs
    if application.job_queue:
        application.job_queue.run_repeating(check_news_job, interval=600, first=30)

    return application
