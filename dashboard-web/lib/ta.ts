// 차트용 지표 계산 (봇 bot/indicators.py·bot/hl_signals.py 와 같은 식). null = 아직 계산 못 하는 구간.
export type Num = number | null;

export function sma(x: number[], n: number): Num[] {
  const out: Num[] = new Array(x.length).fill(null);
  let s = 0;
  for (let i = 0; i < x.length; i++) {
    s += x[i];
    if (i >= n) s -= x[i - n];
    if (i >= n - 1) out[i] = s / n;
  }
  return out;
}

/** pandas ewm(span=n, adjust=False) 와 같음 */
export function ema(x: number[], n: number): Num[] {
  const a = 2 / (n + 1);
  const out: Num[] = [];
  let y: number | null = null;
  for (const v of x) { y = y == null ? v : (1 - a) * y + a * v; out.push(y); }
  return out;
}

/** Wilder RSI: ewm(alpha=1/n, adjust=False, min_periods=n) */
export function rsi(close: number[], n = 14): Num[] {
  const out: Num[] = new Array(close.length).fill(null);
  let g: number | null = null, l: number | null = null;
  for (let i = 1; i < close.length; i++) {
    const d = close[i] - close[i - 1];
    const up = d > 0 ? d : 0, dn = d < 0 ? -d : 0;
    g = g == null ? up : g + (up - g) / n;
    l = l == null ? dn : l + (dn - l) / n;
    if (i >= n) out[i] = l === 0 ? 100 : 100 - 100 / (1 + g / l);
  }
  return out;
}

export const last = (a: Num[]) => (a.length ? a[a.length - 1] : null);
