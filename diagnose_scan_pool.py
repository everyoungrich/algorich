# -*- coding: utf-8 -*-
"""
diagnose_scan_pool.py — 스캔 풀 구성 진단 (읽기 전용, 봇 코드 수정 없음)

목적: scan_s_class_targets()가 사용하는 volume-rank API(FHPST01710000)의
      div=0/1/2 원본 응답을 그대로 덤프해서,
      1) FID_DIV_CLS_CODE가 실제로 무엇을 반환하는지
      2) 후성(093370)이 어느 호출에 존재하는지/어디서도 안 오는지
      3) 최종 심사대상 30종목이 어떻게 구성되는지
      를 검증한다.

실행: python diagnose_scan_pool.py
출력: 콘솔 + scan_pool_dump_YYYYMMDD_HHMM.json (원본 응답 보존)

⚠ 주의: 시세 조회만 수행. 주문/잔고 API는 일절 호출하지 않음.
⚠ volume-rank는 실시간 전용 → 당일 데이터 검증은 당일 장 마감 후 ~ 다음 영업일 개장 전에 실행.
"""
import os
import json
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"
APP_KEY    = os.getenv("KIS_REAL_APP_KEY", "")
APP_SECRET = os.getenv("KIS_REAL_APP_SECRET", "")

TRACE_TARGETS = {
    "093370": "후성",
    "240810": "원익IPS",
    "403870": "HPSP",
}

# brokers.py의 _is_etf_etn와 동일한 로직 (검증 목적 복제 — 봇 코드 무수정 원칙)
_ETF_BRANDS = [
    "KODEX", "TIGER", "KINDEX", "KOSEF", "ARIRANG",
    "SOL", "ACE", "HANARO", "MASTER", "TIMEFOLIO",
    "KBSTAR", "TREX", "SMART", "KoAct",
    "PLUS", "WON", "마이다스", "히어로즈",
]
_ETF_KEYWORDS = ["ETF", "ETN", "스팩", "스펙", "레버리지", "인버스", "선물"]

def is_etf_etn(code, name):
    if any(c.isalpha() for c in code):
        return True
    name_upper = name.upper()
    if any(kw.upper() in name_upper for kw in _ETF_KEYWORDS):
        return True
    if any(name.startswith(b) for b in _ETF_BRANDS):
        return True
    return False

def is_pref(name):
    """우선주 추정: 이름이 '우'/'우B'/'우C'로 끝나거나 '우)' 패턴"""
    n = name.strip()
    return n.endswith("우") or n.endswith("우B") or n.endswith("우C") or n.endswith("(전환)")

def get_token():
    res = requests.post(
        f"{REAL_BASE_URL}/oauth2/tokenP",
        headers={"content-type": "application/json"},
        data=json.dumps({"grant_type": "client_credentials",
                         "appkey": APP_KEY, "appsecret": APP_SECRET}),
        timeout=15)
    res.raise_for_status()
    return res.json()["access_token"]

def fetch_volume_rank(token, div_code, exls_code="000000"):
    """scan_s_class_targets()와 동일한 파라미터로 호출 (exls_code만 가변)"""
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE":  "20171",
        "FID_INPUT_ISCD":         "0000",
        "FID_BLNG_CLS_CODE":      "3",
        "FID_TRGT_CLS_CODE":      "111111111",
        "FID_TRGT_EXLS_CLS_CODE": exls_code,
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
        "FID_INPUT_DATE_1": "",
        "FID_DIV_CLS_CODE": div_code,
    }
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY, "appsecret": APP_SECRET,
        "tr_id": "FHPST01710000", "custtype": "P",
    }
    res = requests.get(f"{REAL_BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank",
                       headers=headers, params=params, timeout=15)
    res.raise_for_status()
    return res.json()

def fmt_row(i, it):
    code = it.get("mksc_shrn_iscd", "")
    name = it.get("hts_kor_isnm", "")
    amt  = float(it.get("acml_tr_pbmn", 0) or 0) / 100_000_000
    vol  = float(it.get("vol_inrt", 0) or 0)
    chg  = float(it.get("prdy_ctrt", 0) or 0)
    tags = []
    if is_etf_etn(code, name): tags.append("ETF/ETN")
    if is_pref(name):          tags.append("우선주")
    mark = " ◀◀◀ 추적대상" if code in TRACE_TARGETS else ""
    return (f"  {i:2d}. {name}({code}) 거래대금:{amt:,.0f}억 "
            f"등락:{chg:+.1f}% 거래량비:{vol:.0f}% {' '.join(tags)}{mark}")

