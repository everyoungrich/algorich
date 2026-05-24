# AlgoRich 자동매매 시스템 — Claude Code 마스터 가이드

## 프로젝트 개요

- **법인명 (예정)**: EveryoungRich (에버영리치)
- **플랫폼명**: AlgoRich (상표권 등록 완료)
- **웹사이트**: algorich.everyoungrich.com
- **GitHub**: github.com/everyoungrich/algorich
- **로컬 경로**: C:\Algorich
- **목표**: 국내 주식 자동매매 시스템 구축 → 추후 구독 서비스 사업화

---

## 시스템 3단계 구조

```
STEP 1. 관심종목 수집 (스크리닝)
    → KIS API로 매일 15:30 이후 자동 실행
    → 4대 조건 필터링 후 Google Sheets 기록

STEP 2. 매매 실행 (자동매매 봇)
    → 조건 충족 시 KIS API로 주문 실행
    → 매매 결과 Google Sheets 기록

STEP 3. 대시보드 (algorich.everyoungrich.com)
    → GitHub Pages + Google Sheets CSV 연동
    → 관심종목·매매일지 실시간 표시
```

---

## 핵심 파일 구조

```
C:\Algorich\
├── CLAUDE.md              ← 이 파일 (Claude Code 컨텍스트)
├── brokers.py             ← 증권사 API 브로커 추상화 레이어
├── trader_final.py        ← 메인 봇 실행 파일
├── index.html             ← AlgoRich 대시보드 웹페이지
└── logo.png               ← AlgoRich 로고
```

---

## 증권사 API 구조 — 멀티 브로커 설계 원칙

### 현재 연동
- **한국투자증권 (KIS)**: 구현 완료 (모의투자 기준)

### 향후 연동 예정
- **키움증권**: OpenAPI+ (Windows COM 방식)
- **DB금융투자**: REST API

### 브로커 추상화 원칙 (반드시 준수)
새 기능 추가 시 반드시 아래 추상화 구조를 따를 것.
특정 증권사 코드를 직접 호출하지 말고 반드시 브로커 인터페이스를 통할 것.

```python
# brokers.py 설계 원칙
class BaseBroker:
    """모든 증권사 브로커의 기본 인터페이스"""
    def get_access_token(self): raise NotImplementedError
    def get_top_volume(self): raise NotImplementedError
    def get_daily_chart(self, code): raise NotImplementedError
    def place_order(self, code, qty, order_type): raise NotImplementedError
    def get_portfolio(self): raise NotImplementedError
    def get_institutional_flow(self, code): raise NotImplementedError

class KISBroker(BaseBroker):
    """한국투자증권 구현체"""
    BASE_URL_REAL = "https://openapi.koreainvestment.com:9443"
    BASE_URL_MOCK = "https://openapivts.koreainvestment.com:29443"
    # ... 구현

class KiwoomBroker(BaseBroker):
    """키움증권 구현체 (추후)"""
    pass

class DBBroker(BaseBroker):
    """DB금융투자 구현체 (추후)"""
    pass

# 브로커 팩토리 — trader_final.py에서 이것만 호출
def get_broker(name: str) -> BaseBroker:
    brokers = {
        "KIS": KISBroker,
        "KIWOOM": KiwoomBroker,
        "DB": DBBroker,
    }
    return brokers[name]()
```

---

## 관심종목 스크리닝 — 4대 S-Class 조건

### 스크리닝 조건 (AND 조건, 전부 충족해야 함)
1. **거래대금 상위 30위** — `FID_BLNG_CLS_CODE: "3"` (KIS 공식 파라미터)
2. **거래량 전일 대비 300% 이상** — `vol_inrt >= 300.0` (전일의 3배)
3. **주가 상승률 10% 이상** — `prdy_ctrt >= 10.0`
4. **52주 신고가** — 250일 일봉 중 오늘 고가 = 최고가

### 제외 종목
- ETF, ETN, 스팩, 스펙
- 1,000원 미만 동전주
- 상장 250일 미만 신규 상장주

### 실행 타이밍
- **반드시 15:30 이후** 실행 (종가 확정 후)
- cron 스케줄: `30 15 * * 1-5` (평일 15:30)

