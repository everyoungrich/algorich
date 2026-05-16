import time
from datetime import datetime, timedelta
import pandas as pd
import matplotlib.pyplot as plt
import yfinance as yf

# KOSPI / KOSDAQ 시가총액 상위 대표 우량주 100선 
TARGET_TICKERS = [
    "005930.KS", "000660.KS", "373220.KS", "207940.KS", "005380.KS", "000270.KS", "068270.KS", "051910.KS", 
    "105560.KS", "005490.KS", "028260.KS", "035420.KS", "012330.KS", "055550.KS", "032830.KS", "066570.KS", 
    "003670.KS", "033780.KS", "015760.KS", "035720.KS", "006400.KS", "086790.KS", "096770.KS", "011200.KS", 
    "034730.KS", "003550.KS", "010130.KS", "000810.KS", "009830.KS", "316140.KS", "030200.KS", "034020.KS", 
    "090430.KS", "017670.KS", "377300.KS", "034220.KS", "251270.KS", "051900.KS", "259960.KS", "323410.KS",
    "267250.KS", "138040.KS", "002790.KS", "003490.KS", "024110.KS", "028050.KS", "009150.KS", "047050.KS", 
    "086280.KS", "010950.KS", "004020.KS", "036460.KS", "001450.KS", "112040.KS", "161890.KS", "011780.KS", 
    "011170.KS", "042660.KS", "282330.KS", "078930.KS", "012450.KS", "020150.KS", "204210.KS", "011210.KS", 
    "052690.KS", "138930.KS", "252670.KS", "137310.KS",
    "086520.KQ", "247540.KQ", "028300.KQ", "196170.KQ", "348370.KQ", "068760.KQ", "403870.KQ", "058470.KQ", 
    "277810.KQ", "263750.KQ", "361610.KQ", "035900.KQ", "041510.KQ", "036830.KQ", "066970.KQ", "121600.KQ", 
    "214150.KQ", "035760.KQ", "214450.KQ", "140860.KQ", "290650.KQ", "253450.KQ", "293490.KQ", "005290.KQ", 
    "095660.KQ", "039030.KQ", "048410.KQ", "036490.KQ", "025980.KQ", "235980.KQ", "056190.KQ", "263720.KQ"
]

