import logging
import asyncio
import difflib
import html
import re
import unicodedata
import shutil
from contextlib import suppress
from typing import List, Dict
from datetime import datetime, timedelta
from urllib.parse import urlparse
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
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

_SOURCE_DISPLAY_NAME: dict[str, str] = {
    "CNYES": "鉅亨網",
    "MoneyDJ": "MoneyDJ",
    "UAnalyze": "UAnalyze",
    "Fugle": "Fugle",
    "UDN": "聯合新聞網",
    "YahooTW": "Yahoo 股市",
    "NewsDigestAI": "NewsDigest AI",
    "Macromicro": "財經M平方",
    "FinGuider": "瑞星財經 (FinGuider)",
    "Fintastic": "Fintastic",
    "Forecastock": "Forecastock",
    "SinoTradeIndustry": "永豐｜3分鐘產業百科",
    "PocketReport": "口袋學堂｜研究報告",
    "BuffettLetter": "Buffett 股東信",
    "HowardMarksMemo": "Howard Marks 備忘錄",
}

# --- Cursor Agent Summary Helpers ---


async def _run_cursor_agent_summary(prompt: str, timeout: float = 30.0) -> str | None:
    """
    Call cursor agent (headless) to summarize text. Returns None on failure.
    """
    if not shutil.which("cursor"):
        logger.warning("cursor CLI 未安裝或不在 PATH，略過摘要。")
        return None

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "cursor",
            "agent",
            "--print",
            "--output-format",
            "text",
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode == 0:
            return stdout.decode().strip()
        logger.warning(
            "cursor agent 摘要失敗 (code=%s): %s",
            proc.returncode,
            (stderr.decode().strip() if stderr else ""),
        )
    except asyncio.TimeoutError:
        logger.warning("cursor agent 摘要逾時，已中止。")
        if proc:
            proc.kill()
            with suppress(Exception):
                await proc.communicate()
    except FileNotFoundError:
        logger.warning("找不到 cursor 可執行檔，略過摘要。")
    except Exception as e:
        logger.error(f"cursor agent 摘要例外: {e}")
    return None


from ..services.ai_service import AIService, RequestType

# Semaphore to limit concurrent AI API calls instead of opencode processes
_APP_AI_SEM = asyncio.Semaphore(2)


async def _run_ai_summary(prompt: str) -> str | None:
    """
    Call AIService directly to summarize text.
    Returns None on failure.
    """
    try:
        async with _APP_AI_SEM:
            ai = AIService()
            return await ai.call(RequestType.TEXT, contents=prompt, use_search=False)
    except Exception as e:
        logger.error(f"AI 摘要發生錯誤: {e}")
        return None


async def _prepare_news_text(news: dict, news_parser: "NewsParser") -> str:
    """
    決定用什麼文字送去摘要（優先取全文）：
    1) 嘗試 fetch 網頁全文（所有已知來源皆支援）
    2) 若全文比現有 content/description 長，使用全文
    3) 退回 content/description -> title
    """
    existing_text = news.get("content") or news.get("description") or ""
    base_text = existing_text

    # Always try to fetch full article content for better summarization
    if news.get("url"):
        try:
            fetched = await news_parser.fetch_news_content(news["url"])
            if fetched and len(fetched) > len(existing_text):
                base_text = fetched
        except Exception as e:
            logger.debug(f"抓取新聞全文失敗 {news.get('url')}: {e}")

    if not base_text:
        base_text = news.get("title") or ""
    return str(base_text)[:4000]


async def _summarize_news(news: dict, news_parser: "NewsParser") -> str | None:
    """
    產出繁體中文摘要。
    """
    text = await _prepare_news_text(news, news_parser)
    if not text:
        return None

    prompt = (
        "Summarize the following news in Traditional Chinese within the 100~200 字. 保留關鍵數字與主語；若資訊不足則回答「資訊不足」。\n\n"
        f"{text}"
    )

    # Use internal AIService (Ollama > Gemini) instead of slow CLI
    return await _run_ai_summary(prompt)


