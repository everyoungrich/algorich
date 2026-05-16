import os
import time
import requests
import json
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv

# 환경 변수 로드
load_dotenv()
APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")
BASE_URL = os.getenv("KIS_BASE_URL", "https://openapivts.koreainvestment.com:29443")
MODE = str(os.getenv("KIS_MODE", "0"))
ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO")

if "-" in ACCOUNT_NO:
    CANO, ACNT_PRDT_CD = ACCOUNT_NO.split("-")
else:
    CANO = ACCOUNT_NO[:8]
    ACNT_PRDT_CD = ACCOUNT_NO[8:] if len(ACCOUNT_NO) > 8 else "01"

# 전역 토큰 캐시
ACCESS_TOKEN = ""
TOKEN_EXPIRE_TIME = datetime.now()

def write_log(message):
    """로그 파일 및 터미널 출력"""
    now_str = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    log_msg = f"{now_str} {message}"
    print(log_msg)
    with open("trade_log.txt", "a", encoding="utf-8") as f:
        f.write(log_msg + "\n")

def get_access_token():
    """토큰 발급 (자동 갱신)"""
    global ACCESS_TOKEN, TOKEN_EXPIRE_TIME
    if ACCESS_TOKEN and datetime.now() < TOKEN_EXPIRE_TIME:
        return ACCESS_TOKEN

    url = f"{BASE_URL}/oauth2/tokenP"
    headers = {"content-type": "application/json"}
    body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
    res = requests.post(url, headers=headers, data=json.dumps(body))
    
    if res.status_code == 200:
        ACCESS_TOKEN = res.json().get("access_token")
        # 안전하게 만료시간 20시간 부여
        TOKEN_EXPIRE_TIME = datetime.now() + timedelta(hours=20) 
        write_log("신규 Access Token 발급 성공")
        return ACCESS_TOKEN
    else:
        write_log(f"[에러] 토큰 발급 실패: {res.text}")
        return None

def order_stock(token, code, qty, is_buy=True):
    """현금 매수/매도 (시장가)"""
    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
    
    # 실전/모의 TR_ID 구분
    if MODE == "1":
        tr_id = "TTTC0802U" if is_buy else "TTTC0801U"
    else:
        tr_id = "VTTC0802U" if is_buy else "VTTC0801U"
        
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": tr_id
    }
    
    body = {
        "CANO": CANO,
        "ACNT_PRDT_CD": ACNT_PRDT_CD,
        "PDNO": code,
        "ORD_DVSN": "01", # 01: 시장가
        "ORD_QTY": str(qty),
        "ORD_UNPR": "0" # 시장가는 단가 0
    }
    
    res = requests.post(url, headers=headers, data=json.dumps(body))
    if res.status_code == 200:
        data = res.json()
        if data.get("rt_cd") == "0":
            side = "매수" if is_buy else "매도"
            write_log(f"[{side} 성공] 종목: {code}, 수량: {qty}개")
            return True
    write_log(f"[주문 실패] {code} - {res.text}")
    return False

def get_account_balance(token):
    """예수금 및 보유 종목 조회"""
    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
    tr_id = "TTTC8434R" if MODE == "1" else "VTTC8434R"
    headers = {
        "content-type": "application/json", "authorization": f"Bearer {token}",
        "appkey": APP_KEY, "appsecret": APP_SECRET, "tr_id": tr_id
    }
    params = {
        "CANO": CANO, "ACNT_PRDT_CD": ACNT_PRDT_CD, "AFHR_FLPR_YN": "N", "OFL_YN": "",
        "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""
    }
    res = requests.get(url, headers=headers, params=params)
    
    cash = 0
    holdings = []
    if res.status_code == 200:
        data = res.json()
        output2 = data.get("output2", [])
        if output2:
            cash = int(output2[0].get("dnca_tot_amt", 0))
        
        output1 = data.get("output1", [])
        for item in output1:
            holdings.append({
                "code": item.get('pdno'),
                "name": item.get('prdt_name'),
                "qty": int(item.get('hldg_qty')),
                "profit_rt": float(item.get('evlu_erng_rt'))
            })
    return cash, holdings

