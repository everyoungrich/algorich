import os
import requests
import json
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# 환경 변수 가져오기
APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")
ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO")
BASE_URL = os.getenv("KIS_BASE_URL", "https://openapivts.koreainvestment.com:29443")
MODE = os.getenv("KIS_MODE", "0") # 1: 실전, 0: 모의

def get_access_token():
    """접근 토큰(Access Token) 발급"""
    print("접근 토큰을 발급받는 중입니다...")
    url = f"{BASE_URL}/oauth2/tokenP"
    headers = {"content-type": "application/json"}
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    res = requests.post(url, headers=headers, data=json.dumps(body))
    
    if res.status_code == 200:
        token = res.json().get("access_token")
        print("토큰 발급 성공!")
        return token
    else:
        print(f"토큰 발급 실패: {res.text}")
        return None

def get_account_balance(token):
    """계좌 잔고 조회"""
    if not token:
        print("유효한 토큰이 없어 계좌를 조회할 수 없습니다.")
        return

    print(f"\n계좌({ACCOUNT_NO}) 잔고 및 보유 종목을 조회합니다...")
    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
    
    # 계좌번호 포맷 분리 (ex: 12345678-01 -> CANO: 12345678, ACNT_PRDT_CD: 01)
    if "-" in ACCOUNT_NO:
        cano, acnt_prdt_cd = ACCOUNT_NO.split("-")
    else:
        cano = ACCOUNT_NO[:8]
        acnt_prdt_cd = ACCOUNT_NO[8:] if len(ACCOUNT_NO) > 8 else "01"

    # 실전/모의투자에 따른 TR ID 설정
    tr_id = "TTTC8434R" if str(MODE) == "1" else "VTTC8434R"

    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": tr_id
    }

    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "00",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": ""
    }

    res = requests.get(url, headers=headers, params=params)
    
    if res.status_code == 200:
        data = res.json()
        
        output2_list = data.get("output2", [])
        if not output2_list:
            print("\n[조회 실패] 계좌 정보를 찾을 수 없거나 데이터가 비어 있습니다.")
            print(f"사유: {data.get('msg1', '알 수 없는 오류')}")
            return
            
        print("\n[조회 성공]")
        
        # 종합 잔고 내역 (output2)
        balance_info = output2_list[0]
        print(f"▶ 예수금 (주문가능현금): {int(balance_info.get('dnca_tot_amt', 0)):,}원")
        print(f"▶ 총 평가 금액: {int(balance_info.get('tot_evlu_amt', 0)):,}원")
        print(f"▶ 평가 손익: {int(balance_info.get('evlu_pfls_smtot_amt', 0)):,}원")
        
        # 보유 종목 내역 (output1)
        stocks = data.get("output1", [])
        if stocks:
            print("\n[보유 종목 리스트]")
            for stock in stocks:
                name = stock.get('prdt_name')
                qty = stock.get('hldg_qty')
                profit_rt = stock.get('evlu_erng_rt')
                print(f"- {name}: {qty}주 (수익률: {profit_rt}%)")
        else:
            print("\n보유 중인 종목이 없습니다.")
    else:
        print(f"잔고 조회 실패 (상태 코드 {res.status_code}): {res.text}")

if __name__ == "__main__":
    print(f"=== 한국투자증권(KIS) {'실전' if str(MODE) == '1' else '모의'}투자 API 연동 테스트 ===")
    
    # 1. 토큰 발급
    access_token = get_access_token()
    
    # 2. 잔고 조회
    get_account_balance(access_token)