def _guess_source_label(source_name: str | None, url: str) -> str:
    """
    Convert internal source_name / URL into a user-facing source label.
    Priority:
      1) source_name mapping (e.g. CNYES -> 鉅亨網)
      2) URL domain fallback (e.g. news.cnyes.com -> cnyes.com)
    """
    if source_name:
        if source_name.startswith("Vocus"):
            # e.g. "Vocus (@ieobserve)" -> "方格子 (@ieobserve)"
            return source_name.replace("Vocus", "方格子", 1)
        if source_name in _SOURCE_DISPLAY_NAME:
            return _SOURCE_DISPLAY_NAME[source_name]
        return source_name

    try:
        host = (urlparse(url).netloc or "").lower()
        host = host.replace("www.", "")
        if "cnyes.com" in host:
            return "鉅亨網"
        if "moneydj.com" in host:
            return "MoneyDJ"
        if "udn.com" in host:
            return "聯合新聞網"
        if "yahoo.com" in host or "yahoo" in host:
            return "Yahoo"
        if "fugle.tw" in host:
            return "Fugle"
        if "vocus.cc" in host:
            return "方格子"
        if "macromicro.me" in host:
            return "財經M平方"
        if "finguider.cc" in host:
            return "瑞星財經 (FinGuider)"
        if "sinotrade.com.tw" in host:
            return "永豐｜3分鐘產業百科"
        if "pocket.tw" in host:
            return "口袋學堂｜研究報告"
        return host or "來源"
    except Exception:
        return source_name or "來源"


def _escape_markdown_text(text: str) -> str:
    # Markdown v1: we mainly avoid breaking link syntax with [].
    return (text or "").replace("[", "(").replace("]", ")")


def _format_source_line_markdown(source_label: str, url: str) -> str:
    label_md = _escape_markdown_text(source_label)
    return f"來源：[{label_md}]({url})"


def _format_source_line_html(source_label: str, url: str) -> str:
    label_html = html.escape(source_label)
    url_html = html.escape(url, quote=True)
    return f'來源：<a href="{url_html}">{label_html}</a>'


def _norm_text(text: str) -> str:
    # NFKC helps normalize full-width characters; upper for ticker matching
    return unicodedata.normalize("NFKC", text or "").upper()


