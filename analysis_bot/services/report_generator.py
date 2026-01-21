from typing import Dict, Any

class ReportGenerator:
    """Generates formatted text reports for stock analysis."""
    
    @staticmethod
    def generate_full_report(data: Dict[str, Any]) -> str:
        """Generates a comprehensive report similar to legacy output."""
        ticker = data['ticker']
        name = data['name']
        price = data['price']
        fin = data.get("financials", {})
        analysis = data.get("analysis", {})
        est = data.get("estimates") or {}

        # Header
        report = []
        report.append("="*76)
        report.append(f"股票名稱: {name:<10}\t股票代號: {ticker:<15}")
        report.append(f"公司產業: {data.get('sector', 'N/A')}\t\t交易所: {data.get('exchange', 'N/A')}")
        
        summary = fin.get('business_summary', '')
        if summary:
            # Show full summary as requested
            report.append(f"公司資訊: {summary}")
        
        report.append("")
        report.append(f"目前股價: {price:>10.2f}\t\t毛利率: {fin.get('gross_margins', 'N/A')}")
        report.append(f"EPS(TTM): {fin.get('eps_ttm', 'N/A'):>10}\t\tBPS: {fin.get('bps', 'N/A'):>10}")
        report.append(f"PE(TTM): {fin.get('pe_ttm', 'N/A'):>10}\t\tPB(TTM): {fin.get('pb_ttm', 'N/A'):>10}")
        
        # Yahoo Target
        report.append("="*76)
        report.append("Yahoo Finance 1y Target Est....")
        report.append("")
        target_price = fin.get('target_mean_price')
        if target_price:
            potential = ((target_price - price) / price) * 100 if price else 0
            # Note: User example had "預估eps" here too, but Yahoo doesn't give forward EPS easily in summary
            # We skip EPS here unless we have it from another source
            report.append(f"目標價位: {target_price:>10.2f}\t\t潛在漲幅: {potential:>10.2f}%")
        else:
             report.append("N/A")
             
        # Mean Reversion
        mr = analysis.get('mean_reversion', {})
        if mr:
            report.append("="*76)
            report.append("股價均值回歸......")
            report.append("")
            report.append("均值回歸適合使用在穩定成長的股票上，如大盤or台積電等，高速成長及景氣循環股不適用，請小心服用。")
            report.append("偏離越多標準差越遠代表趨勢越強，請勿直接進場。")
            report.append("")
            
            probs = mr.get('prob', [0, 0, 0])
            report.append(f"{ticker} 往上的機率為: {probs[0]:>10.2f}%, 維持在這個區間的機率為: {probs[1]:>10.2f}%, 往下的機率為: {probs[2]:>10.2f}%")
            
            tl_val = mr['TL'][0]
            if tl_val is not None:
                tl_growth = mr.get('targetprice', [])[3] - price # TL is index 3
                report.append(f"目前股價: {price:>10.2f}, TL價: {tl_val:>10.2f}, TL價潛在漲幅: {tl_growth:>10.2f}")
            else:
                report.append(f"目前股價: {price:>10.2f}, TL價: N/A, TL價潛在漲幅: N/A")
            
            expects = mr.get('expect', [0, 0, 0])
            
            # ROI Calculations
            roi_bull_1 = (expects[0] / price) * 100 if price else 0
            roi_bull_2 = (expects[1] / price) * 100 if price else 0
            roi_bear = (expects[2] / price) * 100 if price else 0
            
            report.append("做多評估：")
            report.append(f"期望值為: {expects[0]:>10.2f}, 期望報酬率為: {roi_bull_1:>10.2f}% (保守計算: 上檔TL，下檔歸零)")
            report.append(f"期望值為: {expects[1]:>10.2f}, 期望報酬率為: {roi_bull_2:>10.2f}% (樂觀計算: 上檔TL，下檔-3SD)")
            report.append("")
            report.append("做空評估: ")
            report.append(f"期望值為: {expects[2]:>10.2f}, 期望報酬率為: {roi_bear:>10.2f}% (樂觀計算: 上檔+3SD，下檔TL)")
            
            # Bands (Lohas Spectrum)
            report.append("="*76)
            report.append("樂活五線譜......")
            report.append("")
            
            labels_map = {
                0: "超極樂觀價位", # TL+3SD
                1: "極樂觀價位",   # TL+2SD
                2: "樂觀價位",     # TL+1SD
                3: "趨勢價位",     # TL
                4: "悲觀價位",     # TL-1SD
                5: "極悲觀價位",   # TL-2SD
                6: "超極悲觀價位"  # TL-3SD
            }
            
            targets = mr.get('targetprice', [])
            for i, target in enumerate(targets):
                if i in labels_map:
                    target = targets[i]
                    if target is not None:
                        pot = ((target - price) / price) * 100 if price else 0
                        report.append(f"    {labels_map[i]:<10}: {target:>10.2f}, 潛在漲幅: {pot:>10.2f}%")
                    else:
                        report.append(f"    {labels_map[i]:<10}: N/A, 潛在漲幅: N/A")
        
        # Factset / Anue
        if est:
            report.append("="*76)
            report.append("Factest預估")
            report.append("")
            est_eps = est.get('est_eps')
            est_price = est.get('est_price')
            
            pe_est = (price / est_eps) if est_eps and price else "N/A"
            if isinstance(pe_est, float): pe_est = f"{pe_est:.2f}"
            
            pot_est = ((est_price - price) / price * 100) if est_price and price else "N/A"
            if isinstance(pot_est, float): pot_est = f"{pot_est:.2f}"
            
            report.append(f"估計EPS: {est_eps if est_eps else 'N/A':>10}  預估本益比： {pe_est:>10}")
            report.append(f"Factest目標價: {est_price if est_price else 'N/A':>10}  推算潛在漲幅為: {pot_est:>10}")
            report.append(f"資料日期: {est.get('date', 'N/A')}  ")
            report.append(f"url: {est.get('url', 'N/A')}")
            
        # PE / PB Stats
        pe_stats = analysis.get('pe_stats', {})
        if pe_stats:
            report.append("="*76)
            report.append("本益比四分位數與平均本益比......")
            report.append("")
            # Quartile Logic
            quartiles = pe_stats.get("quartile", [])
            eps_ttm = fin.get('eps_ttm')
            
            if quartiles:
                labels = ["PE 25%", "PE 50%", "PE 75%", "PE平均%"]
                for i, val in enumerate(quartiles):
                    target = val * eps_ttm if isinstance(eps_ttm, (int, float)) else "N/A"
                    pot = ((target - price) / price) * 100 if isinstance(target, (int, float)) and price else "N/A"
                    
                    target_str = f"{target:.2f}" if isinstance(target, (int, float)) else "N/A"
                    pot_str = f"{pot:.2f}%" if isinstance(pot, (int, float)) else "N/A"
                    
                    report.append(f"{labels[i]:<8}: {val:>10.2f}          目標價位: {target_str:>10}          潛在漲幅: {pot_str:>10}")
            
            report.append("")
            report.append("="*76)
            report.append("本益比標準差......")
            report.append("")
            # Bands Logic
            sd_bands = pe_stats.get("bands", {})
            if sd_bands:
               ordered_keys = [f"TL+{i}SD" for i in range(3, 0, -1)] + ["TL"] + [f"TL-{i}SD" for i in range(1, 4)]
               
               for key in ordered_keys:
                   if key in sd_bands:
                       band_val = sd_bands[key]
                       if isinstance(band_val, list):
                           band_val = band_val[-1]
                       
                       target = band_val * eps_ttm if isinstance(eps_ttm, (int, float)) else "N/A"
                       pot = ((target - price) / price) * 100 if isinstance(target, (int, float)) and price else "N/A"
                       
                       target_str = f"{target:.2f}" if isinstance(target, (int, float)) else "N/A"
                       pot_str = f"{pot:.2f}%" if isinstance(pot, (int, float)) else "N/A"
                       
                       report.append(f"PE {key:<5}: {band_val:>10.2f}          目標價位: {target_str:>10}          潛在漲幅: {pot_str:>10}")

        pb_stats = analysis.get('pb_stats', {})
        if pb_stats:
            report.append("="*76)
            report.append("股價淨值比四分位數與平均本益比......")
            report.append("")
            
            quartiles = pb_stats.get("quartile", [])
            bps = fin.get('bps')
            
            if quartiles:
                labels = ["PB 25%", "PB 50%", "PB 75%", "PB 平均"]
                for i, val in enumerate(quartiles):
                    target = val * bps if isinstance(bps, (int, float)) else "N/A"
                    pot = ((target - price) / price) * 100 if isinstance(target, (int, float)) and price else "N/A"
                    
                    target_str = f"{target:.2f}" if isinstance(target, (int, float)) else "N/A"
                    pot_str = f"{pot:.2f}%" if isinstance(pot, (int, float)) else "N/A"
                    
                    report.append(f"{labels[i]:<8}: {val:>10.2f}          目標價位: {target_str:>10}          潛在漲幅: {pot_str:>10}")
                    
            report.append("")
            report.append("="*76)
            report.append("股價淨值比標準差......")
            report.append("")
            
            sd_bands = pb_stats.get("bands", {})
            if sd_bands:
               ordered_keys = [f"TL+{i}SD" for i in range(3, 0, -1)] + ["TL"] + [f"TL-{i}SD" for i in range(1, 4)]
               for key in ordered_keys:
                   if key in sd_bands:
                       band_val = sd_bands[key]
                       if isinstance(band_val, list):
                           band_val = band_val[-1]
                       
                       target = band_val * bps if isinstance(bps, (int, float)) else "N/A"
                       pot = ((target - price) / price) * 100 if isinstance(target, (int, float)) and price else "N/A"
                       
                       target_str = f"{target:.2f}" if isinstance(target, (int, float)) else "N/A"
                       pot_str = f"{pot:.2f}%" if isinstance(pot, (int, float)) else "N/A"
                       
                       report.append(f"PB {key:<5}: {band_val:>10.2f}          目標價位: {target_str:>10}          潛在漲幅: {pot_str:>10}")

        report.append("="*76)
        
        return "\n".join(report)

    @staticmethod
    def generate_telegram_report(data: Dict[str, Any]) -> str:
        """Generates the Telegram report in the specific user-requested format."""
        
        # Helper for handling None/Empty values safely
        def get_val(val, default=0):
            if val is None or val == "":
                return default
            return val

        def get_profit(target, current):
            try:
                t = float(target)
                c = float(current)
                if c == 0: return 0.0
                return ((t - c) / c) * 100
            except (TypeError, ValueError):
                return 0.0

        ticker = data.get('ticker', '')
        name = data.get('name', '')
        price = float(get_val(data.get('price'), 0))
        fin = data.get("financials", {})
        analysis = data.get("analysis", {})
        est = data.get("estimates") or {}

        # 1. Basic Info
        text = f"""
股票名稱: {name:<10}\t股票代號: {ticker:<15}
公司產業: {get_val(data.get('sector'), 'N/A')}\t\t交易所: {get_val(data.get('exchange'), 'N/A')}
公司資訊: {get_val(fin.get('long_business_summary'), 'N/A')}

目前股價: {price:>10.2f}\t\t毛利率: {get_val(fin.get('gross_margins'), 'N/A')}
EPS(TTM): {get_val(fin.get('eps_ttm'), 0):>10.2f}          BPS: {get_val(fin.get('bps'), 0):>10.2f}
PE(TTM): {get_val(fin.get('pe_ttm'), 0):>10.2f}          PB(TTM): {get_val(fin.get('pb_ttm'), 0):>10.2f}
"""

        # 2. Yahoo Finance Target
        target_price = fin.get('target_mean_price')
        if target_price:
            profit = get_profit(target_price, price)
            est_eps_yahoo = get_val(fin.get('forward_eps'), "N/A")
            
            text += f"""
============================================================================
Yahoo Finance 1y Target Est....

預估eps: {est_eps_yahoo}
目標價位: {float(target_price):>10.2f}          潛在漲幅: {profit:>10.2f}%
"""

        # 3. Mean Reversion
        mr = analysis.get('mean_reversion', {})
        if mr:
            probs = mr.get('prob', [0, 0, 0])
            tl_price = mr.get('TL', [0])[0]
            tl_profit = get_profit(tl_price, price)
            expects = mr.get('expect', [0, 0, 0])
            
            roi_1 = (expects[0] / price * 100) if price else 0
            roi_2 = (expects[1] / price * 100) if price else 0
            roi_3 = (expects[2] / price * 100) if price else 0

            text += f"""
============================================================================
股價均值回歸......

均值回歸適合使用在穩定成長的股票上，如大盤or台積電等，高速成長及景氣循環股不適用，請小心服用。
偏離越多標準差越遠代表趨勢越強，請勿直接進場。

{ticker} 往上的機率為: {probs[0]:>10.2f}%, 維持在這個區間的機率為: {probs[1]:>10.2f}%, 往下的機率為: {probs[2]:>10.2f}%

目前股價: {price:>10.2f}, TL價: {tl_price:>10.2f}, TL價潛在漲幅: {tl_profit:>10.2f}
做多評估：
期望值為: {expects[0]:>10.2f}, 期望報酬率為: {roi_1:>10.2f}% (保守計算: 上檔TL，下檔歸零)
期望值為: {expects[1]:>10.2f}, 期望報酬率為: {roi_2:>10.2f}% (樂觀計算: 上檔TL，下檔-3SD)

做空評估: 
期望值為: {expects[2]:>10.2f}, 期望報酬率為: {roi_3:>10.2f}% (樂觀計算: 上檔+3SD，下檔TL)
"""

        # 4. Lohas 5-Line
        if mr and 'targetprice' in mr:
            targets = mr['targetprice']
            labels = ["超極樂觀價位", "極樂觀價位", "樂觀價位", "趨勢價位", "悲觀價位", "極悲觀價位", "超極悲觀價位"]
            
            text += f"""
============================================================================
樂活五線譜......      


"""
            for i, label in enumerate(labels):
                if i < len(targets):
                    tp = targets[i]
                    pot = get_profit(tp, price)
                    text += f"    {label}: {tp:>10.2f}, 潛在漲幅: {pot:>10.2f}%\n"

        # 5. FactSet Estimates
        est_eps = est.get('est_eps')
        if est_eps:
            est_target = est.get('est_price', 0)
            est_pe = est.get('est_pe', 0)
            pot = get_profit(est_target, price)
            
            text += f"""
============================================================================
Factest預估

估計EPS: {str(est_eps):>10}  預估本益比： {str(est_pe):>10}
Factest目標價: {str(est_target):>10}  推算潛在漲幅為: {pot:>10.2f}
資料日期: {est.get('date', 'N/A')}  
url: {est.get('url', 'N/A')}
"""

        # Used EPS/BPS
        eps_use = est_eps if est_eps else get_val(fin.get('eps_ttm'), 0)
        bps_use = get_val(fin.get('bps'), 0)
        
        text += f"""
****************************************************************************
*                           以下資料使用的EPS, BPS                         *
*                        EPS: {float(eps_use):>10.2f} BPS: {float(bps_use):>10.2f}                   *    
****************************************************************************
"""

        # 6. PE Stats
        pe_stats = analysis.get('pe_stats', {})
        if pe_stats:
            quartile = pe_stats.get('quartile', [])
            bands = pe_stats.get('bands', [])
            
            if quartile:
                q_labels = ["PE 25%", "PE 50%", "PE 75%", "PE平均%"]
                text += f"""
============================================================================
本益比四分位數與平均本益比......

"""
                for i, q_val in enumerate(quartile):
                    if i < 4:
                        tp = float(q_val) * float(eps_use)
                        pot = get_profit(tp, price)
                        text += f"{q_labels[i]} : {float(q_val):>10.2f}          目標價位: {tp:>10.2f}          潛在漲幅: {pot:>10.2f}%\n"

            if bands:
                b_labels = ["PE TL+3SD", "PE TL+2SD", "PE TL+1SD", "PE TL    ", "PE TL-1SD", "PE TL-2SD", "PE TL-3SD"]
                reversed_bands = list(reversed(bands))
                
                text += f"""
============================================================================
本益比標準差......

"""
                for i, label in enumerate(b_labels):
                    if i < len(reversed_bands):
                        val = reversed_bands[i]
                        tp = val * float(eps_use)
                        pot = get_profit(tp, price)
                        text += f"{label}: {val:>10.2f}          目標價位: {tp:>10.2f}          潛在漲幅: {pot:>10.2f}%\n"

        # 7. PB Stats
        pb_stats = analysis.get('pb_stats', {})
        if pb_stats:
            quartile = pb_stats.get('quartile', [])
            bands = pb_stats.get('bands', [])
            
            if quartile:
                q_labels = ["PB 25%", "PB 50%", "PB 75%", "PB 平均 "]
                text += f"""
============================================================================
股價淨值比四分位數與平均本益比......

"""
                for i, q_val in enumerate(quartile):
                    if i < 4:
                        tp = float(q_val) * float(bps_use)
                        pot = get_profit(tp, price)
                        text += f"{q_labels[i]} : {float(q_val):>10.2f}           目標價位: {tp:>10.2f}          潛在漲幅: {pot:>10.2f}%\n"
            
            if bands:
                b_labels = ["PB TL+3SD", "PB TL+2SD", "PB TL+1SD", "PB TL     ", "PB TL-1SD", "PB TL-2SD", "PB TL-3SD"]
                reversed_bands = list(reversed(bands))
                
                text += f"""
============================================================================
股價淨值比標準差......

"""
                for i, label in enumerate(b_labels):
                    if i < len(reversed_bands):
                        val = reversed_bands[i]
                        tp = val * float(bps_use)
                        pot = get_profit(tp, price)
                        text += f"{label}: {val:>10.2f}           目標價位: {tp:>10.2f}          潛在漲幅: {pot:>10.2f}%\n"

        # 8. PEG
        peg = fin.get('peg_ratio')
        if peg:
             pe_ttm = fin.get('pe_ttm')
             growth = 0
             if pe_ttm and float(peg) != 0:
                 growth = float(pe_ttm) / float(peg)
             
             fair_price = growth * float(eps_use)
             target_peg = f"{fair_price:>10.2f}"
             pot_peg_val = get_profit(fair_price, price)
             pot_peg = f"{pot_peg_val:>10.2f}%"

             text += f"""
============================================================================
PEG估值......

PEG:       {float(peg):<10.2f}           EPS成長率:      {growth:.2f}
目標價位:    {target_peg}          潛在漲幅:     {pot_peg}
"""

        return text
