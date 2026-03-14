import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

import aiohttp
from sqlmodel import Session, select, col

from ..database import engine
from ..models.eps_estimate import EpsEstimate
from .anue_scraper import AnueScraper

logger = logging.getLogger(__name__)


class EpsMomentumService:
    """
    Collects historical FactSet EPS estimates and calculates
    EPS revision momentum signals.
    """

    def __init__(self):
        self.anue = AnueScraper()

    async def collect_and_analyze(
        self, ticker: str, stock_name: str
    ) -> Dict[str, Any]:
        """
        1. Fetch all available FactSet articles from 鉅亨網
        2. Store new snapshots into EpsEstimate table (deduplicate by source_url)
        3. Calculate momentum metrics from the stored history
        Returns a dict ready for ReportGenerator consumption.
        """
        # --- Step 1: Fetch & Store ---
        async with aiohttp.ClientSession() as session:
            all_estimates = await self.anue.fetch_all_estimates(
                session, ticker, stock_name
            )

        if all_estimates:
            self._store_estimates(ticker, all_estimates)

        # --- Step 2: Load history from DB ---
        history = self._load_history(ticker)
        if len(history) < 2:
            # Not enough data points to calculate momentum
            return {}

        # --- Step 3: Calculate metrics ---
        return self._calculate_momentum(history)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _store_estimates(self, ticker: str, estimates: List[Dict]) -> None:
        """Deduplicate by source_url and insert new records."""
        with Session(engine) as session:
            existing_urls = set(
                session.exec(
                    select(EpsEstimate.source_url).where(
                        EpsEstimate.ticker == ticker
                    )
                ).all()
            )

            new_count = 0
            for est in estimates:
                url = est.get("url")
                if url and url in existing_urls:
                    continue

                eps_val = est.get("est_eps")
                if eps_val is None:
                    continue

                # Parse source_date
                source_date = est.get("date")
                if isinstance(source_date, str):
                    try:
                        source_date = datetime.fromisoformat(source_date)
                    except ValueError:
                        source_date = datetime.utcnow()

                record = EpsEstimate(
                    ticker=ticker,
                    est_eps=float(eps_val),
                    est_price=est.get("est_price"),
                    source_date=source_date or datetime.utcnow(),
                    source_url=url,
                )
                session.add(record)
                if url:
                    existing_urls.add(url)
                new_count += 1

            if new_count:
                session.commit()
                logger.info(
                    f"EpsMomentum: stored {new_count} new estimate(s) for {ticker}"
                )

    def _load_history(self, ticker: str) -> List[EpsEstimate]:
        """Load EPS estimates for a ticker within the last 1 year, ordered by source_date ASC."""
        from datetime import timedelta

        one_year_ago = datetime.utcnow() - timedelta(days=365)
        with Session(engine) as session:
            results = session.exec(
                select(EpsEstimate)
                .where(EpsEstimate.ticker == ticker)
                .where(EpsEstimate.source_date >= one_year_ago)
                .order_by(col(EpsEstimate.source_date).asc())
            ).all()
            return list(results)

    @staticmethod
    def _calculate_momentum(history: List[EpsEstimate]) -> Dict[str, Any]:
        """
        Calculate EPS revision momentum from historical snapshots.
        Returns dict with: history, eps_change_pct, eps_trend,
        total_revision_pct, signal.
        """
        # Build timeline
        timeline = []
        for h in history:
            timeline.append(
                {
                    "date": (
                        h.source_date.strftime("%Y-%m-%d")
                        if isinstance(h.source_date, datetime)
                        else str(h.source_date)
                    ),
                    "est_eps": round(h.est_eps, 2),
                    "est_price": round(h.est_price, 2) if h.est_price else None,
                }
            )

        # Latest vs previous
        latest = history[-1]
        previous = history[-2]
        eps_change_pct = (
            ((latest.est_eps - previous.est_eps) / abs(previous.est_eps)) * 100
            if previous.est_eps != 0
            else 0.0
        )

        # Total revision: first vs latest
        first = history[0]
        total_revision_pct = (
            ((latest.est_eps - first.est_eps) / abs(first.est_eps)) * 100
            if first.est_eps != 0
            else 0.0
        )

        # Trend: check last 3 data points (or all if fewer)
        recent = history[-3:] if len(history) >= 3 else history
        ups = 0
        downs = 0
        for i in range(1, len(recent)):
            if recent[i].est_eps > recent[i - 1].est_eps:
                ups += 1
            elif recent[i].est_eps < recent[i - 1].est_eps:
                downs += 1

        if ups > downs:
            eps_trend = "連續上修"
        elif downs > ups:
            eps_trend = "連續下修"
        else:
            eps_trend = "持平"

        # Signal
        if eps_change_pct > 3:
            signal = "強烈正面"
        elif eps_change_pct > 0:
            signal = "正面"
        elif eps_change_pct > -3:
            signal = "中性"
        elif eps_change_pct > -10:
            signal = "負面"
        else:
            signal = "強烈負面"

        return {
            "history": timeline,
            "eps_change_pct": round(eps_change_pct, 2),
            "eps_trend": eps_trend,
            "total_revision_pct": round(total_revision_pct, 2),
            "signal": signal,
        }
