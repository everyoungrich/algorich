# AlgoRich — Cowork 작업 지시서

## 너의 역할
너는 AlgoRich 자동매매 플랫폼 구축을 돕는 AI 에이전트야.
CLAUDE.md를 먼저 읽고 전체 맥락을 파악한 후 작업해줘.
작업 순서는 아래 우선순위를 따르고, 하나 완료되면 다음으로 넘어가줘.
모든 수정 후에는 반드시 GitHub push → 브라우저 테스트 → 결과 확인까지 해줘.

---

## 작업 환경
- 로컬 경로: C:\Algorich
- 대시보드: algorich.everyoungrich.com
- GitHub: github.com/everyoungrich/algorich
- Claude Code: C:\Algorich 폴더에서 실행

---

## 현재 상태 (2026-05-17 기준)

### 완료된 것
- [x] KIS API 연동 (모의투자)
- [x] 4대 S-Class 스크리닝 (brokers.py)
- [x] Google Sheets 주도주 로그 기록
- [x] algorich.everyoungrich.com 배포 (GitHub Pages)
- [x] 관심종목 리스트 표시
- [x] Apps Script API 배포 (AlgoRich-Dashboard-API)
- [x] CLAUDE.md 작성 완료

### 미완료 — 우선순위 순서

#### 🔴 P1 — 지금 당장 (차트 문제)
- [ ] 종목 클릭 시 차트 모달이 안 뜸
  - 원인 추정: CHART_API_URL 미설정 또는 Apps Script CORS 문제
  - 확인 방법: 브라우저 F12 → Console 에러 확인
  - 해결 순서:
    1. index.html에서 CHART_API_URL 값 확인
    2. Apps Script 배포 URL이 올바르게 들어갔는지 확인
    3. Apps Script "다음 사용자로 실행: 나" 설정 확인
    4. 에러 수정 후 GitHub push → 재테스트

#### 🔴 P1 — 대시보드 UI
- [ ] 로고(logo.png) 텍스트로만 표시됨 → img 경로 수정
- [ ] "오늘 주도주 스캔" 숫자가 - 표시 → 최근 스캔일 기준으로 수정
- [ ] 거래대금 컬럼 추가 (억원 단위)

#### 🟡 P2 — 차트 완성
- [ ] 1년(250 거래일) 캔들차트
- [ ] 5일선(흰색), 20일선(노란색), 60일선(주황색)
- [ ] 52주 신고가 수평선 (빨간 점선)
- [ ] 거래량 바차트
- [ ] 기본 정보: 전일/시가/고가/저가/거래량/거래대금/시총
- [ ] 수급 정보: 기관/외국인/개인 순매수

#### 🟡 P2 — 매매 로직
- [ ] 신고가 상승음봉 전략 매수 조건 모니터링 (15:15~15:20)
- [ ] 매도 로직 구현 (익절 +20% / TS 5일선 / 손절 -3%)
- [ ] 눌림목 매매 후보 패널 데이터 연동

#### 🟢 P3 — 확장
- [ ] 20일선 눌림목 스윙 전략 추가
- [ ] 키움증권 브로커 추상화
- [ ] 성과 분석 섹션 (승률/MDD/손익비)
- [ ] 백테스트 모듈

---

## 작업 완료 기준

### 차트 완료 기준
- algorich.everyoungrich.com 접속
- 관심종목 행 클릭
- 1초 내 차트 모달 오픈
- 캔들차트 + 이동평균선 + 52주 신고가선 표시
- 기본 정보 그리드 표시

### 대시보드 완료 기준
- 로고 이미지 정상 표시
- 관심종목 최신 데이터 표시 (거래대금 포함)
- 눌림목 매매 후보 패널 데이터 표시
- 오늘 스캔 건수 정상 표시

---

## 반복 작업 패턴

```
1. Claude Code로 코드 수정
2. git add . && git commit -m "설명" && git push
3. 1~2분 대기 (GitHub Pages 배포)
4. 브라우저에서 algorich.everyoungrich.com 접속
5. F12 콘솔 에러 확인
6. 에러 있으면 1번으로 돌아가기
```

---

## 중요 파일 위치

```
C:\Algorich\
├── CLAUDE.md          ← 전략·코드 규칙 (반드시 먼저 읽기)
├── COWORK.md          ← 이 파일
├── brokers.py         ← KIS API + 스크리닝 로직
├── trader_final.py    ← 메인 봇 (socket 타임아웃 포함)
└── index.html         ← AlgoRich 대시보드
```

---

## Apps Script 정보

- 프로젝트명: AlgoRich-Dashboard-API
- 용도: 대시보드 데이터 API + KIS API 프록시
- 설정: 다음 사용자로 실행 = "나", 액세스 = "Google 계정이 있는 모든 사용자"
- Script Properties:
  - KIS_APP_KEY: brokers.py 참조
  - KIS_APP_SECRET: brokers.py 참조
  - KIS_MODE: 0 (모의투자) / 1 (실전)

---

## 작업 시작 명령어

Cowork 실행 후 이렇게 시작해줘:
1. C:\Algorich\CLAUDE.md 읽기
2. C:\Algorich\COWORK.md 읽기 (이 파일)
3. P1 우선순위부터 순서대로 처리
4. 각 항목 완료 시 체크박스 업데이트
