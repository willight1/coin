// 달러→원 환율: 업비트 KRW-USDT 현재가 (실제로 업비트에서 바꾸면 받는 값, 김치 프리미엄 포함). 60초 캐시.
import "server-only";

let cache: { rate: number; at: number } | null = null;

export async function usdKrw(): Promise<number | null> {
  if (cache && Date.now() - cache.at < 60_000) return cache.rate;
  try {
    const r = await fetch("https://api.upbit.com/v1/ticker?markets=KRW-USDT", { cache: "no-store", signal: AbortSignal.timeout(5000) });
    const rate = (await r.json())[0]?.trade_price;
    if (rate > 0) { cache = { rate, at: Date.now() }; return rate; }
  } catch { /* 네트워크 끊김 — 마지막 값 유지 */ }
  return cache?.rate ?? null;
}
