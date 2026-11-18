// 하이퍼리퀴드 공개 캔들(달러). 봉 시각은 한국 시간 글자로 바꿔서 보낸다 (차트가 KST 글자를 그대로 그림).
import { connection } from "next/server";

const INTERVALS = new Set(["1m", "5m", "15m", "1h", "4h", "1d"]);
const MS: Record<string, number> = { "1m": 6e4, "5m": 3e5, "15m": 9e5, "1h": 36e5, "4h": 144e5, "1d": 864e5 };

export async function GET(request: Request) {
  await connection();
  const q = new URL(request.url).searchParams;
  const coin = /^[A-Z]{2,6}$/.test(q.get("coin") ?? "") ? q.get("coin")! : "BTC";
  const interval = INTERVALS.has(q.get("interval") ?? "") ? q.get("interval")! : "1h";
  const now = Date.now();
  try {
    const r = await fetch("https://api.hyperliquid.xyz/info", { method: "POST", headers: { "content-type": "application/json" }, cache: "no-store",
      signal: AbortSignal.timeout(8000), body: JSON.stringify({ type: "candleSnapshot", req: { coin, interval, startTime: now - MS[interval] * 420, endTime: now } }) });
    if (!r.ok) return Response.json({ error: `하이퍼리퀴드 응답 ${r.status}` }, { status: 502 });
    const rows: any[] = await r.json();
    return Response.json(rows.map((c) => ({ t: new Date(c.t + 9 * 3600_000).toISOString().slice(0, 19), o: +c.o, h: +c.h, l: +c.l, c: +c.c, v: +c.v })));
  } catch (e) {
    return Response.json({ error: `하이퍼리퀴드 연결 실패: ${String(e).slice(0, 120)}` }, { status: 502 });
  }
}
