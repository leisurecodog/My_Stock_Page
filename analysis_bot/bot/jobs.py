import logging
import asyncio
import difflib
import html
import re
import unicodedata
from typing import List, Dict, Set
from datetime import datetime, timedelta
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from sqlmodel import Session, select, col

from ..services.news_parser import NewsParser
from ..models.content import News
from ..database import engine
from ..utils.pii import redact_telegram_id

logger = logging.getLogger(__name__)

# Constants
MAX_ALIAS_LENGTH = 64
TICKER_MATCH_THRESHOLD = 0.85
MAX_SEND_ARTICLES = 5

_WORD_CHARS_RE = re.compile(r"[A-Z0-9]")
# Simple regex for potential tickers: 4 digits (TW) or 2-5 uppercase letters (US)
_POTENTIAL_TICKER_RE = re.compile(r"\b([0-9]{4}|[A-Z]{2,5})\b")

def _norm_text(text: str) -> str:
    # NFKC helps normalize full-width characters; upper for ticker matching
    return unicodedata.normalize("NFKC", text or "").upper()


def _normalize_content_for_matching(title: str, url: str) -> tuple[str, str, str, str, str]:
    """
    Normalize title and URL for matching to avoid repeated operations.
    
    Returns:
        tuple: (title_raw, title_norm, url_raw, url_norm, content_norm)
    """
    title_norm = unicodedata.normalize("NFKC", title).strip()
    url_norm = unicodedata.normalize("NFKC", url).strip()
    content_norm = f"{title_norm}\n{url_norm}"
    return title, title_norm, url, url_norm, content_norm


def _contains_ticker(text_upper: str, ticker_upper: str) -> bool:
    """
    Avoid substring false positives by enforcing non-alnum boundaries around ticker.
    Works for both numeric (2330) and alpha (TSLA) tickers.
    """
    if not ticker_upper:
        return False
    if ticker_upper not in text_upper:
        return False

    # Boundary check without heavy regex compilation per call
    start = 0
    while True:
        idx = text_upper.find(ticker_upper, start)
        if idx < 0:
            return False
        left_ok = idx == 0 or not _WORD_CHARS_RE.match(text_upper[idx - 1])
        right_i = idx + len(ticker_upper)
        right_ok = right_i >= len(text_upper) or not _WORD_CHARS_RE.match(text_upper[right_i])
        if left_ok and right_ok:
            return True
        start = idx + 1

def _extract_tickers_from_text(text_upper: str) -> Set[str]:
    """Extract potential tickers from text."""
    candidates = set(_POTENTIAL_TICKER_RE.findall(text_upper))
    # Filter? For now return all candidates. 
    # In real world, we might want to check against a DB of valid tickers 
    # to avoid false positives (e.g. "THE", "YEAR", "2024"), but let's keep it simple first.
    # Maybe filter out common 4-digit years?
    valid = set()
    current_year = datetime.now().year
    for c in candidates:
        # Simple year filter
        if c.isdigit() and 1990 <= int(c) <= current_year + 5:
            continue
        valid.add(c)
    return valid

