import logging
import io
import asyncio
import re
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, ReplyKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, filters, CallbackQueryHandler

from ..services.stock_analyzer import StockAnalyzer
from ..services.ai_service import AIService, RequestType
from ..services.news_parser import NewsParser
from ..services.legacy_scraper import LegacyMoneyDJ
from ..services.report_generator import ReportGenerator
from ..services.stock_service import StockService

logger = logging.getLogger(__name__)

# Constants
MAX_ALIAS_LENGTH = 64

# Conversation states
ASK_RESEARCH = 1
ASK_GOOGLE_NEWS = 2
ASK_TICKER_INFO = 3
ASK_TICKER_ESTI = 4

ASK_CHAT = 5

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    # Main Menu Keyboard
    keyboard = [
        ["📰 最新新聞", "📊 公司介紹/分析"],
        ["📈 估值報告", "🔎 檔案 Summary"],
        ["🔍 Google 新聞", "💬 AI 聊天"],
        ["⚙️ 設定/訂閱"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        'Stock Analysis Bot Ready! 請選擇功能：', 
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_command(update, context)

# --- Core Logic Functions (Reusable) ---
async def run_info_analysis(update: Update, ticker: str):
    await update.message.reply_text(f"✅ 你輸入的代碼是 {ticker}，幫你處理！📊")
    
    # 1. Scrape MoneyDJ
    dj = LegacyMoneyDJ()
    try:
        stock_name, wiki_text = await dj.get_wiki_result(ticker)
        
        if not stock_name:
             await update.message.reply_text(f"Information of Ticker {ticker} is not found.")
             return

        # 2. AI Summary
        ai = AIService()
        condition = "近1年的公司產品、營收占比、業務來源、財務狀況(營收、eps、毛利率等)、近期pros & cons 加上 google 搜尋結果，要幫我標示來源"
        prompt = "\n" + condition  + "，並且使用繁體中文回答\n"
        
        # Call AI
        await update.message.reply_chat_action(ChatAction.TYPING)
        response = await ai.call(RequestType.TEXT, contents=wiki_text, prompt=prompt)
        
        if response:
            file_name = f"{ticker}{stock_name}_info.md"
            f = io.BytesIO(response.encode('utf-8'))
            f.name = file_name
            await update.message.reply_document(
                document=InputFile(f, filename=file_name), 
                caption="這是你的報告(含google搜尋) 📄"
            )
        else:
            await update.message.reply_text("抱歉我壞了 (AI Error)")

    except Exception as e:
        logger.error(f"Error in info_analysis: {e}")
        await update.message.reply_text("An error occurred during analysis.")

async def run_esti_analysis(update: Update, ticker: str):
    await update.message.reply_text(f"Estimate start: {ticker}")
    analyzer = StockAnalyzer()
    try:
        await update.message.reply_chat_action(ChatAction.TYPING)
        
        # Use Shared Service
        data, from_cache = await StockService.get_or_analyze_stock(ticker)
        
        if from_cache:
             # Optional: We could get timestamp from data if stored, or just generic message
             await update.message.reply_text(f"♻️ Using cached data")

        if not data or "error" in data:
            error_msg = data.get("error", "Unknown error") if data else "Unknown error"
            await update.message.reply_text(f"Error: {error_msg}")
            return

        # Use ReportGenerator with Telegram format
        report_text = ReportGenerator.generate_telegram_report(data)
        
        file_name = f"{ticker}_est.md"
        f = io.BytesIO(report_text.encode('utf-8'))
        f.name = file_name
        
        await update.message.reply_document(
            document=InputFile(f, filename=file_name),
            caption="這是你的報告📄"
        )

    except Exception as e:
        logger.error(f"Error in esti_analysis: {e}")
        await update.message.reply_text("An error occurred during valuation.")

# --- Command Handlers ---
async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get stock information using Legacy MoneyDJ + AI."""
    if not context.args:
        await update.message.reply_text("Please provide a ticker symbol (e.g., /info 2330)")
        return
    await run_info_analysis(update, context.args[0])

async def esti_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get estimation/valuation analysis."""
    if not context.args:
        await update.message.reply_text("Please provide a ticker symbol (e.g., /esti 2330)")
        return
    await run_esti_analysis(update, context.args[0])

# --- Chat ---
async def chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """One-off chat command."""
    if not context.args:
        await update.message.reply_text("Usage: /chat <message>")
        return
    
    user_msg = " ".join(context.args)
    ai = AIService()
    await update.message.reply_chat_action(ChatAction.TYPING)
    try:
        resp = await ai.call(RequestType.TEXT, contents=user_msg)
        await update.message.reply_text(resp)
    except Exception as e:
        await update.message.reply_text(f"AI Error: {e}")

async def chat_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enter persistent chat mode."""
    await update.message.reply_text("💬 進入 AI 聊天模式！\n你可以直接跟我對話，輸入 'exit' 或 'cancel' 離開。")
    return ASK_CHAT

async def chat_handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle persistent chat messages."""
    user_msg = update.message.text
    if user_msg.lower() in ["exit", "cancel"]:
        await update.message.reply_text("已退出聊天模式。")
        return ConversationHandler.END

    ai = AIService()
    await update.message.reply_chat_action(ChatAction.TYPING)
    try:
        resp = await ai.call(RequestType.TEXT, contents=user_msg)
        await update.message.reply_text(resp)
        return ASK_CHAT
    except Exception as e:
        await update.message.reply_text(f"AI Error: {e}")
        return ASK_CHAT

# --- Menu Flow Handlers ---
async def menu_stock_info_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("請輸入股票代碼 (e.g. 2330) 或是輸入 'cancel' 取消：")
    return ASK_TICKER_INFO

async def menu_stock_esti_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("請輸入股票代碼 (e.g. 2330) 進行估值分析：")
    return ASK_TICKER_ESTI

async def handle_ticker_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ticker = update.message.text.strip()
    await run_info_analysis(update, ticker)
    return ConversationHandler.END

async def handle_ticker_esti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ticker = update.message.text.strip()
    await run_esti_analysis(update, ticker)
    return ConversationHandler.END

async def menu_settings_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle settings menu button."""
    chat_id = update.effective_chat.id
    
    # Check subscription status from DB
    from ..database import engine
    from sqlmodel import Session, select
    from ..models.subscriber import Subscriber
    
    is_sub = False
    with Session(engine) as session:
        sub = session.exec(select(Subscriber).where(Subscriber.chat_id == chat_id)).first()
        if sub and sub.is_active:
            is_sub = True
    
    status_text = "✅ 已訂閱" if is_sub else "❌ 未訂閱"
    
    msg = f"""
⚙️ **設定選單**

目前的訂閱狀態：{status_text}

指令：
/subscribe - 訂閱每日通知
/unsubscribe - 取消訂閱
/watch add <ticker> - 加入自選股
/watch list - 查看自選股
    """
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


# --- News ---
async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetch latest news."""
    await update.message.reply_text("Fetching latest news... 📰")
    
    news_parser = context.bot_data.get("news_parser") or NewsParser()
    try:
        articles = await news_parser.fetch_news_list("https://api.cnyes.com/media/api/v1/newslist/category/headline")
        
        if not articles:
             await update.message.reply_text("No news found.")
             return
             
        # Send top 5
        for news in articles[:5]:
            title = news["title"]
            url = news["url"]
            await update.message.reply_text(f"📰 {title}\n{url}")
            
    except Exception as e:
        logger.error(f"News error: {e}")
        await update.message.reply_text("Failed to fetch news.")

async def news_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Placeholder if we implement pagination or categories via buttons
    pass

# --- Google News ---
async def google_news_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("請輸入關鍵字 (e.g. 台積電) 或是輸入 'cancel' 取消：")
    return ASK_GOOGLE_NEWS

async def google_news_handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    
    await update.message.reply_text(f"🔍 搜尋 Google News: {query} ...")
    
    # Use NewsParser or new GoogleNews service?
    # Let's assume NewsParser has google news capability or we implement ad-hoc.
    # Actually `NewsParser` has `fetch_news_list` which takes URL.
    # Google RSS: https://news.google.com/rss/search?q={query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant
    
    rss_url = f"https://news.google.com/rss/search?q={query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    news_parser = context.bot_data.get("news_parser") or NewsParser()
    
    try:
        articles = await news_parser.fetch_news_list(rss_url)
        if not articles:
             await update.message.reply_text("No results found.")
             return ConversationHandler.END
        
        # Send top 5
        for news in articles[:5]:
            title = news["title"]
            url = news["url"]
            await update.message.reply_text(f"📰 {title}\n{url}")
            
    except Exception as e:
        logger.error(f"Google News error: {e}")
        await update.message.reply_text("Failed to fetch Google News.")
        
    return ConversationHandler.END


# --- Research ---
async def research_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("請上傳 PDF/文字檔案，或直接輸入文字內容。結束請輸入 /rq 或按按鈕。")
    # Initialize session data
    context.user_data['research_materials'] = []
    return ASK_RESEARCH

async def research_handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Collect materials
    materials = context.user_data.get('research_materials', [])
    
    if update.message.document:
        # File
        doc = update.message.document
        file_obj = await doc.get_file()
        
        # Download to memory
        f = io.BytesIO()
        await file_obj.download_to_memory(f)
        f.seek(0)
        
        materials.append((doc.mime_type, f.read()))
        await update.message.reply_text(f"已接收檔案：{doc.file_name}")
        
    elif update.message.text:
        # Text
        materials.append(("text/plain", update.message.text))
        await update.message.reply_text("已接收文字。")
        
    context.user_data['research_materials'] = materials
    return ASK_RESEARCH

async def research_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    materials = context.user_data.get('research_materials', [])
    if not materials:
        await update.message.reply_text("沒有資料可供分析。")
        return ConversationHandler.END
        
    sent_msg = await update.message.reply_text("🧠 AI 正在閱讀並整理資料中，請稍候...")
    await update.message.reply_chat_action(ChatAction.TYPING)
    
    ai = AIService()
    try:
        # Prompt
        prompt = "根據提供的報告整理出常見投資問題、重點資訊與詳細回答，並用繁體中文回答"
        
        # Combine materials
        contents = []
        text_accum = ""
        
        for mime, data in materials:
            if mime == "text/plain":
                 if isinstance(data, str):
                     text_accum += f"\n\n{data}"
                 elif isinstance(data, bytes):
                     text_accum += f"\n\n{data.decode('utf-8', errors='ignore')}"
            else:
                 contents.append((mime, data))
        
        # If we have text, append to prompt or send as file?
        # Gemini can take text parts.
        if text_accum:
            # We can pass text as prompt extension or separate part? 
            # AIService expected 'contents' to be list of (mime, bytes).
            # If we pas text, we might need adjustments.
            prompt += f"\n\n[Attached Text Content]:\n{text_accum}"
            
        response = await ai.call(RequestType.FILE, contents=contents, prompt=prompt)
        
        if response:
             f = io.BytesIO(response.encode('utf-8'))
             f.name = "Research.md"
             await update.message.reply_document(document=InputFile(f, filename="Research.md"), reply_to_message_id=sent_msg.message_id)
        else:
             await update.message.reply_text("Analysis failed.")
             
    except Exception as e:
        logger.error(f"Research error: {e}")
        await update.message.reply_text(f"Error analyzing materials: {e}")
        
    # Clear data
    context.user_data['research_materials'] = []
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.")
    context.user_data.clear()
    return ConversationHandler.END

# --- Subscribe ---
async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    from ..database import engine
    from sqlmodel import Session, select
    from ..models.subscriber import Subscriber
    
    with Session(engine) as session:
        sub = session.exec(select(Subscriber).where(Subscriber.chat_id == chat_id)).first()
        if not sub:
            session.add(Subscriber(chat_id=chat_id))
            session.commit()
            await update.message.reply_text("✅ 已成功訂閱！\n您將會收到：\n1. 每日個股分析報告\n2. 即時重大新聞推播\n3. Podcast 摘要")
        else:
            if not sub.is_active:
                sub.is_active = True
                session.add(sub)
                session.commit()
                await update.message.reply_text("✅ 已恢復訂閱！")
            else:
                await update.message.reply_text("您已經是訂閱者囉！")

async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    from ..database import engine
    from sqlmodel import Session, select
    from ..models.subscriber import Subscriber

    with Session(engine) as session:
        sub = session.exec(select(Subscriber).where(Subscriber.chat_id == chat_id)).first()
        if sub:
            sub.is_active = False
            session.add(sub)
            session.commit()
            await update.message.reply_text("❌ 已取消訂閱。")
        else:
            await update.message.reply_text("您尚未訂閱。")


# --- Watchlist ---
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,31}$")


def _normalize_ticker(raw: str) -> str | None:
    ticker = raw.strip().upper()
    if not ticker:
        return None
    if not _TICKER_RE.fullmatch(ticker):
        return None
    return ticker


async def name_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetch and reply the company name for a ticker (best-effort)."""
    usage = "用法：/name <ticker>"
    if not getattr(context, "args", None):
        await update.message.reply_text(usage)
        return

    ticker = _normalize_ticker(str(context.args[0]))
    if not ticker:
        await update.message.reply_text("Ticker 格式不正確")
        return

    data, _from_cache = await StockService.get_or_analyze_stock(ticker)
    if not data or "error" in data:
        await update.message.reply_text(f"找不到公司名稱：{data.get('error') if isinstance(data, dict) else 'Unknown error'}")
        return

    name = data.get("name") or ticker
    await update.message.reply_text(f"公司名稱：{name}\nTicker：{ticker}")


async def watch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manage per-chat watchlist.
    Usage:
      /watch add <ticker>
      /watch remove <ticker>
      /watch list
    """
    usage = "用法：/watch add <ticker> | /watch remove <ticker> | /watch list"

    if not getattr(context, "args", None):
        await update.message.reply_text(usage)
        return

    sub = str(context.args[0]).lower()
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        await update.message.reply_text("無法取得使用者資訊，請稍後再試。")
        return

    from ..database import engine
    from sqlmodel import Session, select
    from ..models.watchlist import WatchlistEntry

    if sub == "list":
        with Session(engine) as session:
            items = session.exec(
                select(WatchlistEntry)
                .where(WatchlistEntry.chat_id == chat_id)
                .where(WatchlistEntry.user_id == user_id)
                .order_by(WatchlistEntry.ticker)
            ).all()

        if not items:
            await update.message.reply_text("目前沒有自選股")
            return

        lines = ["📌 你的自選股："]
        for i, it in enumerate(items, start=1):
            alias = f"（{it.alias}）" if it.alias else ""
            lines.append(f"{i}. {it.ticker}{alias}")
        await update.message.reply_text("\n".join(lines))
        return

    if sub not in ("add", "remove"):
        await update.message.reply_text(usage)
        return

    if len(context.args) < 2:
        await update.message.reply_text(usage)
        return

    ticker = _normalize_ticker(str(context.args[1]))
    if not ticker:
        await update.message.reply_text("Ticker 格式不正確")
        return

    alias = " ".join([str(x) for x in context.args[2:]]).strip() if len(context.args) > 2 else None
    if alias:
        alias = alias[:MAX_ALIAS_LENGTH]
    else:
        # Best-effort auto name fetch if user didn't provide alias.
        try:
            from ..database import engine
            from sqlmodel import Session, select
            from ..models.stock import StockData

            with Session(engine) as session:
                stock = session.exec(select(StockData).where(StockData.ticker == ticker)).first()
                if stock and stock.name:
                    alias = str(stock.name)[:MAX_ALIAS_LENGTH]
        except Exception:
            alias = None

        # Only do network-ish name lookup for TW numeric tickers (keeps /watch fast & stable for US tickers)
        if not alias and ticker.isdigit():
            try:
                data, _ = await StockService.get_or_analyze_stock(ticker)
                if isinstance(data, dict) and data.get("name") and data.get("name") != ticker:
                    alias = str(data["name"])[:MAX_ALIAS_LENGTH]
            except Exception:
                alias = None

    with Session(engine) as session:
        existing = session.exec(
            select(WatchlistEntry)
            .where(WatchlistEntry.chat_id == chat_id)
            .where(WatchlistEntry.user_id == user_id)
            .where(WatchlistEntry.ticker == ticker)
        ).first()

        if sub == "add":
            if existing:
                await update.message.reply_text(f"ℹ️ 已存在：{ticker}")
                return
            session.add(WatchlistEntry(chat_id=chat_id, user_id=user_id, ticker=ticker, alias=alias))
            session.commit()
            alias_suffix = f"（{alias}）" if alias else ""
            await update.message.reply_text(f"✅ 已將 {ticker}{alias_suffix} 加入自選股！")
        
        elif sub == "remove":
            if not existing:
                await update.message.reply_text(f"ℹ️ 自選股中沒有：{ticker}")
                return
            session.delete(existing)
            session.commit()
            await update.message.reply_text(f"🗑️ 已從自選股移除：{ticker}")


async def news_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle news interactive buttons (e.g. Add Watchlist)."""
    query = update.callback_query
    await query.answer() # Acknowledge interaction immediately

    try:
        data = query.data
        if not data.startswith("NA|"):
            return

        # Format: NA|ACTION|PAYLOAD
        parts = data.split("|")
        if len(parts) < 3:
            return
        
        action = parts[1]
        payload = parts[2]
        
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        if action == "ADD":
            ticker = _normalize_ticker(payload)
            if not ticker:
                 await query.answer("無效的代碼", show_alert=True)
                 return

            from ..database import engine
            from sqlmodel import Session, select
            from ..models.watchlist import WatchlistEntry
            
            with Session(engine) as session:
                # Check if already exists
                existing = session.exec(
                    select(WatchlistEntry)
                    .where(WatchlistEntry.chat_id == chat_id)
                    .where(WatchlistEntry.user_id == user_id)
                    .where(WatchlistEntry.ticker == ticker)
                ).first()
                
                if existing:
                    await query.answer(f"ℹ️ {ticker} 已經在自選股清單中囉！", show_alert=False)
                    return
                
                # Add
                # Try to fetch alias if possible? Or leave blank.
                # Since this is quick action, leave alias blank or use ticker.
                session.add(WatchlistEntry(chat_id=chat_id, user_id=user_id, ticker=ticker))
                session.commit()
                
                await query.answer(f"✅ 已將 {ticker} 加入自選股！", show_alert=False)

    except Exception as e:
        logger.error(f"Error in news_action_handler: {e}")
        await query.answer("發生錯誤，請稍後再試", show_alert=True)
