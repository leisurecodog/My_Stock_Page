import logging
import io
import re
from datetime import datetime
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    ReplyKeyboardMarkup,
)
from telegram.constants import ParseMode, ChatAction
from telegram.ext import ContextTypes, ConversationHandler

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
        ["⚙️ 設定/訂閱"],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        "Stock Analysis Bot Ready! 請選擇功能：", reply_markup=reply_markup
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
            await update.message.reply_text(
                f"Information of Ticker {ticker} is not found."
            )
            return

        # 2. AI Summary
        ai = AIService()
        condition = "近1年的公司產品、營收占比、業務來源、財務狀況(營收、eps、毛利率等)、近期pros & cons 加上 google 搜尋結果，要幫我標示來源"
        prompt = "\n" + condition + "，並且使用繁體中文回答\n"

        # Call AI
        await update.message.reply_chat_action(ChatAction.TYPING)
        response = await ai.call(
            RequestType.TEXT, contents=wiki_text, prompt=prompt, use_search=True
        )

        if response:
            file_name = f"{ticker}{stock_name}_info.md"
            f = io.BytesIO(response.encode("utf-8"))
            f.name = file_name
            await update.message.reply_document(
                document=InputFile(f, filename=file_name),
                caption="這是你的報告(含google搜尋) 📄",
            )
        else:
            await update.message.reply_text("抱歉我壞了 (AI Error)")

    except Exception as e:
        logger.error(f"Error in info_analysis: {e}")
        await update.message.reply_text("An error occurred during analysis.")


async def run_esti_analysis(update: Update, ticker: str):
    await update.message.reply_text(f"Estimate start: {ticker}")
    # StockAnalyzer not needed here, analysis goes through AIService
    try:
        await update.message.reply_chat_action(ChatAction.TYPING)

        # Use Shared Service
        data, from_cache = await StockService.get_or_analyze_stock(ticker)

        if from_cache:
            # Optional: We could get timestamp from data if stored, or just generic message
            await update.message.reply_text("♻️ Using cached data")

        if not data or "error" in data:
            error_msg = data.get("error", "Unknown error") if data else "Unknown error"
            await update.message.reply_text(f"Error: {error_msg}")
            return

        # Use ReportGenerator with Telegram format
        report_text = ReportGenerator.generate_telegram_report(data)

        file_name = f"{ticker}_est.md"
        f = io.BytesIO(report_text.encode("utf-8"))
        f.name = file_name

        await update.message.reply_document(
            document=InputFile(f, filename=file_name), caption="這是你的報告📄"
        )

    except Exception as e:
        logger.error(f"Error in esti_analysis: {e}")
        await update.message.reply_text("An error occurred during valuation.")


# --- Command Handlers ---
async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get stock information using Legacy MoneyDJ + AI."""
    if not context.args:
        await update.message.reply_text(
            "Please provide a ticker symbol (e.g., /info 2330)"
        )
        return
    await run_info_analysis(update, context.args[0])


async def esti_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get estimation/valuation analysis."""
    if not context.args:
        await update.message.reply_text(
            "Please provide a ticker symbol (e.g., /esti 2330)"
        )
        return
    await run_esti_analysis(update, context.args[0])


ASK_CHAT = 5


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
        resp = await ai.call(RequestType.TEXT, contents=user_msg, use_search=True, force_provider="gemini")
        await update.message.reply_text(resp)
    except Exception as e:
        await update.message.reply_text(f"AI Error: {e}")


