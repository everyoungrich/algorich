import os
import sys
import time
import socket
from datetime import datetime
import pandas as pd
from dotenv import load_dotenv

# 전역 소켓 타임아웃 설정 (구글 시트 무한 대기 버그 방지)
socket.setdefaulttimeout(15.0)

from utils import write_log, GoogleSheetsManager
from brokers import KISBroker
from strategies import NHSniperStrategy

# =====================================================================
# [사용자 설정 섹션]
# =====================================================================
load_dotenv()

APP_KEY = os.getenv("KIS_APP_KEY", "PSMx5uBusDfehqyC9Pj9iN8YDsiOi9uCBy9d")
APP_SECRET = os.getenv("KIS_APP_SECRET", "WPtn2ND2LmVP8O0ULxMlrx8mkZGxNs1C1vwTeufma2CFCoj8F77jtzw5u6AKT0J0XNSA3SUzrAFoUy4F560esj7ZxFEiaHjPWAhj7CRuhkKHPGgToRYuKUh/sW8v5tWu0IiUnhyALCrbaklOfBp51LufSLvQd5jYrDgGhpJCu+WVMKkB/5c=")
ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "46601260")
MODE = str(os.getenv("KIS_MODE", "0"))

missing_vars = []
if not APP_KEY: missing_vars.append("APP_KEY")
if not APP_SECRET: missing_vars.append("APP_SECRET")
if not ACCOUNT_NO: missing_vars.append("ACCOUNT_NO")

if missing_vars:
    print("[System] ===== 필수 환경변수 누락 =====")
    sys.exit(1)


def run_424_recovery(broker, gs_manager):
    """
    CEO님의 대시보드 초안을 위해 4/24 데이터를 정밀 복구합니다.
    """
    target_date = "20240424"
    write_log(f"[@CTO] {target_date} 주도주 데이터 복구 시퀀스 개시...")
    
    target_watchlist = broker.build_watchlist() 
    
    for stock in target_watchlist:
        code = stock['code']
        name = stock['name']
        
        daily_info = broker.get_daily_prices(code, count=250)
        df = pd.DataFrame(daily_info)[::-1].reset_index(drop=True)
        df['stck_bsop_date'] = df['stck_bsop_date'].astype(str)
        df['stck_hgpr'] = pd.to_numeric(df['stck_hgpr'])
        
        day_row = df[df['stck_bsop_date'] == target_date]
        
        if not day_row.empty:
            idx = day_row.index[0]
            curr = day_row.iloc[0]
            
            h52 = df['stck_hgpr'].iloc[max(0, idx-250):idx].max()
            curr_high = float(curr['stck_hgpr'])
            curr_amt = float(curr['acml_tr_pbmn'])
            
            if curr_high >= h52 and curr_amt >= 100_000_000_000:
                prev_close = float(df.iloc[idx-1]['stck_clpr'])
                curr_close = float(curr['stck_clpr'])
                change_rt = round((curr_close - prev_close) / prev_close * 100, 2)
                
                if gs_manager:
                    gs_manager.log_lead_stock(
                        "2024-04-24", code, name, int(curr_close), 
                        change_rt, int(curr_amt / 100_000_000), 
                        0, "RECOVERY_DATA"
                    )
                write_log(f"[복구완료] {name} ({target_date})")
        
        time.sleep(0.5) 


if __name__ == "__main__":
    write_log(f"=== [V4.0 NH-Sniper Agent] 신고가 상승음봉 자동매매 엔진 개시 (MODE={MODE}) ===")

    # 1. 초기화 (Utils, Broker, Strategy)
    gs_manager = GoogleSheetsManager()
    kis_broker = KISBroker(APP_KEY, APP_SECRET, ACCOUNT_NO, MODE, gs_manager=gs_manager)
    strategy = NHSniperStrategy(broker=kis_broker, gs_manager=gs_manager)
    
    write_log(f"[환경확인] MODE={MODE}, BASE_URL={kis_broker.base_url}")
    write_log(f"[계좌확인] CANO={kis_broker.cano}, ACNT_PRDT_CD='{kis_broker.acnt_prdt_cd}', MODE={MODE}")

    # 백필 로직 1회 호출 (주석 처리 - build_watchlist 에러 발생)
    init_token = kis_broker.get_access_token()
    # if init_token:
    #     run_424_recovery(kis_broker, gs_manager)

    # 2. 메인 루프 (엔진 가동)
    while True:
        try:
            now = datetime.now()
            
            # 전략 틱 실행
            strategy.tick(now)
            
            time.sleep(1)
            
        except Exception as e:
            err_msg = f"[치명적 장애] {type(e).__name__}: {str(e)}"
            write_log(err_msg)
            gs_manager.log_error(err_msg)
            time.sleep(60)
            continue
