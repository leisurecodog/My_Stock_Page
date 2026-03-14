from sqlmodel import create_engine, SQLModel, Session
from .config import get_settings

settings = get_settings()

# check_same_thread=False is needed for SQLite with FastAPI/Async
engine = create_engine(
    settings.DATABASE_URL, 
    echo=settings.DEBUG, 
    connect_args={"check_same_thread": False}
)

def create_db_and_tables():
    # Helper to import all models so SQLModel metadata is populated
    from .models.stock import StockData
    from .models.subscriber import Subscriber
    from .models.config import SystemConfig
    from .models.content import News, Report, Podcast
    from .models.watchlist import WatchlistEntry
    from .models.eps_estimate import EpsEstimate
    
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session