async def chat_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enter persistent chat mode."""
    await update.message.reply_text(
        "💬 進入 AI 聊天模式！\n你可以直接跟我對話，輸入 'exit' 或 'cancel' 離開。"
    )
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
        resp = await ai.call(RequestType.TEXT, contents=user_msg, use_search=True, force_provider="gemini")
        await update.message.reply_text(resp)
        return ASK_CHAT
    except Exception as e:
        await update.message.reply_text(f"AI Error: {e}")
        return ASK_CHAT


# --- Menu Flow Handlers ---
async def menu_stock_info_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "請輸入股票代碼 (e.g. 2330) 或是輸入 'cancel' 取消："
    )
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
        sub = session.exec(
            select(Subscriber).where(Subscriber.chat_id == chat_id)
        ).first()
        if sub and sub.is_active:
            is_sub = True

    msg = (
        "⚙️ **設定與訂閱**\n\n"
        "目前狀態：\n"
        f"- 訂閱新聞：{'✅ 已訂閱' if is_sub else '❌ 未訂閱'}\n\n"
        "指令：\n"
        "/subscribe - 訂閱推播\n"
        "/unsubscribe - 取消訂閱\n"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


# --- News ---
async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Retrieve Parser from bot_data (injected in main.py)
    news_parser: NewsParser = context.bot_data.get("news_parser")
    if not news_parser:
        # Fallback if not injected, though it should be
        news_parser = NewsParser()

    # Expanded News Menu
    keyboard = [
        [
            InlineKeyboardButton("鉅亨網 (CNYES)", callback_data="news_cnyes"),
            InlineKeyboardButton("Google News (TW)", callback_data="news_google"),
        ],
        [
            InlineKeyboardButton("Moneydj", callback_data="news_moneydj"),
            InlineKeyboardButton("Yahoo 股市", callback_data="news_yahoo"),
        ],
        [
            InlineKeyboardButton("聯合新聞網 (UDN)", callback_data="news_udn"),
            InlineKeyboardButton("UAnalyze", callback_data="news_uanalyze"),
        ],
        [
            InlineKeyboardButton("財經M平方", callback_data="news_macromicro"),
            InlineKeyboardButton(
                "瑞星財經 (FinGuider)", callback_data="news_finguider"
            ),
        ],
        [
            InlineKeyboardButton("Fintastic", callback_data="news_fintastic"),
            InlineKeyboardButton("Forecastock", callback_data="news_forecastock"),
        ],
        [
            InlineKeyboardButton("方格子 (Vocus)", callback_data="news_vocus_menu"),
            InlineKeyboardButton("NewsDigest AI", callback_data="news_ndai"),
        ],
        [InlineKeyboardButton("Fugle Report", callback_data="news_fugle")],
        [
            InlineKeyboardButton(
                "永豐｜3分鐘產業百科", callback_data="news_sinotrade_industry"
            ),
            InlineKeyboardButton(
                "口袋學堂｜研究報告", callback_data="news_pocket_report"
            ),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("請選擇新聞來源：", reply_markup=reply_markup)


async def news_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    news_parser: NewsParser = context.bot_data.get("news_parser") or NewsParser()
    data = query.data

    today_date = datetime.now().strftime("%Y-%m-%d")

    # --- Menus ---
    if data == "news_main_menu":
        # Back to Main News Menu
        keyboard = [
            [
                InlineKeyboardButton("鉅亨網 (CNYES)", callback_data="news_cnyes"),
                InlineKeyboardButton("Google News (TW)", callback_data="news_google"),
            ],
            [
                InlineKeyboardButton("Moneydj", callback_data="news_moneydj"),
                InlineKeyboardButton("Yahoo 股市", callback_data="news_yahoo"),
            ],
            [
                InlineKeyboardButton("聯合新聞網 (UDN)", callback_data="news_udn"),
                InlineKeyboardButton("UAnalyze", callback_data="news_uanalyze"),
            ],
            [
                InlineKeyboardButton("財經M平方", callback_data="news_macromicro"),
                InlineKeyboardButton(
                    "瑞星財經 (FinGuider)", callback_data="news_finguider"
                ),
            ],
            [
                InlineKeyboardButton("Fintastic", callback_data="news_fintastic"),
                InlineKeyboardButton("Forecastock", callback_data="news_forecastock"),
            ],
            [
                InlineKeyboardButton("方格子 (Vocus)", callback_data="news_vocus_menu"),
                InlineKeyboardButton("NewsDigest AI", callback_data="news_ndai"),
            ],
            [InlineKeyboardButton("Fugle Report", callback_data="news_fugle")],
            [
                InlineKeyboardButton(
                    "永豐｜3分鐘產業百科", callback_data="news_sinotrade_industry"
                ),
                InlineKeyboardButton(
                    "口袋學堂｜研究報告", callback_data="news_pocket_report"
                ),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("請選擇新聞來源：", reply_markup=reply_markup)
        return

    if data == "news_vocus_menu":
        # Vocus Submenu
        keyboard = [
            [InlineKeyboardButton("全部 (All)", callback_data="news_vocus_all")],
            [InlineKeyboardButton("ieObserve", callback_data="news_vocus_ieobserve")],
            [InlineKeyboardButton("Miula", callback_data="news_vocus_miula")],
            [
                InlineKeyboardButton(
                    "黑洞資本 (Black Hole)", callback_data="news_vocus_blackhole"
                )
            ],
            [InlineKeyboardButton("🔙 回新聞選單", callback_data="news_main_menu")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "與 Vocus 相關的追蹤者：", reply_markup=reply_markup
        )
        return

    # --- Fetching Logic ---
    news_list = []
    source_title = "News"

    # Standard Sources
    if data == "news_cnyes":
        source_title = "CNYES Headline"
        url = "https://api.cnyes.com/media/api/v1/newslist/category/headline"
        news_list = await news_parser.fetch_news_list(url, news_number=15)
    elif data == "news_google":
        source_title = "Google News"
        url = "https://news.google.com/rss?hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        news_list = await news_parser.fetch_news_list(url, news_number=15)
    elif data == "news_moneydj":
        source_title = "MoneyDJ"
        news_list = await news_parser.get_moneydj_report()
    elif data == "news_yahoo":
        source_title = "Yahoo TW"
        news_list = await news_parser.get_yahoo_tw_report()
    elif data == "news_udn":
        source_title = "UDN News"
        news_list = await news_parser.get_udn_report()
    elif data == "news_uanalyze":
        source_title = "UAnalyze"
        news_list = await news_parser.get_uanalyze_report()
    elif data == "news_macromicro":
        source_title = "MacroMicro"
        news_list = await news_parser.get_macromicro_report()
    elif data == "news_finguider":
        source_title = "FinGuider"
        news_list = await news_parser.get_finguider_report()
    elif data == "news_fintastic":
        source_title = "Fintastic"
        news_list = await news_parser.get_fintastic_report()
    elif data == "news_forecastock":
        source_title = "Forecastock"
        news_list = await news_parser.get_forecastock_report()
    elif data == "news_ndai":
        source_title = "NewsDigest AI"
        news_list = await news_parser.get_news_digest_ai_report()
    elif data == "news_fugle":
        source_title = "Fugle"
        url = "https://blog.fugle.tw/"
        news_list = await news_parser.get_fugle_report(url)
    elif data == "news_sinotrade_industry":
        source_title = "SinoTrade｜3分鐘產業百科"
        news_list = await news_parser.get_sinotrade_industry_report(limit=15)
    elif data == "news_pocket_report":
        source_title = "Pocket｜研究報告"
        news_list = await news_parser.get_pocket_school_report(limit=15)

    # Vocus Handlers
    elif data.startswith("news_vocus"):
        source_title = "Vocus"
        vocus_map = {
            "ieobserve": "@ieobserve",
            "miula": "@miula",
            "blackhole": "65ab564cfd897800018a88cc",
        }

        target_users = []
        if data == "news_vocus_all":
            target_users = list(vocus_map.values())
            source_title = "Vocus (All)"
        else:
            key = data.replace("news_vocus_", "")
            if key in vocus_map:
                target_users = [vocus_map[key]]
                source_title = f"Vocus ({key})"

        for v_user in target_users:
            res = await news_parser.get_vocus_articles(v_user)
            if res:
                news_list.extend(res)

    # --- Display ---
    if not news_list:
        back_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 回上一頁", callback_data="news_main_menu")]]
        )
        await query.edit_message_text(
            "No news found or source not implemented yet.", reply_markup=back_markup
        )
        return

    # Format news
    msg = f"📅 **{source_title}** ({today_date})\n\n"
    for news in news_list[:10]:
        title = news["title"].replace("[", "(").replace("]", ")")
        msg += f"• [{title}]({news['url']})\n"

    # Add Back Button
    # If in Vocus submenu, maybe back to Vocus menu? But simplier to Main Menu for consistent UX.
    # Or checking data startswith.
    back_callback = (
        "news_vocus_menu" if data.startswith("news_vocus") else "news_main_menu"
    )

    keyboard = [[InlineKeyboardButton("🔙 回上一頁", callback_data=back_callback)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        msg,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
        reply_markup=reply_markup,
    )


# --- Google News Specific ---
async def google_news_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Enter keyword for Google News search:")
    return ASK_GOOGLE_NEWS


async def google_news_handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyword = update.message.text
    news_parser: NewsParser = context.bot_data.get("news_parser") or NewsParser()

    url = (
        f"https://news.google.com/rss/search?q={keyword}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    )
    news_list = await news_parser.fetch_news_list(url)

    if not news_list:
        await update.message.reply_text("No results found.")
    else:
        msg = f"🔍 Results for '{keyword}':\n\n"
        for news in news_list[:8]:
            title = news["title"].replace("[", "(").replace("]", ")")
            msg += f"• [{title}]({news['url']})\n"
        await update.message.reply_text(
            msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True
        )

    return ConversationHandler.END


# --- Research (Files) ---
async def research_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 請提供欲研究之資料，📝 文字與 📎 檔案皆可（可多份）：\n(Send /rq when done)"
    )
    context.user_data["research_materials"] = []
    return ASK_RESEARCH


async def research_handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Collect materials
    materials = context.user_data.get("research_materials", [])

    if update.message.document:
        doc = update.message.document
        file_name = doc.file_name.lower()
        if not file_name.endswith((".docx", ".doc", ".pdf")):
            await update.message.reply_text("這個檔案格式我還不支援喔！")
            return ASK_RESEARCH

        try:
            # Download file
            new_file = await doc.get_file()
            bio = io.BytesIO()
            await new_file.download_to_memory(out=bio)
            bio.seek(0)

            # Extract text if docx (Legacy logic supported docx internal extraction, new AIService handles file bytes directly usually)
            # But old bot extracted text for docx.
            # Let's keep it simple: pass bytes to AIService.
            # EXCEPT: AIService gemini client supports PDF/Text/Image/Audio.
            # Does it support DOCX bytes directly? Maybe not standard Gemini API.
            # Old bot extracted text for docx. Let's do that.

            _final_content = None
            _mime_type = "application/pdf"

            if file_name.endswith((".docx", ".doc")):
                try:
                    from docx import Document

                    document = Document(bio)
                    # Reset bio for reading bytes again? No, we extracted text.
                    text_content = "\n".join(
                        [para.text for para in document.paragraphs]
                    )
                    materials.append(("text/plain", text_content))  # Treat as text
                    await update.message.reply_text(f"Received Doc: {doc.file_name}")
                    context.user_data["research_materials"] = materials
                    return ASK_RESEARCH
                except Exception as e:
                    logger.error(f"Docx read error: {e}")
                    # Fallback to bytes if failed? No, gemini might not read docx bytes.
            else:
                # PDF or others
                materials.append(("application/pdf", bio.getvalue()))
                await update.message.reply_text(f"Received PDF: {doc.file_name}")

        except Exception as e:
            await update.message.reply_text(f"Failed to download: {e}")

    elif update.message.text and not update.message.text.startswith("/"):
        materials.append(("text/plain", update.message.text))
        await update.message.reply_text("Received text note.")

    context.user_data["research_materials"] = materials
    return ASK_RESEARCH


async def research_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    materials = context.user_data.get("research_materials", [])

    if not materials:
        await update.message.reply_text("No materials provided. Cancelling.")
        return ConversationHandler.END

    sent_msg = await update.message.reply_text(
        "開始為你生成研究報告資訊📊📄(預設prompt)"
    )

    ai = AIService()
    try:
        # Prompt
        prompt = (
            "根據提供的報告整理出常見投資問題、重點資訊與詳細回答，並用繁體中文回答"
        )

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
            f = io.BytesIO(response.encode("utf-8"))
            f.name = "Research.md"
            await update.message.reply_document(
                document=InputFile(f, filename="Research.md"),
                reply_to_message_id=sent_msg.message_id,
            )
        else:
            await update.message.reply_text("Analysis failed.")

    except Exception as e:
        logger.error(f"Research error: {e}")
        await update.message.reply_text(f"Error analyzing materials: {e}")

    # Clear data
    context.user_data["research_materials"] = []
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
        sub = session.exec(
            select(Subscriber).where(Subscriber.chat_id == chat_id)
        ).first()
        if not sub:
            session.add(Subscriber(chat_id=chat_id))
            session.commit()
            await update.message.reply_text(
                "✅ 已成功訂閱！\n您將會收到：\n1. 每日個股分析報告\n2. 即時重大新聞推播\n3. Podcast 摘要"
            )
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
        sub = session.exec(
            select(Subscriber).where(Subscriber.chat_id == chat_id)
        ).first()
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
        await update.message.reply_text(
            f"找不到公司名稱：{data.get('error') if isinstance(data, dict) else 'Unknown error'}"
        )
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

    alias = (
        " ".join([str(x) for x in context.args[2:]]).strip()
        if len(context.args) > 2
        else None
    )
    if alias:
        alias = alias[:MAX_ALIAS_LENGTH]
    else:
        # Best-effort auto name fetch if user didn't provide alias.
        try:
            from ..database import engine
            from sqlmodel import Session, select
            from ..models.stock import StockData

            with Session(engine) as session:
                stock = session.exec(
                    select(StockData).where(StockData.ticker == ticker)
                ).first()
                if stock and stock.name:
                    alias = str(stock.name)[:MAX_ALIAS_LENGTH]
        except Exception:
            alias = None

        # Only do network-ish name lookup for TW numeric tickers (keeps /watch fast & stable for US tickers)
        if not alias and ticker.isdigit():
            try:
                data, _ = await StockService.get_or_analyze_stock(ticker)
                if (
                    isinstance(data, dict)
                    and data.get("name")
                    and data.get("name") != ticker
                ):
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
            session.add(
                WatchlistEntry(
                    chat_id=chat_id, user_id=user_id, ticker=ticker, alias=alias
                )
            )
            session.commit()
            alias_suffix = f"（{alias}）" if alias else ""
            await update.message.reply_text(f"✅ 已加入：{ticker}{alias_suffix}")
            return

        # remove
        if not existing:
            await update.message.reply_text(f"ℹ️ 不在清單：{ticker}")
            return
        session.delete(existing)
        session.commit()
        await update.message.reply_text(f"✅ 已移除：{ticker}")
