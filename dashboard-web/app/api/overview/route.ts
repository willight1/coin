// 봇 3개(업비트 50일선·업비트 AI·하이퍼리퀴드) 한눈에. 메인 화면 맨 위 요약과 탭 목록에 쓴다.
import { connection } from "next/server";
import path from "node:path";
import { STATE_DIR, agentRunning, coinDirs, coins, procRunning, readJson, tradeTick } from "@/lib/data";
import { usdKrw } from "@/lib/fx";

export async function GET() {
  await connection();
  const all = await coins();
  const [tick, tradeOn, hl, hlOn, dtStates, dtOn, krw, pp, ppOn, ap, apOn] = await Promise.all([
    tradeTick(), procRunning("main.py --mode trade$"),
    readJson<Record<string, any>>(path.join(STATE_DIR, "hl_live.json"), {}), procRunning("main.py --mode hl-agent$"),
    Promise.all(all.map((k) => readJson<Record<string, any>>(path.join(coinDirs(k).state, "dt_live.json"), {}))),
    Promise.all(all.map((k) => agentRunning(k))), usdKrw(),
    readJson<Record<string, any> | null>(path.join(STATE_DIR, "hl_paper_live.json"), null), procRunning("main.py --mode hl-paper$"),   // 연습용 쌍둥이
    readJson<Record<string, any> | null>(path.join(STATE_DIR, "hl_aipaper_live.json"), null), procRunning("main.py --mode hl-ai$"),      // AI 연습 계좌
  ]);
  return Response.json({
    trade: { running: tradeOn, tick },
    dt: all.map((k, i) => ({ coin: k, ko: coinDirs(k).coinKo, running: dtOn[i], equity: dtStates[i].equity ?? null, budget: dtStates[i].budget_krw ?? null,
      pos: !!dtStates[i].position, live: !!dtStates[i].live, halt: dtStates[i].halt || (dtStates[i].stop_file ? "새로 사지 않기" : "") })),
    hl: { running: hlOn, live: !!hl.live, equity: hl.equity_usd ?? null, start: hl.start_usd ?? null, pos: hl.pos ?? {}, halt: hl.halt || "", saved: hl.saved ?? null, krw },
    hlAi: ap && { running: apOn, equity: ap.equity_usd ?? null, start: ap.start_usd ?? null, pos: ap.pos ?? {} },
    hlPaper: pp && { running: ppOn, equity: pp.equity_usd ?? null, start: pp.start_usd ?? null, pos: pp.pos ?? {} },
    now: new Date().toISOString(),
  });
}
