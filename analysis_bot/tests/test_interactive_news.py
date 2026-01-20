import pytest
import re
from unittest.mock import AsyncMock, MagicMock
from telegram import Update, User, Chat, CallbackQuery, Message, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlmodel import Session, create_engine, SQLModel, select

from analysis_bot.bot import jobs, handlers
from analysis_bot import database # Import database module to patch engine on it
from analysis_bot.models.watchlist import WatchlistEntry
from analysis_bot.models.subscriber import Subscriber

# Mocking Telegram objects
class FakeUpdate:
    def __init__(self, callback_query=None, message=None):
        self.callback_query = callback_query
        self.message = message
        self.effective_chat = callback_query.message.chat if callback_query else (message.chat if message else None)
        self.effective_user = callback_query.from_user if callback_query else (message.from_user if message else None)

class FakeCallbackQuery:
    def __init__(self, data, from_user, message):
        self.data = data
        self.from_user = from_user
        self.message = message
        self.answer = AsyncMock()
        self.id = "query_id"

class FakeUser:
    def __init__(self, id, first_name):
        self.id = id
        self.first_name = first_name

class FakeChat:
    def __init__(self, id, type="private"):
        self.id = id
        self.type = type

class FakeMessage:
    def __init__(self, chat, text=""):
        self.chat = chat
        self.text = text
        self.reply_text = AsyncMock()

@pytest.fixture()
def isolated_engine(tmp_path, monkeypatch):
    db_path = tmp_path / "interactive_news.sqlite"
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    
    # Patch engine in modules
    # jobs.py imports engine at top level: from ..database import engine
    monkeypatch.setattr(jobs, "engine", engine)
    
    # handlers.py imports engine INSIDE functions: from ..database import engine
    # So we need to patch analysis_bot.database.engine
    monkeypatch.setattr(database, "engine", engine)
    
    return engine

@pytest.mark.asyncio
async def test_news_job_adds_buttons_for_tickers(isolated_engine):
    # Setup: 1 subscriber
    with Session(isolated_engine) as session:
        session.add(Subscriber(chat_id=100, is_active=True))
        session.commit()

    # Mock Bot and NewsParser
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    
    from analysis_bot.tests.bot_fakes import FakeNewsParser
    parser = FakeNewsParser(
        results_by_key={
            "https://api.cnyes.com/media/api/v1/newslist/category/headline": [
                {"title": "台積電 2330 營收創新高", "url": "http://example.com/1"}
            ]
        }
    )
    
    class FakeContext:
        def __init__(self):
            self.bot = bot
            self.bot_data = {"news_parser": parser}

    # Run Job
    await jobs.check_news_job(context=FakeContext())

    # Verify send_message called with reply_markup
    assert bot.send_message.called
    call_args = bot.send_message.call_args_list[0].kwargs
    assert "reply_markup" in call_args
    markup = call_args["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    
    # Verify button content
    button = markup.inline_keyboard[0][0]
    assert "2330" in button.text
    assert button.callback_data == "NA|ADD|2330"


@pytest.mark.asyncio
async def test_news_action_handler_adds_watchlist(isolated_engine):
    # Setup
    user = FakeUser(id=999, first_name="TestUser")
    chat = FakeChat(id=100)
    message = FakeMessage(chat=chat)
    
    # Simulate clicking "NA|ADD|2330"
    query = FakeCallbackQuery(data="NA|ADD|2330", from_user=user, message=message)
    update = FakeUpdate(callback_query=query)
    context = MagicMock()

    # Run Handler
    await handlers.news_action_handler(update, context)

    # Verify DB
    with Session(isolated_engine) as session:
        entry = session.exec(select(WatchlistEntry).where(WatchlistEntry.user_id == 999)).first()
        assert entry is not None
        assert entry.ticker == "2330"

    # Verify Answer called
    # Check if "已將 2330 加入自選股" in call args
    args = query.answer.call_args[0]
    assert "已將 2330 加入自選股" in args[0]

@pytest.mark.asyncio
async def test_news_action_handler_duplicate_ignore(isolated_engine):
    # Setup: Already in watchlist
    with Session(isolated_engine) as session:
        session.add(WatchlistEntry(chat_id=100, user_id=999, ticker="2330"))
        session.commit()

    user = FakeUser(id=999, first_name="TestUser")
    chat = FakeChat(id=100)
    message = FakeMessage(chat=chat)
    
    query = FakeCallbackQuery(data="NA|ADD|2330", from_user=user, message=message)
    update = FakeUpdate(callback_query=query)
    context = MagicMock()

    await handlers.news_action_handler(update, context)

    # Verify Answer says "Already exists"
    args = query.answer.call_args[0]
    assert "已經在自選股清單中" in args[0]