def _normalize_content_for_matching(
    title: str, url: str
) -> tuple[str, str, str, str, str]:
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
        right_ok = right_i >= len(text_upper) or not _WORD_CHARS_RE.match(
            text_upper[right_i]
        )
        if left_ok and right_ok:
            return True
        start = idx + 1


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
        from telegram.request import HTTPXRequest

        settings = get_settings()
        req = HTTPXRequest(
            connection_pool_size=8,
            connect_timeout=30.0,
            read_timeout=30.0,
            write_timeout=30.0,
        )
        bot_instance = Bot(token=settings.TELEGRAM_TOKEN, request=req)
        await bot_instance.initialize()
        news_parser = NewsParser()

    # Privacy: redact Telegram IDs in logs
    from ..config import get_settings

    pii_salt = get_settings().LOG_PII_SALT or None

    try:
        # Sources configuration
        sources = [
            {
                "name": "CNYES",
                "url": "https://api.cnyes.com/media/api/v1/newslist/category/headline",
            },
            # {"name": "GoogleNews", "url": "https://news.google.com/rss?hl=zh-TW&gl=TW&ceid=TW:zh-Hant"},
            {
                "name": "MoneyDJ",
                "url": "https://www.moneydj.com/KMDJ/RssCenter.aspx?svc=NR&fno=1&arg=MB010000",
            },
            # UAnalyze handled separately below
        ]

        new_articles = []

        # 1. Fetch from Standard Sources
        for source in sources:
            try:
                articles = await news_parser.fetch_news_list(source["url"])
                if articles:
                    for a in articles:
                        a["source_name"] = source["name"]
                    new_articles.extend(articles)
            except Exception as e:
                logger.error(f"Error fetching news from {source['name']}: {e}")

        # 2. Fetch from UAnalyze, Fugle, Vocus

        # UAnalyze
        try:
            ua_articles = await news_parser.get_uanalyze_report()
            if ua_articles:
                for a in ua_articles:
                    a["source_name"] = "UAnalyze"
                new_articles.extend(ua_articles)
        except Exception as e:
            logger.error(f"Error fetching UAnalyze: {e}")

        # Fugle
        try:
            fugle_articles = await news_parser.get_fugle_report(
                "https://blog.fugle.tw/"
            )
            if fugle_articles:
                for a in fugle_articles:
                    a["source_name"] = "Fugle"
                new_articles.extend(fugle_articles)
        except Exception as e:
            logger.error(f"Error fetching Fugle: {e}")

        # Vocus
        try:
            vocus_users = ["@ieobserve", "@miula", "65ab564cfd897800018a88cc"]
            for v_user in vocus_users:
                try:
                    vocus_articles = await news_parser.get_vocus_articles(v_user)
                    if vocus_articles:
                        for a in vocus_articles:
                            a["source_name"] = f"Vocus ({v_user})"
                        new_articles.extend(vocus_articles)
                except Exception as e:
                    logger.error(f"Error fetching Vocus user {v_user}: {e}")
        except Exception as e:
            logger.error(f"Error Vocus main block: {e}")

        # 2.5. Additional Sources (SinoTrade / Pocket)
        try:
            st_res = await news_parser.get_sinotrade_industry_report(limit=20)
            if st_res:
                for a in st_res:
                    a["source_name"] = "SinoTradeIndustry"
                new_articles.extend(st_res)
        except Exception as e:
            logger.error(f"Error fetching SinoTradeIndustry: {e}")

        try:
            pk_res = await news_parser.get_pocket_school_report(limit=20)
            if pk_res:
                for a in pk_res:
                    a["source_name"] = "PocketReport"
                new_articles.extend(pk_res)
        except Exception as e:
            logger.error(f"Error fetching PocketReport: {e}")

        # 3. Fetch from Additional Ported Sources (UDN, Yahoo, Others)

        settings = get_settings()

        # UDN
        if settings.ENABLE_UDN_NEWS:
            try:
                udn_res = await news_parser.get_udn_report()
                if udn_res:
                    for a in udn_res:
                        a["source_name"] = "UDN"
                    new_articles.extend(udn_res)
            except Exception as e:
                logger.error(f"Error fetching UDN: {e}")

        # Yahoo TW
        if settings.ENABLE_YAHOO_NEWS:
            try:
                yahoo_res = await news_parser.get_yahoo_tw_report()
                if yahoo_res:
                    for a in yahoo_res:
                        a["source_name"] = "YahooTW"
                    new_articles.extend(yahoo_res)
            except Exception as e:
                logger.error(f"Error fetching YahooTW: {e}")

        # News Digest AI
        try:
            ndai_res = await news_parser.get_news_digest_ai_report()
            if ndai_res:
                for a in ndai_res:
                    a["source_name"] = "NewsDigestAI"
                new_articles.extend(ndai_res)
        except Exception as e:
            logger.error(f"Error fetching NewsDigestAI: {e}")

        # Fallbacks (Macromicro, FinGuider, Fintastic, Forecastock)
        # We wrap each in try-except individually for robustness

        try:
            mm_res = await news_parser.get_macromicro_report()
            if mm_res:
                for a in mm_res:
                    a["source_name"] = "Macromicro"
                new_articles.extend(mm_res)
        except Exception as e:
            logger.error(f"Macromicro fetch error: {e}")

        try:
            fg_res = await news_parser.get_finguider_report()
            if fg_res:
                for a in fg_res:
                    a["source_name"] = "FinGuider"
                new_articles.extend(fg_res)
        except Exception as e:
            logger.error(f"FinGuider fetch error: {e}")

        try:
            ft_res = await news_parser.get_fintastic_report()
            if ft_res:
                for a in ft_res:
                    a["source_name"] = "Fintastic"
                new_articles.extend(ft_res)
        except Exception as e:
            logger.error(f"Fintastic fetch error: {e}")

        try:
            fc_res = await news_parser.get_forecastock_report()
            if fc_res:
                for a in fc_res:
                    a["source_name"] = "Forecastock"
                new_articles.extend(fc_res)
        except Exception as e:
            logger.error(f"Forecastock fetch error: {e}")

        # Buffett Shareholder Letters (annual, from berkshirehathaway.com)
        try:
            buf_res = await news_parser.get_buffett_letters()
            if buf_res:
                for a in buf_res:
                    a["source_name"] = "BuffettLetter"
                new_articles.extend(buf_res)
        except Exception as e:
            logger.error(f"BuffettLetter fetch error: {e}")

        # Howard Marks Memos (from oaktreecapital.com)
        try:
            hm_res = await news_parser.get_howard_marks_memos(limit=5)
            if hm_res:
                for a in hm_res:
                    a["source_name"] = "HowardMarksMemo"
                new_articles.extend(hm_res)
        except Exception as e:
            logger.error(f"HowardMarksMemo fetch error: {e}")

        if not new_articles:
            return

        # 3. Filter and Save with Deduplication
        final_new_articles = []

        with Session(engine) as session:
            # Pre-fetch recent titles for fuzzy matching (last 24 hours)
            yesterday = datetime.utcnow() - timedelta(days=1)
            recent_news = session.exec(
                select(News).where(News.created_at >= yesterday)
            ).all()
            recent_titles = [n.title for n in recent_news]

            for article in new_articles:
                link = article.get("url")
                title = article.get("title")
                source_name = article.get("source_name", "Unknown")

                if not link or not title:
                    continue

                # A. Check exact URL match (Fast)
                existing_link = session.exec(
                    select(News).where(News.link == link)
                ).first()
                if existing_link:
                    continue

                # B. Check Fuzzy Title Match (Slower but necessary)
                is_duplicate_title = False
                for recent_title in recent_titles:
                    ratio = difflib.SequenceMatcher(None, title, recent_title).ratio()
                    if ratio > 0.85:  # Threshold
                        is_duplicate_title = True
                        logger.info(
                            f"Skipping duplicate title ({ratio:.2f}): '{title}' vs '{recent_title}'"
                        )
                        break

                if is_duplicate_title:
                    continue

                # Additional check: Did we already add it in this current batch?
                # (Though unlikely to have duplicate URL in same batch from same source,
                # but maybe cross-source in same run?)
                # Let's check against final_new_articles as well
                in_batch_duplicate = False
                for added in final_new_articles:
                    if (
                        difflib.SequenceMatcher(None, title, added["title"]).ratio()
                        > 0.85
                    ):
                        in_batch_duplicate = True
                        break

                if in_batch_duplicate:
                    continue

                # New article found!
                news_item = News(title=title, link=link, source=source_name)
                session.add(news_item)
                final_new_articles.append(article)
                # Add to recent_titles so next item in loop checks against this one too
                recent_titles.append(title)

            session.commit()

        # Send notifications using final_new_articles
        new_articles = final_new_articles  # Update reference for sending logic below

        # Send notifications
        if new_articles:
            from ..models.subscriber import Subscriber
            from ..models.watchlist import WatchlistEntry
            from ..models.stock import StockData
            # Session, select already imported at top

            subscribers = []
            with Session(engine) as session:
                subs = session.exec(
                    select(Subscriber).where(Subscriber.is_active == True)
                ).all()
                subscribers = [s.chat_id for s in subs]

            # Preload watchlist entries for subscriber chats
            watch_by_chat: Dict[int, Dict[int, List[WatchlistEntry]]] = {}
            tickers_by_chat: Dict[int, set[str]] = {}
            if subscribers:
                with Session(engine) as session:
                    entries = session.exec(
                        select(WatchlistEntry).where(
                            col(WatchlistEntry.chat_id).in_(subscribers)
                        )
                    ).all()
                    for e in entries:
                        watch_by_chat.setdefault(e.chat_id, {}).setdefault(
                            e.user_id, []
                        ).append(e)
                        tickers_by_chat.setdefault(e.chat_id, set()).add(e.ticker)

            # Optional enrichment: pull known company names from StockData for tickers
            names_by_ticker: Dict[str, str] = {}
            all_tickers: set[str] = set()
            for tset in tickers_by_chat.values():
                all_tickers.update(tset)
            if all_tickers:
                with Session(engine) as session:
                    rows = session.exec(
                        select(StockData).where(
                            col(StockData.ticker).in_(list(all_tickers))
                        )
                    ).all()
                    for r in rows:
                        if r.ticker and r.name:
                            names_by_ticker[str(r.ticker).upper()] = str(r.name)

            logger.info(
                f"Found {len(new_articles)} new articles. Sending to {len(subscribers)} subscribers."
            )

            # Group messages to avoid spamming? Or send individually?
            # Let's send individually for now as they appear, or in small batches.
            # Sending top MAX_SEND_ARTICLES latest to avoid flood if DB was empty
            to_send = new_articles[:MAX_SEND_ARTICLES]

            # 4. 先對要送出的新聞做摘要（並行處理，加速）
            summaries: list[str | None] = list(
                await asyncio.gather(
                    *(_summarize_news(news, news_parser) for news in to_send),
                    return_exceptions=False,
                )
            )

            # Pre-normalize all news articles to avoid repeated operations
            normalized_news = []
            for idx, news in enumerate(to_send):
                title_raw = news["title"]
                url_raw = news["url"]
                source_name = news.get("source_name") or news.get("source")
                title, title_norm, url, url_norm, content_norm = (
                    _normalize_content_for_matching(title_raw, url_raw)
                )

                title_md = title.replace("[", "(").replace(
                    "]", ")"
                )  # Simple markdown escape
                source_label = _guess_source_label(source_name, url)

                summary_md = None
                if idx < len(summaries) and summaries[idx]:
                    summary_md = _escape_markdown_text(summaries[idx])

                msg_md = f"📰 *{title_md}*"
                if summary_md:
                    msg_md += f"\n{summary_md}"
                msg_md += f"\n{_format_source_line_markdown(source_label, url)}"

                normalized_news.append(
                    {
                        "title_raw": title_raw,
                        "title_norm": title_norm,
                        "url_raw": url,
                        "url_norm": url_norm,
                        "source_name": source_name,
                        "content_upper": _norm_text(f"{title_raw}\n{url_raw}"),
                        "msg_md": msg_md,
                    }
                )

            for news_data in normalized_news:
                title_raw = news_data["title_raw"]
                url_raw = news_data["url_raw"]
                content_upper = news_data["content_upper"]
                msg_md = news_data["msg_md"]
                source_name = news_data.get("source_name")

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
                                        alias_norm = unicodedata.normalize(
                                            "NFKC", e.alias
                                        ).strip()
                                        if (
                                            alias_norm
                                            and alias_norm
                                            in f"{news_data['title_norm']}\n{news_data['url_norm']}"
                                        ):
                                            hits.add(alias_norm)
                                    # StockData name enrichment
                                    name = names_by_ticker.get(t)
                                    if name:
                                        name_norm = unicodedata.normalize(
                                            "NFKC", name
                                        ).strip()
                                        if (
                                            name_norm
                                            and name_norm
                                            in f"{news_data['title_norm']}\n{news_data['url_norm']}"
                                        ):
                                            hits.add(name_norm)
                                step_hits = list(hits)  # Workaround to use in set
                                for step_hit in step_hits:
                                    # Not sure why the original loop was slightly weird, simplifying the `user_hits[uid] = set()`
                                    pass
                                if hits:
                                    user_hits.setdefault(uid, set()).update(hits)

                            if user_hits:
                                title_html = html.escape(title_raw)
                                source_label = _guess_source_label(source_name, url_raw)
                                source_line_html = _format_source_line_html(
                                    source_label, url_raw
                                )
                                # Privacy: do not include any user_id / tg://user link / numeric IDs in message.
                                # We only show the union of matched keywords.
                                all_hits: set[str] = set()
                                for hits in user_hits.values():
                                    all_hits.update(hits)
                                kw = "、".join(
                                    [html.escape(x) for x in sorted(all_hits)]
                                )
                                related_line = f"相關：{kw}"
                                msg_html = f"📰 <b>{title_html}</b>\n{source_line_html}\n{related_line}"

                                await bot_instance.send_message(
                                    chat_id=chat_id,
                                    text=msg_html,
                                    parse_mode=ParseMode.HTML,
                                    disable_web_page_preview=True,
                                )
                                continue

                        # Default: keep existing broadcast behavior
                        await bot_instance.send_message(
                            chat_id=chat_id,
                            text=msg_md,
                            parse_mode=ParseMode.MARKDOWN,
                            disable_web_page_preview=True,
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to send news to {redact_telegram_id(chat_id, salt=pii_salt)}: {e}"
                        )
    finally:
        # Ensure NewsParser session is closed to prevent "Unclosed client session"
        if news_parser:
            await news_parser.close()
        # If we created a standalone bot instance, shut it down
        if not context and not bot and bot_instance:
            await bot_instance.shutdown()
