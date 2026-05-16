// ============================================================
// AlgoRich Dashboard API — Google Apps Script Web App
// 배포 방법:
//   1. script.google.com → 새 프로젝트
//   2. 이 코드 전체 붙여넣기
//   3. 배포 → 새 배포 → 유형: 웹 앱
//      - 다음 사용자로 실행: 나 (Me)
//      - 액세스 권한: 모든 사용자 (Anyone)
//   4. 배포 URL 복사 → index.html CHART_API_URL에 붙여넣기
//
// [차트 기능 추가 후 필수 설정]
//   프로젝트 설정 → 스크립트 속성 → 아래 3개 추가:
//     KIS_APP_KEY    = (KIS openAPI appkey)
//     KIS_APP_SECRET = (KIS openAPI appsecret)
//     KIS_MODE       = 0  (모의투자) 또는 1 (실전투자)
// ============================================================

// ── 스프레드시트 ID ───────────────────────────────────────────────
var LOG_DB_ID  = '1rf4ppPSqYlM9-dr1WcFpHl3g-mCeWzl4xlibZ1OnBbY';
var MAIN_DB_ID = '1DsinKhffeQBwP-_KjryGkfyAo8HpNbCWOtAuZx-iiK4';

var LOG_SHEET_NAME  = '0_주도주_Log';
var MAIN_SHEET_NAME = '1D-Rebound';


// ============================================================
// 진입점 — GET 요청 처리
// ?type=lead      → 주도주 로그
// ?type=trades    → 매매일지
// ?type=chart&code=XXXXXX[&period=250]  → 일봉 차트 데이터 (KIS 프록시)
// ?type=all       → lead + trades (기본값)
// ============================================================
function doGet(e) {
  var type = (e && e.parameter && e.parameter.type) ? e.parameter.type : 'all';

  var result = {};
  try {
    if (type === 'lead'   || type === 'all') result.leadStocks = getLeadStocks();
    if (type === 'trades' || type === 'all') result.trades     = getTrades();
    if (type === 'chart') {
      var code   = (e && e.parameter && e.parameter.code)   ? e.parameter.code   : '';
      var period = (e && e.parameter && e.parameter.period) ? parseInt(e.parameter.period, 10) : 250;
      if (!code) throw new Error('code 파라미터 필요 (예: ?type=chart&code=005930)');
      result.chart = getDailyChart(code, period);
    }
    result.status    = 'ok';
    result.timestamp = new Date().toISOString();
  } catch (err) {
    result.status = 'error';
    result.error  = err.toString();
  }

  return ContentService
    .createTextOutput(JSON.stringify(result))
    .setMimeType(ContentService.MimeType.JSON);
}


// ============================================================
// 주도주 Log 반환
// ============================================================
function getLeadStocks() {
  var sheet = SpreadsheetApp.openById(LOG_DB_ID).getSheetByName(LOG_SHEET_NAME);
  if (!sheet) throw new Error('시트를 찾을 수 없음: ' + LOG_SHEET_NAME);

  var values = sheet.getDataRange().getValues();
  if (values.length <= 1) return [];

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
// ============================================================
function getTrades() {
  var sheet = SpreadsheetApp.openById(MAIN_DB_ID).getSheetByName(MAIN_SHEET_NAME);
  if (!sheet) throw new Error('시트를 찾을 수 없음: ' + MAIN_SHEET_NAME);

  var values = sheet.getDataRange().getValues();
  if (values.length <= 1) return [];

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
// 일봉 차트 데이터 — KIS API 프록시 (CORS 우회)
//
// 사용 API: inquire-daily-itemchartprice (FHKST03010100)
//   - 날짜 범위 지정 가능 → 250 거래일 (1년) 한 번에 확보
//   - brokers.py get_daily_prices()와 동일 엔드포인트
//
// Script Properties 필수:
//   KIS_APP_KEY, KIS_APP_SECRET, KIS_MODE
// ============================================================
function getDailyChart(code, period) {
  period = period || 250;

  var p      = PropertiesService.getScriptProperties();
  var appKey = p.getProperty('KIS_APP_KEY');
  var secret = p.getProperty('KIS_APP_SECRET');
  if (!appKey || !secret) {
    throw new Error('Script Properties 미설정 — KIS_APP_KEY / KIS_APP_SECRET 추가 필요');
  }

  var token = _kisToken_(appKey, secret);
  var base  = _kisBase_();

  // 250 거래일 확보를 위해 캘린더 기준 400일 범위 요청
  var now   = new Date();
  var past  = new Date(now.getTime() - 400 * 24 * 3600 * 1000);
  var today = Utilities.formatDate(now,  'Asia/Seoul', 'yyyyMMdd');
  var start = Utilities.formatDate(past, 'Asia/Seoul', 'yyyyMMdd');

  var url = base
    + '/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice'
    + '?FID_COND_MRKT_DIV_CODE=J'
    + '&FID_INPUT_ISCD='    + code
    + '&FID_INPUT_DATE_1='  + start
    + '&FID_INPUT_DATE_2='  + today
    + '&FID_PERIOD_DIV_CODE=D'
    + '&FID_ORG_ADJ_PRC=0';

  var res = UrlFetchApp.fetch(url, {
    method: 'get',
    headers: {
      'content-type':  'application/json',
      'authorization': 'Bearer ' + token,
      'appkey':        appKey,
      'appsecret':     secret,
      'tr_id':         'FHKST03010100',
      'custtype':      'P'
    },
    muteHttpExceptions: true
  });

  var output = JSON.parse(res.getContentText()).output2 || [];

  // output2는 최신→과거 순. period개 슬라이스 후 reverse → 과거→최신 정렬
  return output
    .filter(function(d) { return d.stck_bsop_date; })
    .slice(0, period)
    .reverse()
    .map(function(d) {
      return {
        date:   d.stck_bsop_date,   // YYYYMMDD
        open:   toNum(d.stck_oprc),
        high:   toNum(d.stck_hgpr),
        low:    toNum(d.stck_lwpr),
        close:  toNum(d.stck_clpr),
        volume: toNum(d.acml_vol)
      };
    });
}


// ============================================================
// KIS 내부 헬퍼
// ============================================================
function _kisBase_() {
  var mode = PropertiesService.getScriptProperties().getProperty('KIS_MODE') || '0';
  return mode === '1'
    ? 'https://openapi.koreainvestment.com:9443'
    : 'https://openapivts.koreainvestment.com:29443';
}

function _kisToken_(appKey, secret) {
  var cache  = CacheService.getScriptCache();
  var cached = cache.get('KIS_TOKEN');
  if (cached) return cached;

  var res = UrlFetchApp.fetch(_kisBase_() + '/oauth2/tokenP', {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify({
      grant_type: 'client_credentials',
      appkey: appKey,
      appsecret: secret
    }),
    muteHttpExceptions: true
  });

  var token = JSON.parse(res.getContentText()).access_token;
  if (!token) throw new Error('KIS 토큰 발급 실패 — APP_KEY / APP_SECRET 확인');
  cache.put('KIS_TOKEN', token, 21600); // 6시간 캐시
  return token;
}


// ============================================================
// 공통 헬퍼
// ============================================================
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

function toNum(val) {
  var n = Number(val);
  return isNaN(n) ? 0 : n;
}
