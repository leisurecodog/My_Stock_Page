import aiohttp
import asyncio
from typing import Optional, Tuple, List, Dict
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import unquote
import logging
from ..services.data_fetcher import DataFetcher # Re-use utility headers if possible

# Using simple logger for now
logger = logging.getLogger(__name__)

CNYEA_URL_PART = "news.cnyes.com/news/id"

class AnueScraper:
    """Service to scrape estimated EPS and Target Price from Anue (Cnyes)."""
    
    def __init__(self, level: int = 4):
        self.level = level # legacy parameter for EPS table row index?

    async def fetch_estimated_data(self, session: aiohttp.ClientSession, stock_id: str, stock_name: str) -> Optional[Dict]:
        """
        Attempts to find estimate report for the stock.
        Returns dict with {est_price, weighted_eps, date, url} or None.
        """
        search_query = f"鉅亨速報 - Factset 最新調查：{stock_name}({stock_id}-TW)EPS預估+site:news.cnyes.com"
        
        # 1. Search for article URL
        # We try Google or Yahoo. Legacy preferred Yahoo due to anti-bot? 
        # Let's try to simulate what legacy did.
        url = f"https://tw.search.yahoo.com/search?p={search_query}"
        
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            print(f"DEBUG: Searching URL: {url}")
            async with session.get(url, headers=headers, ssl=False) as resp:
                if resp.status != 200:
                    print(f"DEBUG: Search failed with status {resp.status}")
                    return None
                text = await resp.text()
                # print(f"DEBUG: Search result text length: {len(text)}")
                soup = BeautifulSoup(text, "html.parser")
                
            # Parse search results
            target_urls = []
            
            for link in soup.find_all("a"):
                href = link.get("href")
                if href:
                     # Filter for actual news ID pattern to avoid Yahoo junk
                     # Yahoo search results for external sites usually go through a redirect or are direct
                     # We specifically want "news.cnyes.com/news/id"
                     
                     clean_link = href
                     if "RU=" in href:
                         try:
                             clean_link = unquote(href.split("RU=")[1].split("/RK=")[0])
                         except:
                             pass
                     
                     if CNYEA_URL_PART in clean_link:
                         target_urls.append(clean_link)
            
            # Remove duplicates while preserving order
            target_urls = list(dict.fromkeys(target_urls))
            
            print(f"DEBUG: filtered target_urls: {target_urls}")
            if not target_urls:
                return None
                
            # 2. Process articles
            tm_yday = float(datetime.now().timetuple().tm_yday)
            
            candidates = []
            
            for article_url in target_urls:
                print(f"DEBUG: Processing article: {article_url}")
                result = await self._process_article(session, article_url, stock_id, tm_yday)
                if result:
                    candidates.append(result)
            
            if not candidates:
                return None
            
            # Sort by date descending (latest first)
            # Ensure 'date' is a datetime object
            candidates.sort(key=lambda x: x['date'], reverse=True)
            
            print(f"DEBUG: Found {len(candidates)} candidates. Choosing latest: {candidates[0]['date']} - {candidates[0]['url']}")
            return candidates[0]
                    
        except Exception as e:
            logger.error(f"Anue scrape error: {e}")
            print(f"DEBUG: Exception in Anue scrape: {e}")
            
        return None

    async def fetch_all_estimates(self, session: aiohttp.ClientSession, stock_id: str, stock_name: str) -> List[Dict]:
        """
        Fetch ALL available FactSet EPS estimate articles (not just the latest).
        Used by EpsMomentumService to build historical EPS timeline.
        Returns list of dicts sorted by date descending.
        """
        search_query = f"鉅亨速報 - Factset 最新調查：{stock_name}({stock_id}-TW)EPS預估+site:news.cnyes.com"
        url = f"https://tw.search.yahoo.com/search?p={search_query}"

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            async with session.get(url, headers=headers, ssl=False) as resp:
                if resp.status != 200:
                    return []
                text = await resp.text()
                soup = BeautifulSoup(text, "html.parser")

            target_urls = []
            for link in soup.find_all("a"):
                href = link.get("href")
                if href:
                    clean_link = href
                    if "RU=" in href:
                        try:
                            clean_link = unquote(href.split("RU=")[1].split("/RK=")[0])
                        except:
                            pass
                    if CNYEA_URL_PART in clean_link:
                        target_urls.append(clean_link)

            target_urls = list(dict.fromkeys(target_urls))
            if not target_urls:
                return []

            tm_yday = float(datetime.now().timetuple().tm_yday)
            candidates = []
            for article_url in target_urls:
                result = await self._process_article(session, article_url, stock_id, tm_yday)
                if result:
                    candidates.append(result)

            candidates.sort(key=lambda x: x['date'], reverse=True)
            return candidates

        except Exception as e:
            logger.error(f"Anue fetch_all_estimates error: {e}")

        return []

    async def _process_article(self, session, url, stock_id, tm_yday) -> Optional[Dict]:
        try:
            async with session.get(url, ssl=False) as resp:
                if resp.status != 200: return None
                text = await resp.text()
                soup = BeautifulSoup(text, "html.parser")
                
            # Logic ported from legacy __process_page
            # 1. Check title
            article_div = soup.find(id="article-container") # Legacy ID
            if not article_div: 
                # Try finding h1 if id changed
                h1 = soup.find('h1')
                if h1: title_text = h1.text
                else: return None
            else:
                title_text = article_div.text # This gets full text? Legacy said "webtitle = article.text"

            # Check if title strictly matches format "Name(ID-TW)..."
            if str(stock_id) not in title_text:
                return None
                
            # 2. Extract Target Price
            # "預估目標價為xxx元"
            est_price = None
            if "預估目標價為" in title_text:
                try:
                    est_price = float(title_text.split("預估目標價為")[1].split("元")[0])
                except: pass
            
            # 3. Extract EPS Table
            weighted_eps = None
            table = soup.find("table")
            if table:
                rows = table.find_all("tr")
                if len(rows) > 1:
                    headers = [td.get_text(strip=True) for td in rows[0].find_all("td")]
                    # Check "預估值" in header
                    if headers and "預估值" in headers[0]:
                        # Legacy uses self.level to pick a row (default 4 -> "Medium" estimate?)
                        # Legacy index safety check
                        # self.level is 4 (Median). Table has 5 rows (Header + 4 data).
                        # rows[0] is Header. rows[1]..rows[4] are data.
                        # So rows[self.level] should be the target row.
                        target_row_idx = self.level
                        if target_row_idx < len(rows):
                            row = rows[target_row_idx]
                            cols = [td.get_text(strip=True) for td in row.find_all("td")]
                            
                            # Calculate weighted EPS
                            current_year = str(datetime.now().year)
                            # Find column for current year
                            for idx, h in enumerate(headers):
                                if current_year in h:
                                    # Logic: Weighted average of this year and next year
                                    try:
                                        this_year_eps = float(cols[idx].split("(")[0])
                                        next_year_eps = this_year_eps
                                        if idx + 1 < len(cols):
                                             try:
                                                next_year_eps = float(cols[idx+1].split("(")[0])
                                             except: pass
                                        
                                        weighted_eps = ((366 - tm_yday) / 366) * this_year_eps + \
                                                       (tm_yday / 366) * next_year_eps
                                    except: pass
                                    break

            # 4. Extract Date
            article_date = None
            
            # Try meta tag first
            meta_date = soup.find("meta", property="article:published_time")
            if meta_date and meta_date.get("content"):
                try:
                    # Try simplified date parse if standard fails
                    # Anue meta sometimes has Chinese like "2024/1/19 下午5:11:18"
                    # We can try to parse it or just fall back
                    article_date = datetime.fromisoformat(meta_date["content"])
                except:
                    pass
            
            # Fallback to time tag if meta failed
            if not article_date:
                time_tag = soup.find("time")
                if time_tag and time_tag.get("datetime"):
                    try:
                        # "2024-01-19T09:11:18.000Z" - Python 3.13 handles Z
                        article_date = datetime.fromisoformat(time_tag["datetime"])
                    except: pass
                    
            # Final fallback to now if everything failed
            if not article_date:
                article_date = datetime.now()

            if est_price or weighted_eps:
                return {
                    "est_price": est_price,
                    "est_eps": weighted_eps, # Weighted
                    "url": url,
                    "date": article_date.isoformat() if article_date else None
                }

        except Exception as e:
            pass
            
        return None