### KIS API 스펙 (검증 완료)
```python
url = "/uapi/domestic-stock/v1/quotations/volume-rank"
tr_id = "FHPST01710000"
params = {
    "FID_COND_MRKT_DIV_CODE": "J",
    "FID_COND_SCR_DIV_CODE": "20171",
    "FID_INPUT_ISCD": "0000",
    "FID_DIV_CLS_CODE": "0",
    "FID_BLNG_CLS_CODE": "3",   # 거래금액순 정렬 (공식 확인)
    "FID_TRGT_CLS_CODE": "111111111",
    "FID_TRGT_EXLS_CLS_CODE": "000000",
    "FID_INPUT_PRICE_1": "",
    "FID_INPUT_PRICE_2": "",
    "FID_VOL_CNT": "",
    "FID_INPUT_DATE_1": ""
}
# vol_inrt 필드: (오늘 거래량 / 전일 거래량) * 100
# 예: vol_inrt=300 → 전일의 3배 = "300% 이상"
```

---

## 매매 전략

### AlgoRich Strategy Lineup

| 코드명 | 설명 | 상태 |
|--------|------|------|
| `NH-Sniper` | 신고가 상승음봉 정밀타격 | ✅ 구현 중 |
| `1D-Rebound` | 종가베팅 단기 반등 | 🔲 예정 |
| `20D-Swing` | 20일선 눌림 스윙 | 🔲 예정 |
| `20D-Envelope` | 낙폭과대 평균회귀 | 🔲 예정 |
| `200D-Trend` | 장기 추세추종 | 🔲 예정 |

---

### NH-Sniper (신고가 상승음봉) — 구현 중

**전략 코드명**: `NH-Sniper`
**매매일지 기록값**: `"NH-Sniper"`

**매수 조건** (15:15~15:20 체크, 15:20 시장가 실행)
- 관심종목(S-Class) 중에서
- 거래량 전일비 50% 이하
- 등락률 3% 이하
- 5일 이동평균선 위
- (우선순위) 기관수급 — 금융투자·사모펀드·투신 중 하나라도 순매수

**매도 조건**
- 익절: 매수가 대비 +20% 도달 시 보유 수량 50% 시장가 매도
- TS: 종가 기준 5일 이동평균선 이탈 시 잔여 수량 전량 매도
- 손절: 매수가 대비 -3% 이탈 시 전량 손절

**포지션 사이징** (소액 입문용 — 2026-05-22 갱신)
- 종목당 총자산의 25% 배분
- 최대 3종목 동시 보유
- 현금 25% 항상 유지 (안전 버퍼 — 매수 후에도 현금 ≥ 총자산 25% 조건)
- 매수 수량 = (총자산 * 0.25) / 현재가 (소수점 버림, 최소 1주)

---

### 1D-Rebound (종가베팅 단기 반등) — 예정

**전략 코드명**: `1D-Rebound`
**매매일지 기록값**: `"1D-Rebound"`
> 상세 조건 추후 기술

---

### 20D-Swing (20일선 눌림 스윙) — 예정

**전략 코드명**: `20D-Swing`
**매매일지 기록값**: `"20D-Swing"`
> 상세 조건 추후 기술

---

### 20D-Envelope (낙폭과대 평균회귀) — 예정

**전략 코드명**: `20D-Envelope`
**매매일지 기록값**: `"20D-Envelope"`
> 상세 조건 추후 기술

---

### 200D-Trend (장기 추세추종) — 예정

**전략 코드명**: `200D-Trend`
**매매일지 기록값**: `"200D-Trend"`
> 상세 조건 추후 기술

---

### 전략 코딩 원칙
- 모든 전략은 `BaseStrategy` 클래스를 상속해서 구현
- **전략 코드명을 매매일지에 반드시 기록** (성과 비교용)
- 새 전략 추가 시 기존 전략 코드 절대 수정하지 말 것
- 전략 파일은 `strategies/` 폴더에 전략별로 분리

```python
class BaseStrategy:
    name = ""  # 전략 코드명 (예: "NH-Sniper") — 매매일지 기록용
    def check_buy(self, stock_data) -> bool: raise NotImplementedError
    def check_sell(self, position) -> str: raise NotImplementedError
    # 반환값: "HOLD" | "SELL_HALF" | "SELL_ALL"

# 전략 등록 — 새 전략 추가 시 여기만 추가
STRATEGIES = {
    "NH-Sniper":    NHSniperStrategy,
    "1D-Rebound":   OneDReboundStrategy,
    "20D-Swing":    TwentyDSwingStrategy,
    "20D-Envelope": TwentyDEnvelopeStrategy,
    "200D-Trend":   TwoHundredDTrendStrategy,
}
```

---

## Google Sheets 구조

### 시트 1: 주도주 로그 (`0_주도주_Log`)
| 날짜 | 시간 | 종목코드 | 종목명 | 현재가 | 등락률 | 거래량비 | 거래대금 | 조건 | 기관수급 | 매매상태 |
|------|------|----------|--------|--------|--------|----------|----------|------|----------|----------|
| [0] | [1] | [2] | [3] | [4] | [5] | [6] | [7] | [8] | [9] | [10] |

