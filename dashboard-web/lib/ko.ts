// 사람에게 보여 줄 한글 이름·설명. 원본은 bot/strategies.py 의 KO_NAME (이름을 바꾸면 양쪽 같이).

export const KO_NAME: Record<string, string> = {
  rsi_momo_1h: "상승세 올라타기(1시간)",
  rsi_momo_4h: "상승세 올라타기(4시간)",
  breakout_vol_1h: "거래 몰릴 때 최고가 돌파(1시간)",
  sma_trend_daily: "평균선 위면 들고 있기(하루)",
  vb_daily: "아침에 크게 오르면 사기(하루)",
  vwap_revert: "평균보다 쌀 때 사기(15분)",
  discretionary: "AI 직접 판단",
  none: "현금 대기",
  keep: "전략 유지",
  hold: "그냥 들고 있기",
};

export const KO_PARAM: Record<string, string> = {
  entry: "살 때 힘 기준", exit: "팔 때 힘 기준", max_hold: "최대 보유 시간", N: "최고가 볼 기간(봉)",
  vol_mult: "거래량 몇 배", M: "최저가 볼 기간(봉)", period: "평균 낼 일수", k: "전날 출렁임 몇 배",
  ma_n: "평균 낼 일수", dev: "평균보다 몇 % 쌀 때",
};

// AI 가 쓴 문장 속 어려운 말 → 쉬운 말. 원본은 bot/strategies.py 의 EASY_WORDS (양쪽 같이).
const EASY_WORDS: [RegExp, string][] = [
  [/\bMACD(\s?hist)?\b/gi, "오르내림 힘"], [/\bdonchian\d*_pos\b/gi, "최근 범위 속 위치(0=바닥, 1=꼭대기)"],
  [/(호가\s?)?\btop\d+\s?imbalance\b/gi, "사자·팔자 주문 차이"], [/\bimbalance\b/gi, "주문 차이"],
  [/(체결\s?)?\bbuy_ratio\b/gi, "사는 쪽 비율"], [/과매도/g, "너무 많이 떨어진"], [/과매수/g, "너무 많이 오른"],
  [/현물\s?롱\s?전용/g, "오를 때만 돈을 버는 방식"], [/세션\s?VWAP/gi, "오늘 평균 거래가"], [/세션\s?/g, "오늘 "],
  [/\+DI|\bpdi\b/gi, "상승 힘"], [/-DI|\bmdi\b/gi, "하락 힘"], [/\bADX\b/gi, "추세 세기"], [/\bRSI\b/gi, "과열 지수"],
  [/\b[ES]MA\b/gi, "평균선"], [/\bATR\b/gi, "평소 출렁임폭"], [/\bVWAP\b/gi, "오늘 평균 거래가"],
  [/z\s?점수|z-?score/gi, "평소보다 튄 정도"], [/밴드폭\s?백분위/g, "출렁임 크기 순위"], [/볼린저\s?밴드?/g, "가격 범위 띠"],
  [/역배열/g, "내림 순서"], [/정배열/g, "오름 순서"], [/휩쏘/g, "속임 신호"], [/모멘텀/g, "오르는 힘"],
  [/음봉/g, "내린 봉"], [/양봉/g, "오른 봉"], [/지지선/g, "버티던 가격"], [/저항선/g, "막히던 가격"],
  [/레짐|국면/g, "시장 흐름"], [/플랜/g, "전략"], [/\bescalate\b/gi, "다시 판단"], [/\bkeep\b/gi, "유지"],
  [/\bconfidence\b/gi, "확신도"], [/\bbuy_now\b/gi, "바로 매수"], [/\bsell_now\b/gi, "바로 매도"],
  [/\bfollow_plan\b/gi, "전략대로"], [/\b1d\b/g, "하루봉"], [/\b(\d+)h\b/g, "$1시간봉"], [/\b(\d+)m\b/g, "$1분봉"],
];