async def check_news_job(context: ContextTypes.DEFAULT_TYPE = None, bot=None):
    """
    Background job to fetch news and notify subscribers.
    Can be called by PTB JobQueue (context) or APScheduler (bot).
    """
    # Valid check handled later via DB logic

    # Determine Bot and NewsParser
    if context:
        bot_instance = context.bot
        news_parser = context.bot_data.get("news_parser") or NewsParser()
    elif bot:
        bot_instance = bot
        news_parser = NewsParser()
    else:
        # Fallback for standalone run (create bot from settings)
        from ..config import get_settings
        from telegram import Bot
        settings = get_settings()
        bot_instance = Bot(token=settings.TELEGRAM_TOKEN)
        news_parser = NewsParser()

    # Privacy: redact Telegram IDs in logs
    from ..config import get_settings
    pii_salt = (get_settings().LOG_PII_SALT or None)
    
    # Sources configuration
    sources = [
        {"name": "CNYES", "url": "https://api.cnyes.com/media/api/v1/newslist/category/headline"},
        # {"name": "GoogleNews", "url": "https://news.google.com/rss?hl=zh-TW&gl=TW&ceid=TW:zh-Hant"},
        {"name": "MoneyDJ", "url": "https://www.moneydj.com/KMDJ/RssCenter.aspx?svc=NR&fno=1&arg=MB010000"}
        # UAnalyze handled separately below
    ]

    new_articles = []
    
    # 1. Fetch from Standard Sources
    for source in sources:
        try:
            articles = await news_parser.fetch_news_list(source["url"])
            if articles:
                for a in articles:
                    a['source_name'] = source["name"]
                new_articles.extend(articles)
        except Exception as e:
            logger.error(f"Error fetching news from {source['name']}: {e}")

    # 2. Fetch from UAnalyze, Fugle, Vocus
    
    # UAnalyze
    try:
        ua_articles = await news_parser.get_uanalyze_report()
        if ua_articles:
            for a in ua_articles: a['source_name'] = "UAnalyze"
            new_articles.extend(ua_articles)
    except Exception as e:
        logger.error(f"Error fetching UAnalyze: {e}")

    # 3. Filter Duplicates (DB Check) & Save
    final_new_articles = []
    
    if new_articles:
        # Use simple recent title cache to avoid DB spam if possible?
        # But for correctness, check DB.
        
        # Pull recent news titles from DB (last 3 days)
        cutoff = datetime.utcnow() - timedelta(days=3)
        with Session(engine) as session:
            recent_news = session.exec(select(News).where(News.created_at >= cutoff)).all()
            # Create lookup
            # Use fuzzy matching or exact link match?
            # Link is safer.
            recent_links = {n.link for n in recent_news}
            recent_titles = [n.title for n in recent_news]

            for article in new_articles:
                link = article["url"]
                title = article["title"]
                source_name = article.get("source_name", "Unknown")

                # A. Check Exact Link
                if link in recent_links:
                    continue

                # B. Check Fuzzy Title Match (Slower but necessary)
                is_duplicate_title = False
                for recent_title in recent_titles:
                    ratio = difflib.SequenceMatcher(None, title, recent_title).ratio()
                    if ratio > 0.85: # Threshold
                        is_duplicate_title = True
                        logger.info(f"Skipping duplicate title ({ratio:.2f}): '{title}' vs '{recent_title}'")
                        break

                if is_duplicate_title:
                    continue

                # Additional check: Did we already add it in this current batch?
                # (Though unlikely to have duplicate URL in same batch from same source,
                # but maybe cross-source in same run?)
                # Let's check against final_new_articles as well
                in_batch_duplicate = False
                for added in final_new_articles:
                    if difflib.SequenceMatcher(None, title, added['title']).ratio() > 0.85:
                        in_batch_duplicate = True
                        break

                if in_batch_duplicate:
                    continue

                # New article found!
                news_item = News(
                    title=title,
                    link=link,
                    source=source_name
                )
                session.add(news_item)
                final_new_articles.append(article)
                # Add to recent_titles so next item in loop checks against this one too
                recent_titles.append(title)

            session.commit()

    # Send notifications using final_new_articles
    new_articles = final_new_articles # Update reference for sending logic below

    # Send notifications
    if new_articles:
        from ..models.subscriber import Subscriber
        from ..models.watchlist import WatchlistEntry
        from ..models.stock import StockData
        # Session, select already imported at top
        
        subscribers = []
        with Session(engine) as session:
             subs = session.exec(select(Subscriber).where(Subscriber.is_active == True)).all()
             subscribers = [s.chat_id for s in subs]

        # Preload watchlist entries for subscriber chats
        watch_by_chat: Dict[int, Dict[int, List[WatchlistEntry]]] = {}
        tickers_by_chat: Dict[int, set[str]] = {}
        if subscribers:
            with Session(engine) as session:
                entries = session.exec(
                    select(WatchlistEntry).where(col(WatchlistEntry.chat_id).in_(subscribers))
                ).all()
                for e in entries:
                    watch_by_chat.setdefault(e.chat_id, {}).setdefault(e.user_id, []).append(e)
                    tickers_by_chat.setdefault(e.chat_id, set()).add(e.ticker)

        # Optional enrichment: pull known company names from StockData for tickers
        names_by_ticker: Dict[str, str] = {}
        all_tickers: set[str] = set()
        for tset in tickers_by_chat.values():
            all_tickers.update(tset)
        if all_tickers:
            with Session(engine) as session:
                rows = session.exec(select(StockData).where(col(StockData.ticker).in_(list(all_tickers)))).all()
                for r in rows:
                    if r.ticker and r.name:
                        names_by_ticker[str(r.ticker).upper()] = str(r.name)
             
        logger.info(f"Found {len(new_articles)} new articles. Sending to {len(subscribers)} subscribers.")
        
        # Group messages to avoid spamming? Or send individually?
        # Let's send individually for now as they appear, or in small batches.
        # Sending top MAX_SEND_ARTICLES latest to avoid flood if DB was empty
        to_send = new_articles[:MAX_SEND_ARTICLES]
        
        # Pre-normalize all news articles to avoid repeated operations
        normalized_news = []
        for news in to_send:
            title_raw = news["title"]
            url_raw = news["url"]
            title, title_norm, url, url_norm, content_norm = _normalize_content_for_matching(title_raw, url_raw)
            
            # Detect tickers for Interactive Buttons
            content_upper = _norm_text(f"{title_raw}\n{url_raw}")
            detected_tickers = _extract_tickers_from_text(content_upper)
            
            title_md = title.replace("[", "(").replace("]", ")")  # Simple markdown escape
            msg_md = f"📰 *{title_md}*\n{url}"
            
            normalized_news.append({
                'title_raw': title_raw,
                'title_norm': title_norm,
                'url_raw': url,
                'url_norm': url_norm,
                'content_upper': content_upper,
                'msg_md': msg_md,
                'detected_tickers': detected_tickers
            })
        
        for news_data in normalized_news:
            title_raw = news_data['title_raw']
            url_raw = news_data['url_raw']
            content_upper = news_data['content_upper']
            msg_md = news_data['msg_md']
            detected_tickers = news_data['detected_tickers']
            
            # Build Inline Keyboard if tickers detected
            reply_markup = None
            if detected_tickers:
                # Limit to 3 buttons max to avoid clutter
                buttons = []
                for t in sorted(list(detected_tickers))[:3]:
                    # Callback Data: NA|ADD|{ticker}
                    buttons.append(
                        InlineKeyboardButton(f"➕ 關注 {t}", callback_data=f"NA|ADD|{t}")
                    )
                if buttons:
                    reply_markup = InlineKeyboardMarkup([buttons])

            for chat_id in subscribers:
                try:
                    # If we have watchlist entries for this chat, try to mention matching users
                    related = watch_by_chat.get(chat_id, {})
                    if related:
                        user_hits: Dict[int, set[str]] = {}
                        for uid, entries in related.items():
                            hits: set[str] = set()
                            for e in entries:
                                t = (e.ticker or "").upper()
                                if t and _contains_ticker(content_upper, t):
                                    hits.add(t)
                                if e.alias:
                                    alias_norm = unicodedata.normalize("NFKC", e.alias).strip()
                                    if alias_norm and alias_norm in f"{news_data['title_norm']}\n{news_data['url_norm']}":
                                        hits.add(alias_norm)
                                # StockData name enrichment
                                name = names_by_ticker.get(t)
                                if name:
                                    name_norm = unicodedata.normalize("NFKC", name).strip()
                                    if name_norm and name_norm in f"{news_data['title_norm']}\n{news_data['url_norm']}":
                                        hits.add(name_norm)
                            if hits:
                                user_hits[uid] = hits

                        if user_hits:
                            title_html = html.escape(title_raw)
                            url_html = html.escape(url_raw)
                            # Privacy: do not include any user_id / tg://user link / numeric IDs in message.
                            # We only show the union of matched keywords.
                            all_hits: set[str] = set()
                            for hits in user_hits.values():
                                all_hits.update(hits)
                            kw = "、".join([html.escape(x) for x in sorted(all_hits)])
                            related_line = f"相關：{kw}"
                            msg_html = f"📰 <b>{title_html}</b>\n{url_html}\n{related_line}"

                            await bot_instance.send_message(
                                chat_id=chat_id,
                                text=msg_html,
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=True,
                                reply_markup=reply_markup
                            )
                            continue

                    # Default: keep existing broadcast behavior
                    await bot_instance.send_message(
                        chat_id=chat_id,
                        text=msg_md,
                        parse_mode=ParseMode.MARKDOWN,
                        disable_web_page_preview=True,
                        reply_markup=reply_markup
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to send news to {redact_telegram_id(chat_id, salt=pii_salt)}: {e}"
                    )