### 시트 2: 매매일지 (`1D-Rebound`)
| 매수일 | 종목코드 | 종목명 | 전략명 | 매수가 | 매수량 | 목표가 | 손절가 | 매도일 | 매도가 | 손익률 | 메모 |
|--------|----------|--------|--------|--------|--------|--------|--------|--------|--------|--------|------|
| [0] | [1] | [2] | [3] | [4] | [5] | [6] | [7] | [8] | [9] | [10] | [11] |

### 스프레드시트 ID
- 주도주 로그: `brokers.py`의 `LOG_DB_ID` 변수 참조
- 매매일지: `brokers.py`의 `MAIN_DB_ID` 변수 참조

---

## 안정성 원칙 — 절대 준수

### 네트워크 타임아웃
```python
import socket
socket.setdefaulttimeout(15.0)  # trader_final.py 최상단에 반드시 있어야 함
```

### API 호출 규칙
- 호출 간격: 최소 300ms (`time.sleep(0.3)`)
- 모든 API 호출은 `safe_request()` 래퍼 함수 사용
- 실패 시 봇 전체가 죽지 않고 해당 종목만 스킵

### 예외처리 필수 패턴
```python
try:
    result = some_api_call()
except (KeyError, ValueError, TypeError, requests.exceptions.Timeout) as e:
    write_log(f"[에러] {e}")
    return None  # 봇 전체를 죽이지 말 것
```

### 주문 실행 원칙
- **모의투자 먼저 검증, 실전 전환은 명시적 설정 변경으로만**
- `IS_REAL = False` 기본값 유지
- 주문 전 반드시 로그 기록
- 동일 종목 중복 주문 방지 로직 필수

---

## 대시보드 (index.html)

### 기술 스택
- 순수 HTML/CSS/JS (프레임워크 없음)
- Google Sheets CSV 직접 fetch
- GitHub Pages 호스팅

### 데이터 연동
```javascript
// Google Sheets 게시 URL (CSV)
const LEAD_CSV_URL  = '...gid=1222820839...'  // 주도주 로그
const TRADE_CSV_URL = '...gid=1786490570...'  // 매매일지
// 1분마다 자동 갱신
setInterval(updateDashboard, 60000)
```

### 디자인 원칙
- 다크 테마 (`#0b0c0d` 배경)
- 포인트 컬러: `#00ffa3` (AlgoRich 그린)
- 모바일 반응형
- 로고: `logo.png` (같은 폴더)

---

## 배포 구조

```
로컬 개발 (C:\Algorich)
    ↓ git push
GitHub (everyoungrich/algorich)
    ↓ GitHub Pages 자동 배포
algorich.everyoungrich.com
    (가비아 DNS: CNAME algorich → everyoungrich.github.io)
```

---

## Claude Code 작업 요청 방법

### 버그 수정 요청 패턴
```
[증상 설명]이 발생하고 있어.
관련 파일 읽고 원인 찾아서 직접 고쳐줘.
고친 후 실행해서 결과 확인해줘.
```

### 새 기능 추가 패턴
```
[기능명] 추가해줘.
- 기존 [파일명] 구조 참고해서
- BaseBroker / BaseStrategy 인터페이스 준수
- 모의투자 모드에서 먼저 테스트
```

### 코드 검증 패턴
```
[파일명]에서 [기능]이 실제로 적용되어 있는지 확인하고
직접 실행해서 결과 보여줘.
```

---

## 현재 진행 상태 (2025-05-16 기준)

- [x] KIS API 토큰 발급 및 갱신 로직
- [x] 4대 S-Class 스크리닝 조건 구현
- [x] 52주 신고가 검증 로직 (예외처리 포함)
- [x] Google Sheets 기록 연동
- [x] socket 타임아웃 15초 설정
- [x] AlgoRich 대시보드 GitHub Pages 배포
- [ ] NH-Sniper 매수 조건 모니터링 (15:15~20)
- [ ] NH-Sniper 매도 로직 (익절/TS/손절) 구현
- [ ] 1D-Rebound 전략 구현
- [ ] 20D-Swing 전략 구현
- [ ] 20D-Envelope 전략 구현
- [ ] 200D-Trend 전략 구현
- [ ] strategies/ 폴더 분리 및 STRATEGIES 딕셔너리 등록
- [ ] 키움증권 브로커 추상화
- [ ] 백테스트 모듈
- [ ] AlgoRich 로고 대시보드 반영