export const EMOJI: Record<string, string> = {
  rsi_momo_1h: "⚡", rsi_momo_4h: "🔥", breakout_vol_1h: "🚀", sma_trend_daily: "📈",
  vb_daily: "🎯", vwap_revert: "🌊", discretionary: "✋", none: "💤", hold: "🪙",
};

export const DESC: Record<string, string> = {
  rsi_momo_1h: "1시간봉에서 오르는 힘이 아주 세지면 올라타고, 힘이 빠지면 팝니다.",
  rsi_momo_4h: "4시간봉에서 오르는 힘이 아주 세지면 올라타고, 힘이 빠지면 팝니다.",
  breakout_vol_1h: "거래가 몰리면서 최근 24시간 최고가를 넘으면 사고, 최근 12시간 최저가를 깨면 팝니다.",
  sma_trend_daily: "하루 종가가 50일 평균보다 위면 들고 있고, 아래로 내려가면 다음 날 아침 9시에 팝니다.",
  vb_daily: "아침 9시 가격에서 전날 출렁인 폭의 절반 이상 오르면 사고, 다음 날 아침 9시에 팝니다.",
  vwap_revert: "오늘 평균 거래가보다 1% 싸지면 지정가로 사고, 평균가로 돌아오면 팝니다.",
  discretionary: "정해진 규칙 없이 AI가 근거를 대고 예산의 30% 안에서 직접 사고팝니다.",
  none: "사지 않고 현금으로 기다립니다.",
  hold: "아무것도 안 하고 BTC를 그냥 들고 있었을 때예요. 비교 기준이에요.",
};

export const ACTION: Record<string, string> = {
  follow_plan: "전략대로", buy_now: "바로 매수", sell_now: "바로 매도", hold: "기다림",
};

export const SOURCE: Record<string, string> = {
  플랜: "전략 신호", 재량: "AI 판단", 손절: "손절", 최대보유: "보유 시간 초과", 킬: "전체 손실 한도",
  수동: "직접 명령", 인계: "계좌에서 넘겨받음",
};

export const LIB = ["sma_trend_daily", "rsi_momo_4h", "rsi_momo_1h", "breakout_vol_1h", "vb_daily",
  "vwap_revert", "discretionary", "none"];

export const ko = (sid?: string | null) => (sid ? KO_NAME[sid] ?? sid : "-");

export const koParams = (p?: Record<string, number> | null) =>
  Object.entries(p ?? {}).map(([k, v]) => `${KO_PARAM[k] ?? k} ${v}`).join(", ") || "기본값";

/** AI 가 쓴 문장 속 전략 ID·지표 이름을 쉬운 말로. */
export function koSub(text?: string | null): string {
  let t = text ?? "";
  for (const sid of Object.keys(KO_NAME).sort((a, b) => b.length - a.length)) {
    if (sid.length > 4) t = t.split(sid).join(KO_NAME[sid]);
  }
  for (const [rx, word] of EASY_WORDS) t = t.replace(rx, word);
  return t;
}

export const won = (x?: number | null, sign = true) =>
  x == null ? "-" : `${sign && x > 0 ? "+" : ""}${Math.round(x).toLocaleString("ko-KR")}원`;

export const pct = (x?: number | null, d = 2) => (x == null ? "-" : `${x > 0 ? "+" : ""}${x.toFixed(d)}%`);

/** 노출·의견 값(−1~+1) 부호 붙여 두 자리로. */
export const sgn = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)}`;

export const tone = (x?: number | null) => (!x ? "" : x > 0 ? "text-good" : "text-bad");

export function ago(iso?: string | null): string {
  if (!iso) return "-";
  const sec = (Date.now() - new Date(iso).getTime()) / 1000;
  if (sec < 60) return "방금";
  if (sec < 3600) return `${Math.round(sec / 60)}분 전`;
  if (sec < 86400) return `${Math.round(sec / 3600)}시간 전`;
  return `${Math.round(sec / 86400)}일 전`;
}

export const hhmm = (iso?: string | null) => (iso ? iso.slice(11, 16) : "-");
