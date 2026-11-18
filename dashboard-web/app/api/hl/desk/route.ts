// 에이전트 상황실: 분석가 → 메인 → 안전장치 → 주문 흐름, 의견 기록, 활동 타임라인, AI 그림자 성적, 뒤에서 도는 작업 상태.
// 읽는 파일: state/hl_live.json, logs/hl_desk.jsonl(bot/hl_live.py 가 점검마다 한 줄), logs/hl_ai_shadow.csv, state/hl_ai_shadow.json,
// state/hl_signals.json, logs/hl_trades.csv, logs/hl_votes.csv, logs/hl_whales_latest.json, logs/hl_whale_alerts.jsonl
import { connection } from "next/server";
import { stat } from "node:fs/promises";
import path from "node:path";
import { COIN_KO, LOG_DIR, STATE_DIR, procRunning, readCsv, readJson, readJsonl } from "@/lib/data";
import { sgn } from "@/lib/ko";
import { usdKrw } from "@/lib/fx";

type Any = Record<string, any>;
const mtime = (f: string) => stat(f).then((s) => s.mtime.toISOString()).catch(() => null);
const bucket = (iso: string) => Math.floor(Date.parse(iso) / 14_400_000);      // 같은 4시간 점검끼리 묶기

export async function GET() {
  await connection();
  const L = (f: string) => path.join(LOG_DIR, f), S = (f: string) => path.join(STATE_DIR, f);
  const [state, desk, aiRows, ai, signals, trades, votes, whales, alerts, running, tradeOn, krw, tSent, tNews, tReselect] = await Promise.all([
    readJson<Any>(S("hl_live.json"), {}), readJsonl(L("hl_desk.jsonl"), 500), readCsv(L("hl_ai_shadow.csv")),
    readJson<Any | null>(S("hl_ai_shadow.json"), null), readJson<Any>(S("hl_signals.json"), {}),
    readCsv(L("hl_trades.csv")), readCsv(L("hl_votes.csv")), readJson<Any | null>(L("hl_whales_latest.json"), null),
    readJsonl(L("hl_whale_alerts.jsonl"), 20), procRunning("main.py --mode hl-agent$"), procRunning("main.py --mode trade$"), usdKrw(),
    mtime(L("hl_sentiment.csv")), mtime(L("coinness.jsonl")), mtime(L("hl_reselect.out")),
  ]);
  const coins: string[] = Object.keys(state.votes ?? {}).length ? Object.keys(state.votes) : ["BTC", "ETH"];

  // 의견 흐름: 점검 기록 + 같은 점검의 AI 그림자 의견
  const aiAt = new Map<string, number>();
  for (const r of aiRows) aiAt.set(`${r.coin}:${bucket(r.time)}`, +r.ai_exposure);
  const series: Record<string, Any[]> = {};
  for (const c of coins) series[c] = desk.filter((r) => r.coins?.[c]).map((r) => {
    const x = r.coins[c];
    return { t: r.time, ...x.desk, main: x.rule, ai: aiAt.get(`${c}:${bucket(r.time)}`) ?? null };
  });

  // AI 그림자 성적표: 의견대로 1배로 탔다면 (다음 의견 때 가격으로 채점). 규칙 메인도 같은 방식
  const score: Record<string, Any> = {};
  for (const c of coins) {
    const rows = aiRows.filter((r) => r.coin === c);
    let rule = 0, aiPnl = 0, differ = 0;
    for (let i = 0; i + 1 < rows.length; i++) {
      const ret = +rows[i + 1].price / +rows[i].price - 1;
      rule += +rows[i].rule_exposure * ret; aiPnl += +rows[i].ai_exposure * ret;
      if (Math.sign(+rows[i].rule_exposure) !== Math.sign(+rows[i].ai_exposure)) differ++;
    }
    score[c] = { n: rows.length, scored: Math.max(0, rows.length - 1), differ, rule, ai: aiPnl, first: rows[0]?.time ?? null };
  }

  // 활동 기록 (최신순)
  const ev: { t: string; kind: string; title: string; lines: string[] }[] = [];
  for (const r of desk) ev.push({ t: r.time, kind: "check", title: r.urgent ? "명령으로 바로 재조정" : "4시간 점검",
    lines: Object.entries(r.coins as Record<string, Any>).map(([c, x]) =>
      `${COIN_KO[c] ?? c}: ${Object.entries(x.desk as Record<string, number>).map(([k, v]) => `${k} ${sgn(v)}`).join(" · ")} → 메인 ${sgn(x.rule)} → ${x.result}`) });
  const firstDesk = desk[0]?.time ?? "9999";
  const old = new Map<number, { t: string; lines: string[] }>();                 // 점검 기록이 생기기 전: 최종 노출만 남아 있다
  for (const r of votes) if (r.time < firstDesk) {
    const g = old.get(bucket(r.time)) ?? { t: r.time, lines: [] };
    g.lines.push(`${COIN_KO[r.coin] ?? r.coin}: 롱 ${r.long_n}표 · 숏 ${r.short_n}표 → 최종 노출 ${sgn(+r.exposure)}`);
    old.set(bucket(r.time), g);
  }
  for (const g of old.values()) ev.push({ t: g.t, kind: "check", title: "4시간 점검 (예전 기록)", lines: g.lines });
  const aiGroups = new Map<string, Any[]>();
  for (const r of aiRows) aiGroups.set(r.time, [...(aiGroups.get(r.time) ?? []), r]);
  for (const [t, rows] of aiGroups) ev.push({ t, kind: "ai", title: "AI 운용역 의견 (그림자 · 매매 안 함)",
    lines: rows.map((r) => `${COIN_KO[r.coin] ?? r.coin} ${sgn(+r.ai_exposure)} (규칙 메인 ${sgn(+r.rule_exposure)}) — ${r.reason}`) });
  for (const r of trades) ev.push({ t: r.time, kind: "trade", title: `체결: ${COIN_KO[r.coin] ?? r.coin} ${r.side}`,
    lines: [`${r.size}개 @ $${(+r.price).toLocaleString("en-US")}${r.pnl_usd ? ` · 실현 ${+r.pnl_usd >= 0 ? "+" : "−"}$${Math.abs(+r.pnl_usd).toFixed(2)}` : ""}${r.fee_usd ? ` · 수수료 $${(+r.fee_usd).toFixed(2)}` : ""}`, r.why].filter(Boolean) });
  for (const h of (signals.history ?? []) as Any[]) {
    const add = Object.values(h.added ?? {}).flat() as string[], rem = Object.values(h.removed ?? {}).flat() as string[];
    ev.push({ t: h.time, kind: "reselect", title: `투표단 재선정 (${h.reason})`,
      lines: h.kept ? ["합격 규칙이 너무 적어 이전 투표단 유지"] : [`규칙 ${h.total}개${add.length || rem.length ? "" : " · 변화 없음"}`, ...add.map((n) => `+ ${n}`), ...rem.map((n) => `− ${n}`)] });
  }
  for (const a of alerts) ev.push({ t: a.time, kind: "whale", title: "고래 알림 (참고용)", lines: a.msgs ?? [] });
  ev.sort((a, b) => Date.parse(b.t) - Date.parse(a.t));

  return Response.json({
    state, running, krw, coins, ai, series, score, events: ev.slice(0, 80),
    last: desk.at(-1) ?? null, prev: desk.filter((r) => !r.urgent).at(-2) ?? null,
    lastTrade: trades.at(-1) ?? null,
    whales: whales ? { time: whales.time, coins: Object.fromEntries(Object.entries(whales.coins ?? {}).map(([c, v]: [string, any]) => [c, v.groups])) } : null,
    signals: { updated: signals.updated ?? null, n: Object.values(signals.chosen ?? {}).flat().length },
    jobs: [
      { name: "하이퍼리퀴드 봇", what: "30초마다 잔고·가격 확인, 4시간마다 점검", at: state.saved ?? null, ok: running, late: 120 },
      { name: "AI 운용역 (그림자)", what: "4시간 점검 직후 의견 기록", at: ai?.time ?? null, ok: true, late: 5 * 3600 },
      { name: "시장 심리·고래 수집", what: "매시 5분", at: tSent, ok: true, late: 2 * 3600 },
      { name: "코인니스 속보 수집", what: "10분마다", at: tNews, ok: true, late: 1800 },
      { name: "투표단 재선정", what: "거래가 끝날 때와 매주 월요일", at: signals.updated ?? tReselect, ok: true, late: 8 * 86400 },
      { name: "50일선 봇", what: "텔레그램 /hl_* 명령을 하이퍼리퀴드 봇에 전달", at: null, ok: tradeOn, late: 0 },
    ],
    now: new Date().toISOString(),
  });
}
