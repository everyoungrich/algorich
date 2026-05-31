from abc import ABC, abstractmethod
import os
import json
import requests
from datetime import datetime, timedelta
import pandas as pd
import time

from utils import write_log, safe_request
from scores.leader_score import calculate_leader_score, format_score_log

# -- ETF/ETN/레버리지/인버스 판별 헬퍼 ------------------------------------------
# 국내 ETF는 두 가지 방법으로 식별:
#   1. 종목코드에 알파벳 포함 (신규 ETF 코드 형식: 예 0193T0, 0195S0)
#   2. 종목명이 알려진 ETF 운용사 브랜드로 시작하거나 ETF/ETN 키워드 포함
_ETF_BRANDS = [
    "KODEX", "TIGER", "KINDEX", "KOSEF", "ARIRANG",
    "SOL", "ACE", "HANARO", "MASTER", "TIMEFOLIO",
    "KBSTAR", "TREX", "SMART", "KoAct",
    "PLUS", "WON", "마이다스", "히어로즈",
]
_ETF_KEYWORDS = ["ETF", "ETN", "스팩", "스펙", "레버리지", "인버스", "선물"]


def _is_etf_etn(code: str, name: str) -> bool:
    """True 이면 ETF/ETN/파생상품 -- 스캔 제외 대상."""
    # 코드에 알파벳 포함 -> 신규 ETF 코드 형식
    if any(c.isalpha() for c in code):
        return True
    # 종목명 키워드 체크
    name_upper = name.upper()
    if any(kw.upper() in name_upper for kw in _ETF_KEYWORDS):
        return True
    # ETF 운용사 브랜드로 시작하는지 체크
    if any(name.startswith(brand) for brand in _ETF_BRANDS):
        return True
    return False


class BaseBroker(ABC):
    @abstractmethod
    def get_access_token(self):
        pass

    @abstractmethod
    def place_order(self, code, qty, price=0, is_buy=True):
        pass

    @abstractmethod
    def get_balance(self):
        pass

    @abstractmethod
    def get_daily_prices(self, code, count=250):
        pass

    @abstractmethod
    def check_market_crash(self):
        pass

    @abstractmethod
    def scan_s_class_targets(self):
        pass