def main():
    if not APP_KEY or not APP_SECRET:
        print("[오류] .env에 KIS_REAL_APP_KEY/SECRET 없음"); return

    now = datetime.now()
    print(f"=== 스캔 풀 진단 시작 ({now:%Y-%m-%d %H:%M:%S}) ===")
    token = get_token()
    print("토큰 발급 OK\n")

    dump = {"run_at": now.isoformat(), "responses": {}}
    found_in = {c: [] for c in TRACE_TARGETS}
    seen_codes, raw_combined = set(), []

    for div in ["0", "1", "2"]:
        data = fetch_volume_rank(token, div)
        items = data.get("output", [])
        dump["responses"][f"div={div}"] = data  # 원본 그대로 보존

        print(f"───── div={div} 응답: {len(items)}종목 ─────")
        for i, it in enumerate(items, 1):
            print(fmt_row(i, it))
            code = it.get("mksc_shrn_iscd", "")
            if code in TRACE_TARGETS:
                found_in[code].append(f"div={div} #{i}")
            if code and code not in seen_codes:   # 스캐너와 동일한 풀 병합
                seen_codes.add(code)
                raw_combined.append(it)
        print()
        time.sleep(0.5)

    # 스캐너와 동일: 거래대금 내림차순 정렬 → 비ETF 상위 30
    raw_combined.sort(key=lambda x: float(x.get("acml_tr_pbmn", 0) or 0), reverse=True)
    final30 = [it for it in raw_combined
               if not is_etf_etn(it.get("mksc_shrn_iscd", ""), it.get("hts_kor_isnm", ""))][:30]

    print("═════ 스캐너 로직 재현: 최종 심사대상 30종목 ═════")
    n_pref = 0
    for i, it in enumerate(final30, 1):
        print(fmt_row(i, it))
        if is_pref(it.get("hts_kor_isnm", "")): n_pref += 1
    print(f"\n→ 30종목 중 우선주 {n_pref}개")

    print("\n═════ 추적 대상 3종목 결론 ═════")
    final_codes = {it.get("mksc_shrn_iscd") for it in final30}
    for code, name in TRACE_TARGETS.items():
        src = ", ".join(found_in[code]) if found_in[code] else "❌ div=0/1/2 어디에도 없음"
        in30 = "✅ 포함" if code in final_codes else "❌ 미포함"
        print(f"  {name}({code}): 원본 응답 출처 [{src}] / 최종 30종목 {in30}")

    # ═════ 해법 테스트: 제외 플래그 적용 호출 ═════
    # KIS 공식 문서: FID_TRGT_EXLS_CLS_CODE 10자리 =
    # [투자위험/경고/주의, 관리종목, 정리매매, 불성실공시, 우선주, 거래정지, ETF, ETN, 신용주문불가, SPAC]
    # "0000101101" = 우선주+ETF+ETN+SPAC 제외
    print("\n═════ [해법 테스트] 우선주·ETF·ETN·SPAC 제외 플래그 적용 (div=0) ═════")
    time.sleep(1.0)
    try:
        data = fetch_volume_rank(token, "0", exls_code="0000101101")
        items = data.get("output", [])
        dump["responses"]["div=0_excl=0000101101"] = data
        if not items:
            print(f"  ⚠ 응답 비어있음 — rt_cd={data.get('rt_cd')}, msg={data.get('msg1')}")
        for i, it in enumerate(items, 1):
            print(fmt_row(i, it))
        codes = {it.get("mksc_shrn_iscd") for it in items}
        for code, name in TRACE_TARGETS.items():
            print(f"  → {name}({code}): {'✅ 포함됨' if code in codes else '❌ 여전히 없음'}")
    except Exception as e:
        print(f"  ⚠ 제외 플래그 호출 실패: {e}")

    out = f"scan_pool_dump_{now:%Y%m%d_%H%M}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=2)
    print(f"\n원본 응답 저장: {out}")

if __name__ == "__main__":
    main()
