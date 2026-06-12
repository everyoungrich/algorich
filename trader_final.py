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
from strategies import NHSniperStrategy, OneDReboundStrategy

# =====================================================================
# [사용자 설정 섹션]
# =====================================================================
load_dotenv()

REAL_APP_KEY    = os.getenv("KIS_REAL_APP_KEY", "")
REAL_APP_SECRET = os.getenv("KIS_REAL_APP_SECRET", "")
MOCK_APP_KEY    = os.getenv("KIS_MOCK_APP_KEY", "")
MOCK_APP_SECRET = os.getenv("KIS_MOCK_APP_SECRET", "")
ACCOUNT_NO      = os.getenv("KIS_ACCOUNT_NO", "46601260")

missing_vars = []
if not REAL_APP_KEY:    missing_vars.append("KIS_REAL_APP_KEY")
if not REAL_APP_SECRET: missing_vars.append("KIS_REAL_APP_SECRET")
if not MOCK_APP_KEY:    missing_vars.append("KIS_MOCK_APP_KEY")
if not MOCK_APP_SECRET: missing_vars.append("KIS_MOCK_APP_SECRET")
if not ACCOUNT_NO:      missing_vars.append("KIS_ACCOUNT_NO")

if missing_vars:
    print(f"[System] ===== 필수 환경변수 누락: {', '.join(missing_vars)} =====")
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


# =====================================================================
# [전략별 계좌·모드 설정]
# mode: "MOCK"=모의매매, "REAL"=실전매매
# account_no: 전략별 독립 계좌 (.env에서 로드, 미설정 시 기본 계좌 사용)
# =====================================================================
STRATEGY_CONFIG = {
    "NH-Sniper": {
        "mode":       os.getenv("NH_SNIPER_MODE", "MOCK"),
        "account_no": os.getenv("KIS_ACCOUNT_NH_SNIPER", ACCOUNT_NO),
    },
    "1D-Rebound": {
        "mode":       os.getenv("REBOUND_MODE", "MOCK"),
        "account_no": os.getenv("KIS_ACCOUNT_REBOUND", ACCOUNT_NO),
    },
    "20D-Swing": {
        "mode":       os.getenv("SWING_MODE", "MOCK"),
        "account_no": os.getenv("KIS_ACCOUNT_SWING", ACCOUNT_NO),
    },
    "20D-Envelope": {
        "mode":       os.getenv("ENVELOPE_MODE", "MOCK"),
        "account_no": os.getenv("KIS_ACCOUNT_ENVELOPE", ACCOUNT_NO),
    },
    "200D-Trend": {
        "mode":       os.getenv("TREND_MODE", "MOCK"),
        "account_no": os.getenv("KIS_ACCOUNT_TREND", ACCOUNT_NO),
    },
}


def build_broker(cfg, gs_manager):
    """전략 설정에 따라 KISBroker 인스턴스 생성 (is_real 자동 분기)"""
    is_real = cfg["mode"] == "REAL"
    return KISBroker(
        cfg["account_no"], gs_manager=gs_manager,
        real_app_key=REAL_APP_KEY, real_app_secret=REAL_APP_SECRET,
        mock_app_key=MOCK_APP_KEY, mock_app_secret=MOCK_APP_SECRET,
        is_real=is_real
    )


if __name__ == "__main__":
    # 중복 실행 방지 (싱글톤 락): 동시 다중 실행 시 토큰 분당 1회 제한 충돌 +
    # VTS 서버 연결 거부(SSL EOF)로 잔고 조회 오류가 폭주했던 사고 재발 방지 (2026-06-11)
    _singleton_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _singleton_lock.bind(("127.0.0.1", 54219))
    except OSError:
        print("[System] AlgoRich 봇이 이미 실행 중입니다. 중복 실행을 종료합니다.")
        sys.exit(0)

    nh_cfg  = STRATEGY_CONFIG["NH-Sniper"]
    mode_kr = "실전" if nh_cfg["mode"] == "REAL" else "모의"
    write_log(f"=== [V5.0 NH-Sniper Agent] 신고가 상승음봉 자동매매 개시 ({mode_kr}·계좌:{nh_cfg['account_no']}) ===")

    # 1. 초기화 (Utils, Broker, Strategy)
    gs_manager = GoogleSheetsManager()
    kis_broker = build_broker(nh_cfg, gs_manager)
    strategy   = NHSniperStrategy(broker=kis_broker, gs_manager=gs_manager, mode=mode_kr)

    write_log(f"[환경확인] 시세URL={kis_broker.REAL_BASE_URL}")
    write_log(f"[계좌확인] CANO={kis_broker.cano}, ACNT_PRDT_CD='{kis_broker.acnt_prdt_cd}', 모드={mode_kr}")

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
