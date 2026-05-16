// ============================================================
// AlgoRich Dashboard API — Google Apps Script Web App
// 배포 방법:
//   1. script.google.com → 새 프로젝트
//   2. 이 코드 전체 붙여넣기
//   3. 배포 → 새 배포 → 유형: 웹 앱
//      - 다음 사용자로 실행: 나 (Me)
//      - 액세스 권한: 모든 사용자 (Anyone)
//   4. 배포 URL 복사 → 웹사이트 JS에 붙여넣기
// ============================================================

// ── 스프레드시트 ID (utils.py 에서 확인) ──────────────────────
var LOG_DB_ID  = '1rf4ppPSqYlM9-dr1WcFpHl3g-mCeWzl4xlibZ1OnBbY'; // 주도주 Log
var MAIN_DB_ID = '1DsinKhffeQBwP-_KjryGkfyAo8HpNbCWOtAuZx-iiK4'; // 매매일지

// ── 시트 이름 매핑 (utils.py get_main_sheet / get_log_sheet 참조) ──
var LOG_SHEET_NAME  = '0_주도주_Log';
var MAIN_SHEET_NAME = '1D-Rebound';


// ============================================================
// 진입점 — GET 요청 처리
// ?type=lead   → 주도주 로그만 반환
// ?type=trades → 매매일지만 반환
// ?type=all    → 둘 다 반환 (기본값)
// ============================================================
function doGet(e) {
  var type = (e && e.parameter && e.parameter.type) ? e.parameter.type : 'all';

  var result = {};
  try {
    if (type === 'lead'   || type === 'all') result.leadStocks = getLeadStocks();
    if (type === 'trades' || type === 'all') result.trades     = getTrades();
    result.status    = 'ok';
    result.timestamp = new Date().toISOString();
  } catch (err) {
    result.status = 'error';
    result.error  = err.toString();
  }

  // ContentService 는 "모든 사용자" 배포 시 자동으로 CORS 허용
  return ContentService
    .createTextOutput(JSON.stringify(result))
    .setMimeType(ContentService.MimeType.JSON);
}


// ============================================================
// 주도주 Log 반환
// 시트 컬럼: 날짜 | 종목코드 | 종목명 | 종가 | 상승률(%) | 거래대금(억) | 순위 | 비고
// utils.py log_lead_stock() 기준
// ============================================================
function getLeadStocks() {
  var sheet = SpreadsheetApp.openById(LOG_DB_ID).getSheetByName(LOG_SHEET_NAME);
  if (!sheet) throw new Error('시트를 찾을 수 없음: ' + LOG_SHEET_NAME);

  var values = sheet.getDataRange().getValues();
  if (values.length <= 1) return [];

  // 헤더 제외, 최신 50건
  return values.slice(1).slice(-50).map(function(r) {
    return {
      date:       formatCell(r[0]),
      code:       String(r[1] || '').padStart(6, '0'),
      name:       String(r[2] || ''),
      close:      toNum(r[3]),
      changeRate: toNum(r[4]),
      amt:        toNum(r[5]),
      rank:       toNum(r[6]),
      remark:     String(r[7] || '')
    };
  });
}


// ============================================================
// 매매일지 반환
// 시트 컬럼: Trade ID | 상태 | 종목코드 | 종목명 | 매수일자 | 매수단가 |
//           매수수량 | 투자금액 | 매도일자 | 매도단가 | 수익률(%) | 수익금(원) | 비고
// utils.py log_buy / log_sell 기준
// ============================================================
function getTrades() {
  var sheet = SpreadsheetApp.openById(MAIN_DB_ID).getSheetByName(MAIN_SHEET_NAME);
  if (!sheet) throw new Error('시트를 찾을 수 없음: ' + MAIN_SHEET_NAME);

  var values = sheet.getDataRange().getValues();
  if (values.length <= 1) return [];

  // 헤더 제외, 최신 50건
  return values.slice(1).slice(-50).map(function(r) {
    return {
      tradeId:   String(r[0]  || ''),
      status:    String(r[1]  || ''),
      code:      String(r[2]  || '').padStart(6, '0'),
      name:      String(r[3]  || ''),
      buyDate:   formatCell(r[4]),
      buyPrice:  toNum(r[5]),
      qty:       toNum(r[6]),
      invest:    toNum(r[7]),
      sellDate:  formatCell(r[8]),
      sellPrice: toNum(r[9]),
      profitRt:  toNum(r[10]),
      profit:    toNum(r[11]),
      remark:    String(r[12] || '')
    };
  });
}


// ============================================================
// 헬퍼 함수
// ============================================================

// Google Sheets 의 Date 객체 → "YYYY-MM-DD" 문자열 변환
function formatCell(val) {
  if (!val) return '';
  if (val instanceof Date) {
    var y = val.getFullYear();
    var m = String(val.getMonth() + 1).padStart(2, '0');
    var d = String(val.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + d;
  }
  return String(val);
}

// 숫자 변환 (빈 값 → 0, NaN → 0)
function toNum(val) {
  var n = Number(val);
  return isNaN(n) ? 0 : n;
}
