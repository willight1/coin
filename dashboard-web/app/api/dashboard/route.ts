// 화면 한 번에 필요한 것 전부. 판단 기록은 무거운 스냅샷을 빼고 보낸다 (상세는 /api/decision).
import { connection } from "next/server";
import { existsSync } from "node:fs";
import path from "node:path";
import { agentRunning, coinDirs, coins, readCsv, readJson, readJsonl, readText } from "@/lib/data";

export async function GET(request: Request) {
  await connection(); // 요청마다 새로 읽는다
  const c = coinDirs(new URL(request.url).searchParams.get("coin"));
  const all = await coins();
  const [state, decisions, trades, equity, lessons, reviews, running, states] = await Promise.all([
    readJson<Record<string, any>>(path.join(c.state, "dt_live.json"), {}),
    readJsonl(path.join(c.log, "dt_decisions.jsonl"), 60),
    readCsv(path.join(c.log, "dt_trades.csv")),
    readCsv(path.join(c.log, "dt_equity.csv")),
    readText(path.join(c.state, "dt_lessons.md")),
    readJsonl(path.join(c.log, "dt_reviews.jsonl"), 1),
    agentRunning(c.coin),
    Promise.all(all.map((k) => readJson<Record<string, any>>(path.join(coinDirs(k).state, "dt_live.json"), {}))),
  ]);
  // API 예산은 모든 코인 봇이 같이 쓴다
  state.api_total_usd_all = states.reduce((a, s) => a + (s.llm?.api_total_usd ?? 0), 0);
  const slim = decisions.map(({ snapshot, ...rest }) => rest);
  return Response.json({
    state,
    running,
    inboxPending: existsSync(path.join(c.state, "dt_commands.txt")),
    coin: c.coin,
    coinKo: c.coinKo,
    coins: all.map((k, i) => ({ coin: k, ko: coinDirs(k).coinKo, equity: states[i].equity ?? null, pos: !!states[i].position })),
    decisions: slim.reverse(),
    trades: trades.slice(-50).reverse(),
    equity: equity.slice(-2000).map((r) => ({ t: r.time, equity: +r.equity, price: +r.price })),
    lessons: lessons.trim(),
    review: reviews[0] ?? null,
    now: new Date().toISOString(),
  });
}
