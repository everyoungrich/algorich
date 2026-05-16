import os
import requests
import json
import time
from dotenv import load_dotenv

# 환경 변수 로드
load_dotenv()

APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")
BASE_URL = os.getenv("KIS_BASE_URL", "https://openapivts.koreainvestment.com:29443")
MODE = str(os.getenv("KIS_MODE", "0"))

def get_access_token():
    """접근 토큰(Access Token) 발급"""
    url = f"{BASE_URL}/oauth2/tokenP"
    headers = {"content-type": "application/json"}
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    res = requests.post(url, headers=headers, data=json.dumps(body))
    
    if res.status_code == 200:
        return res.json().get("access_token")
    else:
        print(f"토큰 발급 실패: {res.text}")
        return None

def get_volume_rank(token, iscd="0000"):
    """
    거래대금 상위 조회 (iscd: '0000' 코스피/코스닥 전체, '0001' 코스피, '1001' 코스닥)
    """
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank"
    
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHPST01710000", # 거래대금 상위 TR ID
        "custtype": "P"
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": iscd,
        "FID_DIV_CLS_CODE": "0",  # 0: 전체
        "FID_BLNG_CLS_CODE": "0", # 0: 전체
        "FID_TRGT_CLS_CODE": "111111111", # 대상 분류
        "FID_TRGT_EXLS_CLS_CODE": "000000", # 제외 분류 (ETF, ETN 등 제외 여부에 따라 설정)
        "FID_INPUT_PRICE_1": "",
        "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
        "FID_INPUT_DATE_1": ""
    }

    res = requests.get(url, headers=headers, params=params)
    if res.status_code == 200:
        return res.json().get("output", [])
    else:
        print(f"거래대금 상위 조회 실패: {res.text}")
        return []

def check_investor_trend(token, stock_code):
    """
    종목별 투자자 흐름 (기관/외국인 양매수 여부 파악)
    """
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor"
    
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHPST02310000", # 종목별 투자자 TR ID
        "custtype": "P"
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": stock_code
    }

    res = requests.get(url, headers=headers, params=params)
    if res.status_code == 200:
        # output 리스트의 0번째 요소가 가장 최근 거래일(혹은 당일) 실적
        data = res.json().get("output", [])
        if data:
            today_data = data[0]
            # prdy_frgn_ntby_qty: 외국인 순매수 수량, prdy_itor_ntby_qty: 기관 순매수 수량
            foreign_net_buy = int(today_data.get("prdy_frgn_ntby_qty", 0) or 0)
            inst_net_buy = int(today_data.get("prdy_itor_ntby_qty", 0) or 0)
            
            # 둘 다 0보다 크면 양매수
            is_both_buying = (foreign_net_buy > 0) and (inst_net_buy > 0)
            return is_both_buying, foreign_net_buy, inst_net_buy
    else:
        # API 조회 제한 (초당 호출 제한 등) 회피용 로깅 (심하면 출력 안하도록 주석처리)
        # print(f"수급 조회 실패 ({stock_code}): {res.text}")
        pass
        
    return False, 0, 0

def run_scanner():
    print("=== 시장 스캐너 구동 시작 (V1.0 전략: 거래대금 & 양매수) ===")
    token = get_access_token()
    if not token:
        print("토큰 발급 실패로 스캐너를 종료합니다.")
        return

    print("1차 필터링: 거래대금 상위 (500억 이상) 및 상승률 (5% 이상) 종목을 검색합니다...")
    
    # 코스피(0001), 코스닥(1001) 각각 가져오기 (전체 0000 호출 시 상위 30개만 올 경우 대비)
    candidates = []
    
    # KIS "0000" 모드로 시장 통합 거래대금 상위 100위 조회 (대부분 100개가 응답됨)
    raw_list = get_volume_rank(token, "0000")
    
    for item in raw_list:
        stock_name = item.get("hts_kor_isnm", "")
        stock_code = item.get("mksc_shrn_iscd", "")
        
        # 누적 거래 대금 (단위: 원, KIS API 반환값은 문자열이므로 변환. 1=1원)
        trade_amount = float(item.get("acml_tr_pbmn", 0))
        # 전일 대비 상승률 (%)
        price_change_rt = float(item.get("prdy_ctrt", 0))
        
        # 필터 기준: 500억 이상, 상승률 5% 이상
        if trade_amount >= 50000000000 and price_change_rt >= 5.0:
            candidates.append({
                "code": stock_code,
                "name": stock_name,
                "amount": trade_amount,
                "change_rt": price_change_rt
            })
            
    print(f"1차 필터링 완료: {len(candidates)}개 종목 발견.")
    if not candidates:
        print("조건에 부합하는 종목이 없습니다.")
        return

    print("\n2차 필터링: 외인/기관 양매수 수급을 확인합니다. (API 호출 대기...)")
    final_picks = []
    
    # KIS API의 초당 단건 호출 제어 (Max 20 RPS) - 안전하게 0.1초 딜레이 추가
    for idx, cand in enumerate(candidates):
        is_both_buying, frgn, inst = check_investor_trend(token, cand["code"])
        
        if is_both_buying:
            cand["frgn_net"] = frgn
            cand["inst_net"] = inst
            final_picks.append(cand)
            print(f"  -> [발견!] {cand['name']} (외인: {frgn}주, 기관: {inst}주)")
            
        time.sleep(0.1) # TPS 리밋 회피

    print("\n=== 최종 스캐닝 결과 (상위 10개) ===")
    
    # 거래대금 500억 통과 상태이므로 추가 정렬 필요 시 거래대금 순으로 다시 정렬
    final_picks = sorted(final_picks, key=lambda x: x["amount"], reverse=True)
    
    for i, pick in enumerate(final_picks[:10], 1):
        print(f"[{i}위] {pick['name']} ({pick['code']})")
        print(f"  - 등락률: {pick['change_rt']}%")
        print(f"  - 거래대금: {pick['amount'] / 100000000:,.0f}억원")
        print(f"  - 수급: 양매수 (외국인 {pick['frgn_net']}주 / 기관 {pick['inst_net']}주)")
        print("-" * 40)
        
    print("\n시장 스캐닝 작업이 끝났습니다. (이 결과는 15시 20분에 매수 후보 목록이 됩니다.)")

if __name__ == "__main__":
    run_scanner()