def check_1min_ma20(token, code):
    """1분봉 종가 기준 20선 우상향 & 현재가 20선 상회 여부"""
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
    headers = {
        "content-type": "application/json", "authorization": f"Bearer {token}",
        "appkey": APP_KEY, "appsecret": APP_SECRET, "tr_id": "FHKST03010200", "custtype": "P"
    }
    now_time = datetime.now().strftime("%H%M%S")
    params = {
        "FID_ETC_CLS_CODE": "", "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
        "FID_INPUT_HOUR_1": now_time, "FID_PW_DATA_INCU_YN": "N"
    }
    res = requests.get(url, headers=headers, params=params)
    if res.status_code == 200:
        data = res.json().get("output2", [])
        if len(data) >= 21:
            # 최근 25개 분봉 추출 (데이터는 최근 것이 0번째임)
            # data[0] = 현재 진행 중인 분봉이거나 막 끝난 분봉
            closes = [float(x['stck_prpr']) for x in data[:25]]
            # 오래된 순(과거부터 현재)으로 정렬
            closes.reverse() 
            df = pd.DataFrame({"close": closes})
            df["ma20"] = df["close"].rolling(window=20).mean()
            
            # 현재 20MA와 1분 전 20MA 비교하여 우상향 판별
            current_ma20 = df["ma20"].iloc[-1]
            prev_ma20 = df["ma20"].iloc[-2]
            current_price = df["close"].iloc[-1]
            
            upward_trend = current_ma20 > prev_ma20
            above_ma20 = current_price >= current_ma20
            return upward_trend and above_ma20
    return False

