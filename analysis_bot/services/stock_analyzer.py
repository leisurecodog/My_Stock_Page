from typing import Dict, Any, Optional
import numpy as np
import pandas as pd
import aiohttp
import asyncio
from datetime import datetime, timedelta
from .data_fetcher import DataFetcher
from .math_utils import MathUtils
from .finmind_fetcher import FinMindFetcher
from .anue_scraper import AnueScraper
from .eps_momentum_service import EpsMomentumService
from ..config import get_settings

settings = get_settings()

class StockAnalyzer:
    """Core service for stock analysis and valuation."""
    
    def __init__(self):
        self.fetcher = DataFetcher()
        self.finmind = FinMindFetcher() # Tokens loaded from settings automatically
        self.anue = AnueScraper()

    async def analyze_stock(self, ticker: str, lohas_years: float = 3.5) -> Dict[str, Any]:
        """
        Perform comprehensive analysis on a stock.
        """
        try:
            # 1. Fetch Basic Price Data (Yahoo)
            # 1. Fetch Basic Price Data (Yahoo)
            search_ticker = ticker
            data = None
            
            if ticker.isdigit():
                # Try .TW first
                search_ticker = f"{ticker}.TW"
                try:
                    data = self.fetcher.fetch_yahoo_data(search_ticker)
                    if data["history"].empty:
                        # Fallback trigger
                        raise ValueError("Empty history")
                except Exception:
                    # If failed (exception or empty), try .TWO
                    search_ticker = f"{ticker}.TWO"
                    data = self.fetcher.fetch_yahoo_data(search_ticker)
            else:
                 # User provided suffix or US stock
                 data = self.fetcher.fetch_yahoo_data(search_ticker)
            
            history: pd.DataFrame = data["history"]
            info: Dict = data["info"]
            
            if history.empty:
                return {"error": f"No data found for {ticker} (tried .TW and .TWO)" if ticker.isdigit() else f"No data found for {ticker}"}
            
            # Debug: Check Yahoo Info fields
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"Yahoo Info Keys for {ticker}: {list(info.keys())[:20]}...")
            logger.info(f"TargetMeanPrice: {info.get('targetMeanPrice')}, PEG: {info.get('pegRatio')}")
            
            # 2. Fetch Advanced Data (FinMind) - if numeric
            per_list, pbr_list = [], []
            anue_data = None
            if ticker.isdigit():
                async with aiohttp.ClientSession() as session:
                     start_date = (datetime.now() - timedelta(days=5*365)).strftime("%Y-%m-%d")
                     # Fetch concurrently
                     per_pbr_task = self.finmind.get_per_pbr(session, ticker, start_date)
                     
                     # Fetch FinMind info first to get accurate Chinese name
                     fm_info = await self.finmind.get_stock_info(session, ticker)
                     
                     # Update info with FinMind data immediately
                     if fm_info:
                         if fm_info.get("name"): info["longName"] = fm_info["name"]
                         if fm_info.get("sector"): info["sector"] = fm_info["sector"]
                         if fm_info.get("exchange"): info["exchange"] = fm_info["exchange"]
                     
                     # Now fetch Anue using the (potentially updated) name and concurrent PER/PBR
                     anue_task = self.anue.fetch_estimated_data(session, ticker, info.get("longName", "") or ticker)
                     
                     (per_list, pbr_list), anue_data = await asyncio.gather(per_pbr_task, anue_task)

                     # Fallback calculation for Estimated PE
                     if anue_data and anue_data.get("est_eps") and not anue_data.get("est_pe"):
                         try:
                             # Use current price if available from info or previous fetch
                             curr_price = info.get("currentPrice") or info.get("regularMarketPrice")
                             if not curr_price and not history.empty and "Close" in history.columns:
                                 curr_price = history["Close"].iloc[-1]
                             
                             if curr_price and anue_data["est_eps"] > 0:
                                 anue_data["est_pe"] = round(curr_price / anue_data["est_eps"], 2)
                         except Exception:
                             pass

        except Exception as e:
            return {"error": f"Failed to fetch data for {ticker}: {str(e)}"}

        # Extract Key Metrics
        # Align everything to valid data points
        valid_history = history.dropna(subset=["Close"])
        price_series = valid_history["Close"].tolist()
        
        current_price = info.get("currentPrice") or info.get("regularMarketPrice") or (price_series[-1] if price_series else 0)
        
        # Financials from Yahoo (Fallback)
        eps_ttm = info.get("trailingEps")
        forward_eps = info.get("forwardEps")
        
        # Additional fields updates
        bps = info.get("bookValue")
        
        # Fallback BPS calculation if missing
        if not bps:
            pb_ratio = info.get("priceToBook")
            if current_price and pb_ratio:
                try:
                    bps = current_price / pb_ratio
                except:
                    pass
        
        target_mean_price = info.get("targetMeanPrice")
        gross_margins = info.get("grossMargins")
        
        # 3. Mean Reversion Analysis (Price based - default to 3.5 years / ~882 trading days)
        target_days = int(lohas_years * 252)
        lohas_series = price_series[-target_days:] if len(price_series) > target_days else price_series
        mr_analysis = MathUtils.mean_reversion(lohas_series)
        mr_analysis["lohas_years"] = lohas_years # Store for report awareness

        # 3.5. EPS Momentum (FactSet historical estimates)
        eps_momentum = {}
        if ticker.isdigit():
            try:
                eps_momentum_svc = EpsMomentumService()
                stock_name_for_search = info.get("longName", ticker) or ticker
                eps_momentum = await eps_momentum_svc.collect_and_analyze(
                    ticker, stock_name_for_search
                )
            except Exception as e:
                logger.warning(f"EPS Momentum analysis failed for {ticker}: {e}")

        # 4. PE/PB Analysis (using FinMind if available)
        pe_analysis = {}
        pb_analysis = {}
        
        if per_list:
            pe_analysis["quartile"] = MathUtils.quartile(per_list)
            pe_bands, _ = MathUtils.std(per_list)
            # Convert dict of arrays to list of current values (Low to High: -3SD to +3SD)
            # MathUtils keys are: TL, TL+1SD... TL-3SD
            # We want: TL-3SD, TL-2SD, TL-1SD, TL, TL+1SD, TL+2SD, TL+3SD
            pe_ordered_keys = ["TL-3SD", "TL-2SD", "TL-1SD", "TL", "TL+1SD", "TL+2SD", "TL+3SD"]
            pe_analysis["bands"] = [float(pe_bands[k][-1]) for k in pe_ordered_keys if k in pe_bands]

            if info.get("trailingPE"):
                pe_analysis["percentile"] = MathUtils.percentile_rank(per_list, info.get("trailingPE"))
            else:
                pe_analysis["percentile"] = 50.0
        
        if pbr_list:
            pb_analysis["quartile"] = MathUtils.quartile(pbr_list)
            pb_bands, _ = MathUtils.std(pbr_list)
            # Convert dict of arrays to list of current values (Low to High)
            pb_ordered_keys = ["TL-3SD", "TL-2SD", "TL-1SD", "TL", "TL+1SD", "TL+2SD", "TL+3SD"]
            pb_analysis["bands"] = [float(pb_bands[k][-1]) for k in pb_ordered_keys if k in pb_bands]

            if info.get("priceToBook"):
                pb_analysis["percentile"] = MathUtils.percentile_rank(pbr_list, info.get("priceToBook"))
            else:
                 pb_analysis["percentile"] = 50.0

        analysis_result = {
            "ticker": ticker,
            "name": info.get("longName", ticker),
            "sector": info.get("sector"),
            "price": current_price,
            "exchange": info.get("exchange"),
            "financials": {
                "eps_ttm": eps_ttm,
                "forward_eps": forward_eps,
                "pe_ttm": info.get("trailingPE"),
                "pb": info.get("priceToBook"),
                "pb_ttm": info.get("priceToBook"), # Keep for backward compatibility if needed
                "peg_ratio": info.get("pegRatio"),
                "book_value": bps,
                "bps": bps, # Add alias for ReportGenerator
                "target_mean_price": target_mean_price,
                "gross_margins": gross_margins, 
                "long_business_summary": info.get("longBusinessSummary"),
            },
            "estimates": anue_data, # From Anue
            "analysis": {
                "mean_reversion": mr_analysis,
                "pe_stats": pe_analysis,
                "pb_stats": pb_analysis,
                "eps_momentum": eps_momentum,
            },
            "chart_data": {
                "dates": [d.strftime("%Y-%m-%d") for d in valid_history.index.tolist()],
                "close": price_series
            },
            "last_updated": datetime.now().isoformat()
        }
        
        return analysis_result
