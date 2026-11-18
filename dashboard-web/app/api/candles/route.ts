// 업비트 공개 시세(인증 없음). 브라우저가 직접 부르면 CORS 에 막혀서 여기서 대신 받는다.
import { connection } from "next/server";
import { coinDirs } from "@/lib/data";

const UNITS = new Set([15, 60, 240, 1440]);

export async function GET(request: Request) {
  await connection();
  const q = new URL(request.url).searchParams;
  const unit = Number(q.get("unit") ?? 60);
  const { coin } = coinDirs(q.get("coin"));
  if (!UNITS.has(unit)) return Response.json({ error: "봉 단위는 15, 60, 240, 1440 중 하나" }, { status: 400 });
  const ep = unit === 1440 ? "days" : `minutes/${unit}`;
  try {
    const r = await fetch(`https://api.upbit.com/v1/candles/${ep}?market=KRW-${coin}&count=150`,
      { cache: "no-store", signal: AbortSignal.timeout(8000) });
    if (!r.ok) return Response.json({ error: `업비트 응답 ${r.status}` }, { status: 502 });
    const rows: any[] = await r.json();
    return Response.json(rows.reverse().map((c) => ({
      t: c.candle_date_time_kst, o: c.opening_price, h: c.high_price, l: c.low_price, c: c.trade_price,
    })));
  } catch (e) {
    return Response.json({ error: `업비트 연결 실패: ${String(e).slice(0, 120)}` }, { status: 502 });
  }
}
