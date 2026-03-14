import sys
import asyncio
sys.path.append('.')
from analysis_bot.services.stock_analyzer import StockAnalyzer

async def main():
    analyzer = StockAnalyzer()
    res = await analyzer.analyze_stock("2330")
    print(f"Price: {res['price']}")
    from pprint import pprint
    mr = res['analysis']['mean_reversion']
    print(f"Bands array length: {len(mr['bands']['TL'])}")
    print("Latest Bands values:")
    for k, v in mr['bands'].items():
        print(f"  {k}: {v[-1]}")
    
asyncio.run(main())
