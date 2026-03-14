from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class EpsEstimate(SQLModel, table=True):
    """Historical FactSet EPS estimate snapshots from 鉅亨網."""
    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(index=True)
    est_eps: float
    est_price: Optional[float] = None
    source_date: datetime = Field(index=True)
    source_url: Optional[str] = None
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
