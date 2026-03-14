import sys
import asyncio
sys.path.append('.')
from analysis_bot.services.stock_analyzer import StockAnalyzer
from analysis_bot.services.report_generator import ReportGenerator

async def main():
    analyzer = StockAnalyzer()
    res = await analyzer.analyze_stock("2330") # TSMC
    print(ReportGenerator.generate_full_report(res))

asyncio.run(main())