def run_backtest_v1_4(start_dt, end_dt):
    print("=== [1D-Rebound] V1.4 전략 백테스트 (KOSPI 벤치마크 포함) ===")
    
    start_date_fetch = (datetime.strptime(start_dt, "%Y-%m-%d") - timedelta(days=400)).strftime("%Y-%m-%d")
    
    print("야후 파이낸스 과거 데이터 일괄 다운로드 중 (~1분 소요)...")
    df = yf.download(TARGET_TICKERS, start=start_date_fetch, end=end_dt, progress=False)
    kospi_df = yf.download("^KS11", start=start_date_fetch, end=end_dt, progress=False)
    
    if df.empty or kospi_df.empty:
        print("데이터를 불러오지 못했습니다.")
        return
        
    dates = df.index.tolist()
    start_idx = 0
    start_dt_obj = datetime.strptime(start_dt, "%Y-%m-%d").date()
    for i, d in enumerate(dates):
        if d.date() >= start_dt_obj:
            start_idx = i
            break
            
    FEE_MULTIPLIER = 1 - 0.0022 # 순수 제비용 0.22%

    print("장중 시뮬레이션 및 데이터 스캔 진행 중 (V1.4)...")
    
    dict_open = df['Open'].to_dict('index')
    dict_close = df['Close'].to_dict('index')
    dict_high = df['High'].to_dict('index')
    dict_low = df['Low'].to_dict('index')
    dict_vol = df['Volume'].to_dict('index')
    
    # 코스피 기준가 추출
    try:
        kospi_close_series = kospi_df['Close']['^KS11']
    except Exception:
        kospi_close_series = kospi_df['Close']
    
    kospi_close_dict = kospi_close_series.to_dict()
    
    active_positions = []
    
    daily_realized_returns = {d: 0.0 for d in dates}
    total_trades = 0
    winning_trades = 0
    total_holding_days = 0
    
    for i in range(start_idx, len(dates) - 1):
        today_date = dates[i]
        
        surviving_positions = []
        for pos in active_positions:
            ticker = pos['ticker']
            buy_price = pos['buy_price']
            
            t_open = float(dict_open[today_date].get(ticker, float('nan')))
            t_high = float(dict_high[today_date].get(ticker, float('nan')))
            t_low = float(dict_low[today_date].get(ticker, float('nan')))
            t_close = float(dict_close[today_date].get(ticker, float('nan')))
            
            if pd.isna(t_open):
                pos['days_held'] += 1
                surviving_positions.append(pos)
                continue
                
            pos['days_held'] += 1
            days = pos['days_held']
            qty = pos['qty']
            
            # MA5 계산
            ma5 = 0
            if i >= 4:
                ma5_sum = t_close
                for k in range(1, 5):
                    ma5_sum += float(dict_close[dates[i-k]].get(ticker, 0))
                ma5 = ma5_sum / 5
                
            # SL 가격 (-5% 또는 전일저가)
            prev_low = float(dict_low[dates[i-1]].get(ticker, 0))
            if pd.isna(prev_low) or prev_low == 0:
                actual_sl = buy_price * 0.95
            else:
                actual_sl = min(buy_price * 0.95, prev_low) 
                
            # TP 가격 (+20%)
            tp_price = buy_price * 1.20
            
            daily_trade_pnl = 0.0
            
            # (1) D+1 시초가 5% 갭 익절 50%
            if days == 1 and qty == 1.0 and t_open >= buy_price * 1.05:
                profit_pct = (t_open / buy_price) * FEE_MULTIPLIER - 1
                daily_trade_pnl += profit_pct * 0.5
                qty = 0.5
                pos['total_pnl'] += daily_trade_pnl
            
            # (2) 장중 강제 손절(-5% / 전일저가) 및 20% 슈팅 익절
            hit_sl = (t_low <= actual_sl)
            hit_tp = (t_high >= tp_price)
            
            if qty > 0:
                if hit_sl and hit_tp:
                    sell_price = actual_sl if t_open > actual_sl else t_open # 최악 가정
                    profit_pct = (sell_price / buy_price) * FEE_MULTIPLIER - 1
                    daily_trade_pnl += profit_pct * qty
                    pos['total_pnl'] += profit_pct * qty
                    qty = 0
                elif hit_sl:
                    sell_price = actual_sl if t_open > actual_sl else t_open
                    profit_pct = (sell_price / buy_price) * FEE_MULTIPLIER - 1
                    daily_trade_pnl += profit_pct * qty
                    pos['total_pnl'] += profit_pct * qty
                    qty = 0
                elif hit_tp:
                    sell_price = tp_price if t_open < tp_price else t_open
                    profit_pct = (sell_price / buy_price) * FEE_MULTIPLIER - 1
                    daily_trade_pnl += profit_pct * qty
                    pos['total_pnl'] += profit_pct * qty
                    qty = 0
                    
            # (3) 종가 기준 5일선 이탈 또는 타임컷(3일)
            if qty > 0:
                if ma5 > 0 and t_close < ma5:
                    sell_price = t_close
                    profit_pct = (sell_price / buy_price) * FEE_MULTIPLIER - 1
                    daily_trade_pnl += profit_pct * qty
                    pos['total_pnl'] += profit_pct * qty
                    qty = 0
                elif days >= 3:
                    sell_price = t_close
                    profit_pct = (sell_price / buy_price) * FEE_MULTIPLIER - 1
                    daily_trade_pnl += profit_pct * qty
                    pos['total_pnl'] += profit_pct * qty
                    qty = 0
            
            pos['qty'] = qty
            
            if qty == 0:
                total_holding_days += days
                if pos['total_pnl'] > 0:
                    winning_trades += 1
            else:
                surviving_positions.append(pos)
                
            daily_realized_returns[today_date] += (daily_trade_pnl / 3.0) * 100.0
            
        active_positions = surviving_positions
        
        # 2. 매수 스캔
        day_picks = []
        for ticker in TARGET_TICKERS:
            t_open = float(dict_open[today_date].get(ticker, float('nan')))
            t_close = float(dict_close[today_date].get(ticker, float('nan')))
            t_low = float(dict_low[today_date].get(ticker, float('nan')))
            t_vol = float(dict_vol[today_date].get(ticker, float('nan')))
            prev_close = float(dict_close[dates[i-1]].get(ticker, float('nan')))
            prev_vol = float(dict_vol[dates[i-1]].get(ticker, float('nan')))
            y2_close = float(dict_close[dates[i-2]].get(ticker, float('nan')))
            
            if pd.isna(t_close) or pd.isna(prev_close) or pd.isna(t_open): continue
            
            if prev_close * prev_vol < 30000000000: continue
            if t_vol > prev_vol * 0.5: continue
            if t_close >= t_open: continue
            
            ma3 = (t_close + prev_close + y2_close) / 3
            if t_close <= ma3: continue
            
            low_gap = (t_low - ma3) / ma3
            if not (-0.01 <= low_gap <= 0.03): continue
            
            lookback = max(0, i-250)
            highest_52w = float(df['High'][ticker].iloc[lookback:i+1].max())
            if t_close < highest_52w * 0.85: continue
            
            day_picks.append(ticker)
        
        day_picks = day_picks[:3]
        for t in day_picks:
            total_trades += 1
            active_positions.append({
                "ticker": t,
                "buy_price": float(dict_close[today_date][t]),
                "qty": 1.0,
                "days_held": 0,
                "total_pnl": 0.0
            })

    # Last day close
    last_date = dates[-1]
    for pos in active_positions:
        ticker = pos['ticker']
        t_close = float(dict_close[last_date].get(ticker, pos['buy_price']))
        profit_pct = (t_close / pos['buy_price']) * FEE_MULTIPLIER - 1
        daily_trade_pnl = profit_pct * pos['qty']
        pos['total_pnl'] += daily_trade_pnl
        if pos['total_pnl'] > 0:
            winning_trades += 1
        total_holding_days += pos['days_held']
        daily_realized_returns[last_date] += (daily_trade_pnl / 3.0) * 100.0

    cumulative_return = 0.0
    daily_profits = []
    plot_dates = []
    kospi_rets = []
    
    max_portfolio = 0.0
    mdd = 0.0
    
    base_kospi = float(kospi_close_dict.get(dates[start_idx], 1.0))
    if base_kospi == 0 or pd.isna(base_kospi):
        base_kospi = 2700.0 # fallback
        
    for d in dates[start_idx:]:
        cumulative_return += daily_realized_returns[d]
        daily_profits.append(cumulative_return)
        plot_dates.append(d)
        
        k_close = float(kospi_close_dict.get(d, base_kospi))
        if pd.isna(k_close): k_close = base_kospi
        kospi_rets.append((k_close / base_kospi - 1) * 100)
        
        if cumulative_return > max_portfolio:
            max_portfolio = cumulative_return
        dd = cumulative_return - max_portfolio
        if dd < mdd:
            mdd = dd

    print("\n" + "="*50)
    print("★ [1D-Rebound V1.4] vs KOSPI 벤치마크 결과 리포트 ★")
    print("="*50)
    print(f"매매 발생 횟수: {total_trades} 건 (진입 기준)")
    if total_trades > 0:
        print(f"평균 보유 기간: {total_holding_days / total_trades:.1f} 일")
        print(f"승률(Win Rate): {winning_trades / total_trades * 100:.2f}%")
        print(f"V1.4 누적 수익률(Total Return): {cumulative_return:.2f}%")
        print(f"KOSPI 기간 수익률(Benchmark): {kospi_rets[-1]:.2f}%")
        print(f"최대 낙폭(MDD): {mdd:.2f}%")
    else:
        print("조건에 부합하는 매매 내역이 없습니다.")

    plt.figure(figsize=(12, 6))
    plt.plot([d.strftime("%Y-%m-%d") for d in plot_dates], daily_profits, label='V1.4 Strategy', color='#FF9800', linewidth=2)
    plt.plot([d.strftime("%Y-%m-%d") for d in plot_dates], kospi_rets, label='KOSPI Benchmark', color='#BDBDBD', linewidth=1.5, linestyle='--')
    
    plt.title('AlgoRich V1.4 vs KOSPI Equity Curve (Fees 0.22%)', fontsize=14, fontweight='bold')
    plt.xlabel('Date (2025~2026)', fontsize=10)
    plt.ylabel('Cumulative Return (%)', fontsize=10)
    plt.grid(True, linestyle='--', alpha=0.5)
    
    ax = plt.gca()
    ax.axes.xaxis.set_major_locator(plt.MaxNLocator(10))
    plt.xticks(rotation=45)
    
    plt.legend()
    plt.tight_layout()
    plt.savefig('backtest_v1_4_result.png', dpi=150)
    print("시각화 차트 'backtest_v1_4_result.png'가 성공적으로 저장되었습니다.")

if __name__ == "__main__":
    start_date = "2025-04-01"
    end_date = "2026-04-19"
    run_backtest_v1_4(start_date, end_date)
