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
// [스크립트 속성 필수 설정]
//   프로젝트 설정 → 스크립트 속성 → 아래 3개 추가:
//     KIS_APP_KEY    = (KIS openAPI appkey)
//     KIS_APP_SECRET = (KIS openAPI appsecret)
//     KIS_MODE       = 0 (모의투자)  또는  1 (실전투자)
// ============================================================

// ── 스프레드시트 ID ────────────────────────────────────────────────────
var LOG_DB_ID  = '1rf4ppPSqYlM9-dr1WcFpHl3g-mCeWzl4xlibZ1OnBbY';
var MAIN_DB_ID = '1DsinKhffeQBwP-_KjryGkfyAo8HpNbCWOtAuZx-iiK4';

var LOG_SHEET_NAME  = '0_주도주_Log';
var MAIN_SHEET_NAME = '1D-Rebound';


// ============================================================
// 진입점 — GET 요청 처리
//
// ?type=lead                              → 주도주 로그
// ?type=trades                            → 매매일지
// ?type=chart&code=XXXXXX
//          [&period=250][&period_div=D|W|M] → 봉 차트 데이터
// ?type=stockinfo&code=XXXXXX            → 현재가 + 기본정보 + 수급
// ?type=all                              → lead + trades (기본값)
// ============================================================
function doGet(e) {
  var type = (e && e.parameter && e.parameter.type) ? e.parameter.type : 'all';

  var result = {};
  try {
    if (type === 'lead'   || type === 'all') result.leadStocks = getLeadStocks();
    if (type === 'trades' || type === 'all') result.trades     = getTrades();

    if (type === 'chart') {
      var code      = (e.parameter.code       || '');
      var period    = parseInt(e.parameter.period    || '250', 10);
      var periodDiv = (e.parameter.period_div || 'D').toUpperCase();
      if (!code) throw new Error('code 파라미터 필요 (예: ?type=chart&code=005930)');
      result.chart = getDailyChart(code, period, periodDiv);
    }

    if (type === 'stockinfo') {
      var code = (e.parameter.code || '');
      if (!code) throw new Error('code 파라미터 필요 (예: ?type=stockinfo&code=005930)');
      result.stockInfo = getStockInfo(code);
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

  return values.slice(1).slice(-2000).map(function(r) {
    return {
      date:       formatCell(r[0]),
      code:       String(r[1] || '').padStart(6, '0'),
      name:       String(r[2] || ''),
      close:      toNum(r[3]),
      changeRate: toNum(r[4]),
      amt:        toNum(r[5]),
      rank:       toNum(r[6]),
      remark:     String(r[7] || ''),
      volRatio:   toNum(r[8] || 0)   // 거래량비(%) — 구버전 행은 0 반환
    };
  });
}


// ============================================================
// 매매일지 반환
// ============================================================
function getTrades() {
  var wb    = SpreadsheetApp.openById(MAIN_DB_ID);
  // '1D-Rebound' 탭이 없으면 'NH-Sniper' 탭 시도, 둘 다 없으면 빈 배열 반환
  var sheet = wb.getSheetByName(MAIN_SHEET_NAME)
           || wb.getSheetByName('NH-Sniper')
           || null;
  if (!sheet) return [];

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
// 봉 차트 데이터 — Naver Finance (API 키 불필요, 무료)
//
// periodDiv: 'D' = 일봉 (기본)
//            'W' = 주봉
//            'M' = 월봉
// ============================================================
function getDailyChart(code, period, periodDiv) {
  period    = period    || 250;
  periodDiv = periodDiv || 'D';

  try {
    return _naverChart_(code, period, periodDiv);
  } catch (e) {
    // Naver 실패 시 KIS 폴백
    try {
      return _kisChart_(code, period, periodDiv);
    } catch (e2) {
      throw new Error('차트 조회 실패 (Naver: ' + e.message + ' / KIS: ' + e2.message + ')');
    }
  }
}

// ── Naver Finance 차트 (API 키 불필요) ──────────────────────────
function _naverChart_(code, period, periodDiv) {
  var tfMap = { 'D': 'day', 'W': 'week', 'M': 'month' };
  var tf    = tfMap[periodDiv] || 'day';
  var url   = 'https://fchart.stock.naver.com/sise.nhn'
            + '?symbol=' + code
            + '&timeframe=' + tf
            + '&count=' + period
            + '&requestType=0';

  var res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  if (res.getResponseCode() !== 200) {
    throw new Error('HTTP ' + res.getResponseCode());
  }

  // XML 파싱: <item data="YYYYMMDD|open|high|low|close|volume"/>
  var xml  = res.getContentText();
  var rows = [];
  var re   = /data="([^"]+)"/g;
  var m;
  while ((m = re.exec(xml)) !== null) {
    var p = m[1].split('|');
    if (p.length < 6) continue;
    var close = toNum(p[4]);
    if (!p[0] || !close) continue;
    rows.push({
      date:   p[0],           // YYYYMMDD
      open:   toNum(p[1]),
      high:   toNum(p[2]),
      low:    toNum(p[3]),
      close:  close,
      volume: toNum(p[5])
    });
  }
  if (!rows.length) throw new Error('Naver 응답 파싱 결과 0건');
  return rows;
}

// ── KIS 차트 폴백 (실전 URL 고정 — 차트는 항상 실전 서버) ──────
function _kisChart_(code, period, periodDiv) {
  var p      = PropertiesService.getScriptProperties();
  var appKey = p.getProperty('KIS_APP_KEY');
  var secret = p.getProperty('KIS_APP_SECRET');
  if (!appKey || !secret) throw new Error('KIS_APP_KEY 미설정');

  // 차트·시세는 실전 서버에서만 제공 (모의 서버 불가)
  var base  = 'https://openapi.koreainvestment.com:9443';
  var token = _kisToken_(appKey, secret, base);

  var now   = new Date();
  var today = Utilities.formatDate(now, 'Asia/Seoul', 'yyyyMMdd');
  var lookbackDays = periodDiv === 'M' ? period * 35
                   : periodDiv === 'W' ? period * 10
                   :                     period * 2;
  var past  = new Date(now.getTime() - lookbackDays * 24 * 3600 * 1000);
  var start = Utilities.formatDate(past, 'Asia/Seoul', 'yyyyMMdd');

  var url = base
    + '/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice'
    + '?FID_COND_MRKT_DIV_CODE=J'
    + '&FID_INPUT_ISCD='      + code
    + '&FID_INPUT_DATE_1='    + start
    + '&FID_INPUT_DATE_2='    + today
    + '&FID_PERIOD_DIV_CODE=' + periodDiv
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
  return output
    .filter(function(d) { return d.stck_bsop_date; })
    .slice(0, period)
    .reverse()
    .map(function(d) {
      return {
        date:   d.stck_bsop_date,
        open:   toNum(d.stck_oprc),
        high:   toNum(d.stck_hgpr),
        low:    toNum(d.stck_lwpr),
        close:  toNum(d.stck_clpr),
        volume: toNum(d.acml_vol)
      };
    });
}


// ============================================================
// 종목 기본정보 + 수급 데이터
//
// [1] inquire-price (FHKST01010100)
//   현재가, 전일대비, 등락률, 시/고/저/전일종가
//   거래량, 거래대금, 52주H/L, 시가총액, PER, PBR, 시장구분
//
// [2] inquire-investor (FHKST01010900)
//   기관·외국인·개인·금융투자·투신·사모 당일 순매수 수량 + 대금
// ============================================================
function getStockInfo(code) {
  var p      = PropertiesService.getScriptProperties();
  var appKey = p.getProperty('KIS_APP_KEY');
  var secret = p.getProperty('KIS_APP_SECRET');
  if (!appKey || !secret) throw new Error('Script Properties 미설정');

  // 시세·수급은 항상 실전 서버 (모의 서버 미지원)
  var base  = 'https://openapi.koreainvestment.com:9443';
  var token = _kisToken_(appKey, secret, base);

  // ── 공통 헤더 베이스 ──────────────────────────────────
  var baseHdr = {
    'content-type':  'application/json',
    'authorization': 'Bearer ' + token,
    'appkey':        appKey,
    'appsecret':     secret,
    'custtype':      'P'
  };

  // ── [1] 현재가 ──────────────────────────────────────────
  var priceRes = UrlFetchApp.fetch(
    base + '/uapi/domestic-stock/v1/quotations/inquire-price'
      + '?FID_COND_MRKT_DIV_CODE=J&FID_INPUT_ISCD=' + code,
    {
      method: 'get',
      headers: _mergeHdr_(baseHdr, 'FHKST01010100'),
      muteHttpExceptions: true
    }
  );
  var pd = JSON.parse(priceRes.getContentText()).output || {};

  // ── [2] 투자자별 순매수 ────────────────────────────────
  var invRes = UrlFetchApp.fetch(
    base + '/uapi/domestic-stock/v1/quotations/inquire-investor'
      + '?FID_COND_MRKT_DIV_CODE=J&FID_INPUT_ISCD=' + code,
    {
      method: 'get',
      headers: _mergeHdr_(baseHdr, 'FHKST01010900'),
      muteHttpExceptions: true
    }
  );
  // output 배열 첫 번째 = 당일 데이터
  var inv = (JSON.parse(invRes.getContentText()).output || [])[0] || {};

  // prdy_vrss_sign: '1'상한 '2'상승 '3'보합 '4'하한 '5'하락
  var sign    = String(pd.prdy_vrss_sign || '3');
  var isDown  = (sign === '4' || sign === '5');
  var chgMult = isDown ? -1 : 1;

  return {
    // ── 가격 ──
    price:        toNum(pd.stck_prpr),
    priceChange:  toNum(pd.prdy_vrss)  * chgMult,
    changeRate:   toNum(pd.prdy_ctrt)  * chgMult,
    prevClose:    toNum(pd.stck_prdy_clpr),
    open:         toNum(pd.stck_oprc),
    high:         toNum(pd.stck_hgpr),
    low:          toNum(pd.stck_lwpr),

    // ── 거래량/대금 ──
    volume:       toNum(pd.acml_vol),
    tradingValue: toNum(pd.acml_tr_pbmn),   // 원 단위

    // ── 52주 ──
    high52w:      toNum(pd.w52_hgpr),
    low52w:       toNum(pd.w52_lwpr),

    // ── 밸류에이션 ──
    marketCapB:   toNum(pd.hts_avls),   // 억원 단위
    per:          toNum(pd.per),
    pbr:          toNum(pd.pbr),

    // ── 시장 구분 ──
    market: String(pd.rprs_mrkt_kor_name || ''),  // "코스피" / "코스닥"

    // ── 투자자별 순매수 (수량 기준, 음수 = 순매도) ──
    instNet:    toNum(inv.orgn_ntby_qty),          // 기관 합계
    instNetAmt: toNum(inv.orgn_ntby_tr_pbmn),      // 기관 순매수대금 (백만원)
    frnNet:     toNum(inv.frgn_ntby_qty),          // 외국인
    frnNetAmt:  toNum(inv.frgn_ntby_tr_pbmn),
    indvNet:    toNum(inv.prsn_ntby_qty),          // 개인
    indvNetAmt: toNum(inv.prsn_ntby_tr_pbmn),

    // ── 기관 세부 ──
    fnncNet:    toNum(inv.fnnc_invt_ntby_qty),     // 금융투자
    ivtrNet:    toNum(inv.invt_trsf_ntby_qty),     // 투신
    prvtNet:    toNum(inv.prmr_fund_ntby_qty),     // 사모
  };
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

function _kisToken_(appKey, secret, base) {
  base = base || _kisBase_();
  var cache  = CacheService.getScriptCache();
  var cacheKey = 'KIS_TOKEN_' + (base.indexOf('vts') > -1 ? 'mock' : 'real');
  var cached = cache.get(cacheKey);
  if (cached) return cached;

  var res = UrlFetchApp.fetch(base + '/oauth2/tokenP', {
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
  cache.put(cacheKey, token, 21600);  // 6시간 캐시
  return token;
}

// tr_id만 다른 헤더 생성 (객체 복사)
function _mergeHdr_(base, trId) {
  return {
    'content-type':  base['content-type'],
    'authorization': base['authorization'],
    'appkey':        base['appkey'],
    'appsecret':     base['appsecret'],
    'custtype':      base['custtype'],
    'tr_id':         trId
  };
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

// KIS 토큰 캐시 수동 초기화 — KIS_MODE 변경 후 GAS 편집기에서 한 번 실행
function clearKisToken() {
  CacheService.getScriptCache().remove('KIS_TOKEN');
  Logger.log('KIS 토큰 캐시 삭제 완료');
}