def market_scanner(token):
    """거래대금 500억+, 상승률 5%+, 동전주 제외, 기관외인 양매수 스캐닝"""
    write_log("스캐닝 시작 (거래대금 500억, 상승률 5%, 양매수, 동전주 및 관리종목 제외)")
    headers = {
        "content-type": "application/json", "authorization": f"Bearer {token}",
        "appkey": APP_KEY, "appsecret": APP_SECRET, "custtype": "P", "tr_id": "FHPST01710000"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": "0000", "FID_DIV_CLS_CODE": "0", "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "111111111", "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "", "FID_VOL_CNT": "", "FID_INPUT_DATE_1": ""
    }
    
    candidates = []
    res = requests.get(f"{BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank", headers=headers, params=params)
    if res.status_code == 200:
        raw_list = res.json().get("output", [])
        for item in raw_list:
            code = item.get("mksc_shrn_iscd", "")
            price = float(item.get("stck_prpr", 0))
            trade_amount = float(item.get("acml_tr_pbmn", 0))
            change_rt = float(item.get("prdy_ctrt", 0))
            
            # 1,000원 이하 동전주 제외 + 기본 조건 합치기
            if trade_amount >= 50000000000 and change_rt >= 5.0 and price >= 1000:
                candidates.append({"code": code, "price": price})
                
    final_candidates = []
    headers["tr_id"] = "FHPST02310000" # 양매수 체크 TR ID
    for cand in candidates:
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": cand["code"]}
        res = requests.get(f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor", headers=headers, params=params)
        
        if res.status_code == 200:
            data = res.json().get("output", [])
            if data:
                today = data[0]
                frgn = int(today.get("prdy_frgn_ntby_qty", 0) or 0)
                inst = int(today.get("prdy_itor_ntby_qty", 0) or 0)
                if frgn > 0 and inst > 0:
                    final_candidates.append(cand)
        time.sleep(0.1) # 초당 호출 제한 방지
        
    write_log(f"스캐닝 완료: 총 {len(final_candidates)}개 종목 양매수 포착.")
    return final_candidates

def execute_daily_buy():
    """오후 3시 18분~20분 종가 매수 로직"""
    token = get_access_token()
    if not token: return
    
    cash, holdings = get_account_balance(token)
    budget = int(cash * 0.1) # 총 예수금의 10%할당
    
    if budget < 10000:
        write_log("보유 현금이 충분하지 않아 매수를 건너뜁니다.")
        return
        
    candidates = market_scanner(token)
    
    bought_count = 0
    max_buy_count = 3 # 하루 최대 매수 종목 상한선
    
    for cand in candidates:
        if bought_count >= max_buy_count:
            write_log("하루 최대 매수 종목 수(3개)를 채웠습니다. 스캐닝 종료.")
            break
            
        code = cand["code"]
        price = cand["price"]
        
        # 보유 종목 중복 매수 방지
        already_holding = any(h["code"] == code for h in holdings)
        if already_holding:
            continue
            
        write_log(f"종목 {code} 1분봉 20선 우상향 판별 중...")
        if check_1min_ma20(token, code):
            qty = int(budget // price)
            if qty > 0:
                write_log(f"> 조건 완벽 부합! 종목 {code} 시장가 매수 주문 ({qty}주)")
                if order_stock(token, code, qty, is_buy=True):
                    bought_count += 1
            time.sleep(1) # 주문간 딜레이

def monitor_portfolio():
    """장중 익절 / 손절 로직 (2~3% 익절, 평단가 0% 이하 손절)"""
    token = get_access_token()
    if not token: return
    
    cash, holdings = get_account_balance(token)
    for h in holdings:
        profit_rt = h["profit_rt"]
        if profit_rt >= 2.0:
            write_log(f"[익절 감지] {h['name']}({h['code']}) 수익률 {profit_rt}%. 전량 매도합니다.")
            order_stock(token, h["code"], h["qty"], is_buy=False)
            time.sleep(1)
        elif profit_rt < 0.0:
            write_log(f"[손절 감지] {h['name']}({h['code']}) 수익률 {profit_rt}%. 즉시 손절합니다.")
            order_stock(token, h["code"], h["qty"], is_buy=False)
            time.sleep(1)

def force_sell_all():
    """09:05 미체결/잔량 전량 시장가 매도"""
    token = get_access_token()
    if not token: return
    
    cash, holdings = get_account_balance(token)
    if holdings:
         write_log("09:05 일괄 매도 타임: 전 보유 종목 시작가 정리.")
         for h in holdings:
             order_stock(token, h["code"], h["qty"], is_buy=False)
             time.sleep(0.5)

if __name__ == "__main__":
    write_log(f"=== 알고리치(AlgoRich) V1.0 장중 봇 구동 시작 (MODE={MODE}) ===")
    
    has_executed_morning_sell = False
    has_executed_afternoon_buy = False

    while True:
        try:
            now = datetime.now()
            
            # 장 운영 시간 내 관찰: 09:00 ~ 15:30
            if 9 <= now.hour <= 15:
                # 09:05 정리 로직 (1번만 실행)
                if now.hour == 9 and now.minute == 5 and not has_executed_morning_sell:
                    force_sell_all()
                    has_executed_morning_sell = True
                
                # 익절/손절 장중 실시간 감시 (예: 매 30초마다)
                if now.second % 30 == 0:
                    monitor_portfolio()
                
                # 15:18 ~ 15:20 종가매수 로직 (1번만 실행)
                if now.hour == 15 and 18 <= now.minute <= 20 and not has_executed_afternoon_buy:
                    execute_daily_buy()
                    has_executed_afternoon_buy = True
                    
            # 자정 넘어가면 플래그 초기화
            if now.hour == 0 and now.minute == 0:
                has_executed_morning_sell = False
                has_executed_afternoon_buy = False
                
            time.sleep(1) # 루프 대기
            
        except Exception as e:
            write_log(f"[에러 메인루프] {e}")
            time.sleep(5)
