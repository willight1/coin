// 하이퍼리퀴드 롱·숏 추세 봇 상세 (state/hl_live.json, logs/hl_trades.csv, logs/hl_equity.csv)
import { connection } from "next/server";
import { open } from "node:fs/promises";
import path from "node:path";
import { LOG_DIR, STATE_DIR, procRunning, readCsv, readJson, readJsonl } from "@/lib/data";
import { usdKrw } from "@/lib/fx";

// 코인니스 최근 속보 (research/coinness.py 가 10분마다 쌓는 170MB+ 파일 — 끝 256KB만 읽는다)
async function coinness(n = 12) {
  try {
    const f = await open(path.join(LOG_DIR, "coinness.jsonl"), "r");
    try {
      const { size } = await f.stat(); const len = Math.min(size, 262_144);
      const buf = Buffer.alloc(len); await f.read(buf, 0, len, size - len);
      const seen = new Map<number, any>();
      for (const l of buf.toString("utf8").split("\n").slice(1)) { try { const x = JSON.parse(l); seen.set(x.id, x); } catch { /* 잘린 줄 */ } }
      return [...seen.values()].sort((a, b) => (a.t < b.t ? 1 : -1)).slice(0, n)
        .map((x) => ({ t: x.t, title: x.title, imp: x.imp, cat: x.cat, codes: x.codes }));
    } finally { await f.close(); }
  } catch { return []; }
}

export async function GET(request: Request) {
  await connection();
  // ?inst=paper = 연습용 쌍둥이(--mode hl-paper): 같은 화면에 hl_paper_* 파일을 보여 준다. 고래·심리·속보는 공통 참고 자료
  // ?inst=aipaper = AI 연습 계좌(--mode hl-ai): AI 가 15분마다 노출을 정하는 가상 계좌. 마지막 판단은 state.ai
  const q = new URL(request.url).searchParams.get("inst");
  const inst = q === "paper" || q === "aipaper" ? q : "";
  const paper = !!inst;
  const n = inst ? `hl_${inst}` : "hl";
  const [state, trades, equity, running, krw, senti, votes, whales, whaleAlerts, news, shadow, chat] = await Promise.all([
    readJson<Record<string, any>>(path.join(STATE_DIR, `${n}_live.json`), {}),
    readCsv(path.join(LOG_DIR, `${n}_trades.csv`)),
    readCsv(path.join(LOG_DIR, `${n}_equity.csv`)),
    procRunning(inst === "paper" ? "main.py --mode hl-paper$" : inst === "aipaper" ? "main.py --mode hl-ai$" : "main.py --mode hl-agent$"), usdKrw(),
    readCsv(path.join(LOG_DIR, "hl_sentiment.csv")),
    readCsv(path.join(LOG_DIR, `${n}_votes.csv`)),
    readJson<Record<string, any> | null>(path.join(LOG_DIR, "hl_whales_latest.json"), null),   // 고래 청산 지도 (research/hl_sentiment.py)
    readJsonl(path.join(LOG_DIR, "hl_whale_alerts.jsonl"), 30).then((r) => r.reverse()),          // 고래 알림 기록 (최근 30회)
    coinness(),
    paper ? null : readJson<Record<string, any> | null>(path.join(STATE_DIR, "hl_ai_shadow.json"), null),   // AI 운용역 그림자 (bot/hl_ai.py, 매매 안 함)
    paper ? [] : readJsonl(path.join(LOG_DIR, "hl_chat.jsonl"), 60),                             // 주문 대화창 (봇 알림 + 대시보드에서 보낸 말)
  ]);
  const ai = inst === "aipaper" ? (state.ai ?? null) : shadow;
  for (const [c, p] of Object.entries<any>(state.pos ?? {}))          // 가상 포지션에는 거래소가 주는 미실현 손익이 없다
    if (p.upnl == null && state.marks?.[c]) p.upnl = p.size * (state.marks[c] - p.entry);
  const sentiment: Record<string, Record<string, string>> = {};       // 코인별 마지막 기록 (참고용)
  for (const r of senti) sentiment[r.coin] = r;
  const sentimentPrev: Record<string, Record<string, string>> = {};   // 코인별 약 24시간 전 기록 (지표 해설의 '하루 변화'용)
  for (const c of Object.keys(sentiment)) { const rows = senti.filter((r) => r.coin === c); if (rows.length > 24) sentimentPrev[c] = rows[rows.length - 25]; }
  return Response.json({
    chat, state, running, krw, sentiment, sentimentPrev, whales, whaleAlerts, news, ai, paper, inst,
    exposure: votes.slice(-3000).map((r) => ({ t: r.time, coin: r.coin, v: +r.exposure })), sentimentRows: senti.length,
    trades: trades.slice(-50).reverse(),
    equity: equity.slice(-3000).map((r) => ({ t: r.time, v: +r.equity_usd })),
    now: new Date().toISOString(),
  });
}