class KISBroker(BaseBroker):
    REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"    # 실전: 시세조회/차트
    MOCK_BASE_URL = "https://openapivts.koreainvestment.com:29443" # 모의: 주문/잔고

    def __init__(self, account_no, gs_manager=None,
                 real_app_key=None, real_app_secret=None,
                 mock_app_key=None, mock_app_secret=None,
                 is_real=False):
        self.real_app_key    = real_app_key    or os.getenv("KIS_REAL_APP_KEY", "")
        self.real_app_secret = real_app_secret or os.getenv("KIS_REAL_APP_SECRET", "")
        self.mock_app_key    = mock_app_key    or os.getenv("KIS_MOCK_APP_KEY", "")
        self.mock_app_secret = mock_app_secret or os.getenv("KIS_MOCK_APP_SECRET", "")
        self.account_no = account_no
        self.gs_manager = gs_manager
        self.is_real = is_real  # True=실전주문, False=모의주문

        if "-" in self.account_no:
            self.cano, self.acnt_prdt_cd = self.account_no.split("-")
        else:
            self.cano = self.account_no[:8]
            self.acnt_prdt_cd = self.account_no[8:] if len(self.account_no) > 8 else "01"

        self._real_token = ""
        self._real_token_expire = datetime.now()
        self._mock_token = ""
        self._mock_token_expire = datetime.now()

    # -- 토큰 발급 (실전/모의 분리) ------------------------------------------

    def _get_real_token(self):
        if self._real_token and datetime.now() < self._real_token_expire:
            return self._real_token
        url = f"{self.REAL_BASE_URL}/oauth2/tokenP"
        body = {"grant_type": "client_credentials",
                "appkey": self.real_app_key, "appsecret": self.real_app_secret}
        res = safe_request('POST', url, gs_manager=self.gs_manager,
                           headers={"content-type": "application/json"},
                           data=json.dumps(body), timeout=10)
        if res and res.status_code == 200:
            self._real_token = res.json().get("access_token")
            self._real_token_expire = datetime.now() + timedelta(hours=20)
            write_log("실전 Access Token 발급 성공 (시세조회용)")
            return self._real_token
        status = res.status_code if res else "응답없음"
        body_txt = res.text[:300] if res else "요청 실패"
        write_log(f"[실전 토큰 발급 실패] status={status}, body={body_txt}")
        return None

    def _get_mock_token(self):
        if self._mock_token and datetime.now() < self._mock_token_expire:
            return self._mock_token
        url = f"{self.MOCK_BASE_URL}/oauth2/tokenP"
        body = {"grant_type": "client_credentials",
                "appkey": self.mock_app_key, "appsecret": self.mock_app_secret}
        res = safe_request('POST', url, gs_manager=self.gs_manager,
                           headers={"content-type": "application/json"},
                           data=json.dumps(body), timeout=10)
        if res and res.status_code == 200:
            self._mock_token = res.json().get("access_token")
            self._mock_token_expire = datetime.now() + timedelta(hours=20)
            write_log("모의 Access Token 발급 성공 (주문실행용)")
            return self._mock_token
        status = res.status_code if res else "응답없음"
        body_txt = res.text[:300] if res else "요청 실패"
        write_log(f"[모의 토큰 발급 실패] status={status}, body={body_txt}")
        return None

    def get_access_token(self):
        return self._get_real_token()

    # -- 헤더 빌더 -----------------------------------------------------------

    def _real_headers(self, tr_id):
        return {
            "content-type": "application/json",
            "authorization": f"Bearer {self._get_real_token()}",
            "appkey": self.real_app_key,
            "appsecret": self.real_app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _mock_headers(self, tr_id):
        return {
            "content-type": "application/json",
            "authorization": f"Bearer {self._get_mock_token()}",
            "appkey": self.mock_app_key,
            "appsecret": self.mock_app_secret,
            "tr_id": tr_id,
        }

    # -- 주문/잔고 (is_real 플래그로 실전/모의 자동 분기) --------------------

    def place_order(self, code, qty, price=0, is_buy=True):
        if self.is_real:
            if not self._get_real_token():
                return False
            url   = f"{self.REAL_BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
            tr_id = "TTTC0802U" if is_buy else "TTTC0801U"
            headers  = self._real_headers(tr_id)
            mode_tag = "실전"
        else:
            if not self._get_mock_token():
                return False
            url   = f"{self.MOCK_BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
            tr_id = "VTTC0802U" if is_buy else "VTTC0801U"
            headers  = self._mock_headers(tr_id)
            mode_tag = "모의"

        body = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PDNO": code, "ORD_DVSN": "01",
            "ORD_QTY": str(qty), "ORD_UNPR": str(price),
        }
        res = safe_request('POST', url, gs_manager=self.gs_manager,
                           headers=headers, data=json.dumps(body), timeout=10)
        if res and res.status_code == 200:
            data = res.json()
            if data.get("rt_cd") == "0":
                side = "매수" if is_buy else "매도"
                write_log(f"[{side} 주문성공({mode_tag}/시장가)] 종목: {code}, 수량: {qty}주")
                return True
            write_log(f"[주문 실패] rt_cd={data.get('rt_cd')}, "
                      f"메시지={data.get('msg1')}, 종목={code}")
            if self.gs_manager:
                self.gs_manager.log_error(f"주문실패 {code}: {data.get('msg1')}")
        elif res:
            write_log(f"[주문 HTTP 오류] status={res.status_code}, body={res.text[:200]}")
        return False

    def get_balance(self):
        if self.is_real:
            if not self._get_real_token():
                return None, None, None
            url     = f"{self.REAL_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
            headers = self._real_headers("TTTC8434R")
        else:
            if not self._get_mock_token():
                return None, None, None
            url     = f"{self.MOCK_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
            headers = self._mock_headers("VTTC8434R")

        params = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
            "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        res = safe_request('GET', url, gs_manager=self.gs_manager,
                           headers=headers, params=params, timeout=10)
        if res is None or res.status_code != 200:
            status   = res.status_code if res else "응답없음"
            body_txt = res.text[:300] if res else "요청 실패"
            write_log(f"[잔고 조회 API 오류] status={status}, body={body_txt}")
            return None, None, None

        cash, tot_eval = 0, 0
        holdings = []
        data = res.json()
        if data.get("rt_cd") not in ("0", None, ""):
            write_log(f"[잔고 조회 API 오류] rt_cd={data.get('rt_cd')}, "
                      f"msg={data.get('msg1')}, msg_cd={data.get('msg_cd')}")
            return None, None, None
        out2 = data.get("output2", [])
        if out2:
            cash     = int(out2[0].get("dnca_tot_amt", 0))
            tot_eval = int(out2[0].get("tot_evlu_amt", 0))
        for item in data.get("output1", []):
            qty = int(item.get("hldg_qty", 0))
            if qty > 0:
                holdings.append({
                    "code":          item.get("pdno"),
                    "name":          item.get("prdt_name"),
                    "qty":           qty,
                    "buy_price":     float(item.get("pchs_avg_pric", 0)),
                    "current_price": float(item.get("prpr", 0)),
                    "profit_rt":     float(item.get("evlu_erng_rt", 0)),
                })
        return cash, tot_eval, holdings

    # -- 시세조회/차트 (실전 키 사용) ----------------------------------------

    def get_daily_prices(self, code, count=250, end_date=None):
        if not self._get_real_token():
            return []

        url      = (f"{self.REAL_BASE_URL}/uapi/domestic-stock/v1/quotations"
                    f"/inquire-daily-itemchartprice")
        end_date = end_date or datetime.now().strftime("%Y%m%d")
        result   = []

        while len(result) < count:
            rem        = count - len(result)
            start_date = (datetime.strptime(end_date, "%Y%m%d")
                          - timedelta(days=rem * 1.5)).strftime("%Y%m%d")
            params = {
                "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                "FID_INPUT_DATE_1": start_date, "FID_INPUT_DATE_2": end_date,
                "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0",
            }
            res = safe_request('GET', url, gs_manager=self.gs_manager,
                               headers=self._real_headers("FHKST03010100"),
                               params=params, timeout=10)
            if not res or res.status_code != 200:
                break
            data = res.json().get("output2", [])
            if not data:
                break
            valid_data = [d for d in data if d.get("stck_bsop_date")]
            if not valid_data:
                break
            result.extend(valid_data)
            oldest = valid_data[-1]["stck_bsop_date"]
            end_date = (datetime.strptime(oldest, "%Y%m%d")
                        - timedelta(days=1)).strftime("%Y%m%d")
            time.sleep(0.2)

        return result[:count]

    def check_market_crash(self):
        if not self._get_real_token():
            return False

        url   = (f"{self.REAL_BASE_URL}/uapi/domestic-stock/v1/quotations"
                 f"/inquire-daily-indexchartprice")
        today = datetime.now().strftime("%Y%m%d")
        params = {
            "FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001",
            "FID_INPUT_DATE_1": today, "FID_INPUT_DATE_2": today,
            "FID_PERIOD_DIV_CODE": "D",
        }
        res = safe_request('GET', url, gs_manager=self.gs_manager,
                           headers=self._real_headers("FHKUP03500100"),
                           params=params, timeout=10)
        if res and res.status_code == 200:
            data = res.json().get("output1", {})
            if isinstance(data, list) and len(data) > 0:
                data = data[0]
            if data:
                idx_prpr = float(data.get("bstp_nmix_prpr", 0))
                idx_lwpr = float(data.get("bstp_nmix_lwpr", 0))
                if idx_prpr > 0 and idx_prpr <= idx_lwpr * 1.002:
                    return True
        return False

    def is_s_class_target(self, df):
        if len(df) < 250:
            return False
        try:
            curr      = df.iloc[-1]
            curr_high = float(curr["hgpr"])
            if curr_high <= 0:
                return False
            prior_highs       = df["hgpr"].iloc[:-1]
            prior_highs_clean = prior_highs[prior_highs > 0]
            if prior_highs_clean.empty:
                return False
            high_52w = float(prior_highs_clean.max())
            return curr_high >= high_52w  # 조건 4: 52주 신고가
        except (KeyError, ValueError, TypeError) as e:
            write_log(f"[에러] 52주 신고가 검증 중 차트 데이터 오류: {e}")
            return False

    def get_inst_net_amount(self, code) -> float:
        """당일 기관 합계 순매수대금(백만원). 실패 시 None 반환."""
        if not self._get_real_token():
            return None
        url    = (f"{self.REAL_BASE_URL}/uapi/domestic-stock/v1/quotations"
                  f"/inquire-investor")
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
        res    = safe_request('GET', url, gs_manager=self.gs_manager,
                              headers=self._real_headers("FHKST01010900"),
                              params=params, timeout=10)
        if res and res.status_code == 200:
            data = res.json()
            if data.get("rt_cd") == "0":
                inv = (data.get("output") or [{}])[0]
                return float(inv.get("orgn_ntby_tr_pbmn") or 0)
        return None

    def _naver_top_by_amount(self, date_str: str, top_n: int = 40) -> list:
        """
        Naver Finance 거래량 순위 페이지에서 과거 날짜 상위 종목 조회 (백필 전용).
        KIS volume-rank API 는 실시간 전용이므로 과거 날짜 백필 시 이 메서드를 사용.

        ⚠️  주의: sise_quant.nhn 은 거래대금(trading value) 순위가 아닌
                   거래량(volume) 순위 페이지임.
                   거래량이 많고 주가가 낮은 소형주가 유입될 수 있으므로
                   호출부(backfill_lead.py)에서 scan_amt_100m >= 500억 필터 필수.

        date_str : 'YYYYMMDD'
        반환     : [{"code": str, "name": str, "amount": int(원)}, ...]  -- ETF/ETN 제외
        amount 단위: 원  (Naver 원본 백만원 x 1_000_000 변환)
        """
        import re as _re

        results = []
        seen    = set()

        for sosok in ["0", "1"]:       # 0=KOSPI, 1=KOSDAQ
            for page in [1, 2, 3]:     # 페이지당 ~50종목
                # sise_trans.nhn = 거래대금 순위 (sise_quant.nhn은 거래량 순위 — 잘못된 URL)
                url = (f"https://finance.naver.com/sise/sise_trans.nhn"
                       f"?sosok={sosok}&date={date_str}&page={page}")
                try:
                    r = requests.get(
                        url, timeout=15,
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                            "Referer":    "https://finance.naver.com/sise/",
                        })
                    if not r.ok:
                        break
                    html = r.content.decode("euc-kr", errors="replace")

                    # tr 블록별 파싱
                    for tr in _re.findall(r"<tr[^>]*>(.*?)</tr>", html, _re.DOTALL):
                        code_m = _re.search(r"code=(\d{6})", tr)
                        if not code_m:
                            continue
                        code = code_m.group(1)
                        if code in seen:
                            continue

                        # 종목명
                        name_m = _re.search(r"code=\d+[^>]+>([^<]+)<", tr)
                        if not name_m:
                            continue
                        name = name_m.group(1).strip()

                        # ETF/ETN 제외
                        if _is_etf_etn(code, name):
                            continue

                        # td 내 숫자값 전체 추출 (콤마 제거)
                        nums = []
                        for raw in _re.findall(r">([0-9,]+)</td>", tr):
                            try:
                                nums.append(int(raw.replace(",", "")))
                            except ValueError:
                                pass
                        if len(nums) < 4:
                            continue

                        # 거래대금(백만원): 행에서 가장 큰 값으로 근사
                        amount_million = max(nums)
                        if amount_million < 100:  # 100백만원(=1억) 미만 스킵
                            continue

                        results.append({
                            "code":   code,
                            "name":   name,
                            "amount": amount_million * 1_000_000,  # 백만원 -> 원
                        })
                        seen.add(code)

                except Exception as e:
                    write_log(f"[_naver_top_by_amount] sosok={sosok} page={page} 오류: {e}")
                time.sleep(0.3)

        results.sort(key=lambda x: x["amount"], reverse=True)
        return results[:top_n]

    def scan_s_class_targets(self, target_date: str = None):
        """target_date: 'YYYYMMDD' (None 이면 오늘). 과거 날짜 백필 가능."""
        if not self._get_real_token():
            return []

        date_label   = target_date or datetime.now().strftime("%Y%m%d")
        log_date_str = datetime.strptime(date_label, "%Y%m%d").strftime("%Y-%m-%d")
        write_log(f"[@Scanner] V3.2 S-Class 주도주 스캐닝 시작 (기준일: {log_date_str})")

        # ── KIS API 다중 호출로 비ETF 30종목 확보 ──────────────────────────
        # 문제: KIS API(FHPST01710000)는 호출당 최대 30종목만 반환.
        #       ETF가 상위권에 대거 포진하면 비ETF 종목 수가 13~17개에 그침.
        # 해결: FID_DIV_CLS_CODE = 0(전체)/1(KOSPI)/2(KOSDAQ) 순으로 최대 3회 호출,
        #       중복 제거 후 거래대금 기준 재정렬 → 비ETF 상위 30종목 선정.
        url_amt = (f"{self.REAL_BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank")
        base_params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE":  "20171",
            "FID_INPUT_ISCD":         "0000",
            "FID_BLNG_CLS_CODE":      "3",    # 거래금액순 정렬
            "FID_TRGT_CLS_CODE":      "111111111",
            "FID_TRGT_EXLS_CLS_CODE": "000000",
            "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
            "FID_VOL_CNT": "",
            "FID_INPUT_DATE_1": target_date or "",
        }

        seen_codes   = set()
        raw_combined = []

        for div_code in ["0", "1", "2"]:   # 전체 → KOSPI → KOSDAQ 순으로 보충 호출
            params = {**base_params, "FID_DIV_CLS_CODE": div_code}
            res    = safe_request('GET', url_amt, gs_manager=self.gs_manager,
                                  headers=self._real_headers("FHPST01710000"),
                                  params=params, timeout=10)
            batch  = res.json().get("output", []) if res and res.status_code == 200 else []

            added = 0
            for item in batch:
                code = item.get("mksc_shrn_iscd", "")
                if code and code not in seen_codes:
                    seen_codes.add(code)
                    raw_combined.append(item)
                    added += 1

            non_etf_so_far = sum(
                1 for it in raw_combined
                if not _is_etf_etn(it.get("mksc_shrn_iscd", ""), it.get("hts_kor_isnm", ""))
            )
            write_log(
                f"[거래대금 순위 조회] div={div_code}: {len(batch)}종목 수신 "
                f"(신규 {added}종목 추가 / 누적 비ETF {non_etf_so_far}종목)"
            )

            if non_etf_so_far >= 30:   # 비ETF 30종목 이상 확보 → 추가 호출 불필요
                break
            time.sleep(0.3)

        # 거래대금(acml_tr_pbmn) 기준 내림차순 정렬 후 비ETF 상위 30개 선정
        raw_combined.sort(
            key=lambda x: float(x.get("acml_tr_pbmn", 0) or 0), reverse=True
        )
        raw_list = [
            item for item in raw_combined
            if not _is_etf_etn(item.get("mksc_shrn_iscd", ""), item.get("hts_kor_isnm", ""))
        ][:30]
        write_log(
            f"[거래대금 상위 30위] 전체수신 {len(raw_combined)}종목 → "
            f"비ETF {len(raw_list)}종목 선정"
        )

        # -- 공휴일 안전장치 --------------------------------------------------
        if not target_date:
            ref_data    = self.get_daily_prices("005930", count=1)
            market_date = ref_data[0].get("stck_bsop_date", "") if ref_data else ""
            if market_date and market_date != date_label:
                write_log(f"[스캔 중단] 공휴일/휴장 감지 -- "
                          f"최근 영업일({market_date}) != 오늘({date_label}).")
                return []

        candidates = []
        for mkt_rank, item in enumerate(raw_list, start=1):
            code  = item.get("mksc_shrn_iscd", "")
            name  = item.get("hts_kor_isnm", "")
            price = float(item.get("stck_prpr", 0))
            vol   = float(item.get("acml_vol", 0))
            amt   = float(item.get("acml_tr_pbmn", 0))
            if amt == 0:
                amt = price * vol

            if _is_etf_etn(code, name):
                write_log(f"[스캔 제외] {name}({code}) ETF/ETN/레버리지/인버스/스팩")
                continue

            vol_rate      = float(item.get("vol_inrt", 0))
            price_rate    = float(item.get("prdy_ctrt", 0))
            scan_amt_100m_filter = int(amt / 100_000_000)  # 1차 필터 로그용

            fail_reason = ""
            if price < 1000:
                fail_reason = f"동전주(가격:{price:,.0f})"
            elif vol_rate < 200.0:
                fail_reason = f"거래량부족(vol_inrt:{vol_rate:.0f}%)"
            elif price_rate < 10.0:
                fail_reason = f"상승률부족(등락:{price_rate:.1f}%)"

            write_log(f"[스캔] #{mkt_rank:02d} {name}({code}) "
                      f"등락:{price_rate:+.1f}% 거래량비:{vol_rate:.0f}% "
                      f"거래대금:{scan_amt_100m_filter}억 "
                      f"-> {'PASS' if not fail_reason else 'FAIL:' + fail_reason}")

            if price >= 1000 and vol_rate >= 200.0 and price_rate >= 10.0:
                candidates.append({
                    "code": code, "name": name, "price": price, "amt": amt,
                    "vol_rate": vol_rate, "price_rate": price_rate, "mkt_rank": mkt_rank,
                })

        candidates.sort(key=lambda x: x["amt"], reverse=True)
        write_log(f"[필터 통과] vol>=200%+등락>=10%+1000원: "
                  f"{len(candidates)}종목 -> 52주 신고가 검증 시작")

        s_class_list = []
        for cand in candidates:
            code       = cand["code"]
            name       = cand["name"]
            vol_rate   = cand.get("vol_rate", 0)
            price_rate = cand.get("price_rate", 0)
            mkt_rank   = cand.get("mkt_rank", 0)
            daily_info = self.get_daily_pric