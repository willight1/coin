"use client";
// 업비트 캔들 + AI 가 실제로 사고판 곳 + 산 가격·손절가 선. 평가금액 선(AI vs 그냥 샀다면)은 따로 작은 차트.
import {
  CandlestickSeries, ColorType, HistogramSeries, LineSeries, LineStyle, LineType, createChart, createSeriesMarkers,
  type IChartApi, type IPriceLine, type ISeriesApi, type ISeriesMarkersPluginApi, type SeriesMarker, type Time, type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

export type Candle = { t: string; o: number; h: number; l: number; c: number; v?: number };
export type Trade = Record<string, string>;
export type Equity = { t: string; equity: number; price: number };

// 차트는 UTC 로 시각을 그린다. 한국 시각 글자 그대로 보이게 KST 를 UTC 인 척 넘긴다.
const kstNaive = (s: string) => (Date.parse(s.slice(0, 19) + "Z") / 1000) as UTCTimestamp;
const kstFromIso = (iso: string) => ((Date.parse(iso) / 1000 + 9 * 3600)) as UTCTimestamp;
const fmt = (p: number) => Math.round(p).toLocaleString("ko-KR");

function useDark() {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const m = window.matchMedia("(prefers-color-scheme: dark)");
    setDark(m.matches);
    const on = (e: MediaQueryListEvent) => setDark(e.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return dark;
}

function baseOptions(dark: boolean) {
  const ink = dark ? "#a1a1a8" : "#8a8a90";
  const grid = dark ? "#232327" : "#f2f2f4";
  return {
    autoSize: true,
    layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: ink,
      fontFamily: "Pretendard Variable, sans-serif", fontSize: 12.5, attributionLogo: false },
    grid: { vertLines: { color: grid }, horzLines: { color: grid } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
    crosshair: { horzLine: { labelBackgroundColor: dark ? "#3a3a40" : "#1c1c1e" },
      vertLine: { labelBackgroundColor: dark ? "#3a3a40" : "#1c1c1e" } },
    localization: { locale: "ko-KR", priceFormatter: fmt },
    handleScale: { axisPressedMouseMove: false },
  };
}

export type Overlay = { color: string; values: (number | null)[]; dashed?: boolean };

export function PriceChart({ candles, trades, entry, stop, guard, entryLabel = "산 가격", stopLabel = "손절가", stopColor = "#f97316", fmtPrice, overlays = [], height = 300, recent, levels }: {
  candles: Candle[]; trades: Trade[]; entry?: number | null; stop?: number | null; guard?: number | null; entryLabel?: string; stopLabel?: string; stopColor?: string; levels?: number[];   // levels = 예약 주문 가격(파란 점선)
  fmtPrice?: (p: number) => string; overlays?: Overlay[]; height?: number | string; recent?: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const legend = useRef<HTMLDivElement>(null);
  const prof = useRef<HTMLDivElement>(null);
  const cs = useRef<Candle[]>([]);
  const byTime = useRef<Map<number, Candle>>(new Map());
  const lastC = useRef<Candle | null>(null);
  const dark = useDark();
  // 차트는 한 번만 만들고, 새 데이터가 와도 사용자가 확대·이동한 범위를 그대로 둔다 (매번 다시 만들면 15초마다 원래대로 튄다)
  const ref = useRef<{ chart: IChartApi; s: ISeriesApi<"Candlestick">; vol: ISeriesApi<"Histogram">; lines: IPriceLine[]; extra: ISeriesApi<"Line">[]; markers?: ISeriesMarkersPluginApi<Time>; first: boolean } | null>(null);
  useEffect(() => {
    if (!box.current) return;
    const opts = baseOptions(dark);
    if (fmtPrice) opts.localization = { ...opts.localization, priceFormatter: fmtPrice };
    const chart = createChart(box.current, opts);
    const s = chart.addSeries(CandlestickSeries, {
      upColor: "#e8365d", downColor: "#3b82f6", borderVisible: false,
      wickUpColor: "#e8365d", wickDownColor: "#3b82f6", priceLineVisible: true,
    });
    // 거래량 막대: 같은 차트 아래 20% 에 겹쳐 그린다 (가격 축과 따로)
    const vol = chart.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    ref.current = { chart, s, vol, lines: [], extra: [], first: true };
    // 매물대: 화면에 보이는 봉들의 거래량을 가격대별로 모아 오른쪽에 가로 막대로. 확대·이동·크기 변경 때 다시 그림
    const draw = () => requestAnimationFrame(() => { if (prof.current) drawProfile(prof.current, chart, s, cs.current, fmtPrice); });
    chart.timeScale().subscribeVisibleLogicalRangeChange(draw);
    // 마우스를 올린 봉(없으면 마지막 봉)의 시가·고가·저가·종가·거래량. 마우스 움직임마다 React 를 다시 그리지 않게 글자만 바꾼다
    chart.subscribeCrosshairMove((param) => {
      const c = (param.time != null && byTime.current.get(param.time as number)) || lastC.current;
      if (legend.current) legend.current.innerHTML = ohlcText(c, fmtPrice);
    });
    const ro = new ResizeObserver(draw);      // 크기는 autoSize 가 맞춘다. 여기서는 매물대만 다시 그린다
    ro.observe(box.current);
    return () => { ro.disconnect(); chart.remove(); ref.current = null; };
  }, [dark, fmtPrice]);
  useEffect(() => {
    const r = ref.current;
    if (!r || !candles.length) return;
    const { chart, s } = r;
    const range = chart.timeScale().getVisibleLogicalRange();
    const times = candles.map((c) => kstNaive(c.t));
    byTime.current = new Map(candles.map((c, i) => [times[i] as number, c]));
    lastC.current = candles[candles.length - 1];
    cs.current = candles;
    if (legend.current) legend.current.innerHTML = ohlcText(lastC.current, fmtPrice);
    s.setData(candles.map((c, i) => ({ time: times[i], open: c.o, high: c.h, low: c.l, close: c.c })));
    r.vol.setData(candles.some((c) => c.v != null)
      ? candles.map((c, i) => ({ time: times[i], value: c.v ?? 0, color: c.c >= c.o ? "rgba(232,54,93,0.35)" : "rgba(59,130,246,0.35)" })) : []);
    // 체결 시각을 그 시각이 들어 있는 봉에 붙인다
    const markers: SeriesMarker<Time>[] = [];
    for (const t of trades) {
      const ts = kstFromIso(t.time);
      if (ts < times[0]) continue;
      let at = times[0];
      for (const x of times) if (x <= ts) at = x;
      // 업비트: 매수/매도. 하이퍼리퀴드: "숏 줄임(일부 되사기)" 처럼 포지션 변화 문구 → 실제 주문이 사기인지로 판정
      const buy = t.side === "매수" || t.side === "롱" || t.side.includes("되사기") || (t.side.startsWith("롱") && !t.side.includes("팔기"));
      markers.push({ time: at, position: buy ? "belowBar" : "aboveBar", shape: buy ? "arrowUp" : "arrowDown",
        color: buy ? "#10b981" : "#f97316", text: t.side, size: 1.4 });
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    if (r.markers) r.markers.setMarkers(markers); else r.markers = createSeriesMarkers(s, markers);
    for (const ls of r.extra) chart.removeSeries(ls);
    r.extra = overlays.map((o) => {
      const ls = chart.addSeries(LineSeries, { color: o.color, lineWidth: 1, lineStyle: o.dashed ? 2 : 0, priceLineVisible: false, lastValueVisible: false });   // 이름표는 안 붙인다(차트 위 칩이 색으로 알려 준다)
      ls.setData(o.values.map((v, i) => (v == null ? null : { time: times[i], value: v })).filter(Boolean) as { time: UTCTimestamp; value: number }[]);
      return ls;
    });
    for (const pl of r.lines) s.removePriceLine(pl);
    r.lines = [];
    if (entry) r.lines.push(s.createPriceLine({ price: entry, color: "#10b981", lineWidth: 1, lineStyle: 2, title: entryLabel }));
    if (stop) r.lines.push(s.createPriceLine({ price: stop, color: stopColor, lineWidth: 1, lineStyle: 2, title: stopLabel }));
    if (guard) r.lines.push(s.createPriceLine({ price: guard, color: "#f59e0b", lineWidth: 2, lineStyle: 1, title: "" }));
    for (const lv of levels ?? []) r.lines.push(s.createPriceLine({ price: lv, color: "#0095f6", lineWidth: 1, lineStyle: 2, title: "" }));
    // 처음 그릴 때: recent 가 있으면 전체 대신 최신 recent 봉만 확대해서 보여 준다 (그 뒤로는 사용자가 움직인 범위 유지)
    if (r.first) { if (recent && candles.length > recent) chart.timeScale().setVisibleLogicalRange({ from: candles.length - recent, to: candles.length + 3 }); else chart.timeScale().fitContent(); r.first = false; }
    else if (range) chart.timeScale().setVisibleLogicalRange(range);
    requestAnimationFrame(() => { if (prof.current) drawProfile(prof.current, chart, s, candles, fmtPrice); });
  }, [candles, trades, entry, stop, guard, entryLabel, stopLabel, stopColor, overlays, dark, fmtPrice, recent, levels]);
  return (
    <div className="relative w-full" style={{ height }}>
      <div ref={box} className="h-full w-full" />
      <div ref={prof} className="pointer-events-none absolute inset-0 z-[5] overflow-hidden" />
      <div ref={legend} className="pointer-events-none absolute left-2 top-2 z-10 min-w-[150px] rounded-lg border border-line bg-card/90 px-2.5 py-1.5 text-[13px] leading-[1.55] tabular-nums text-ink shadow-card" />
    </div>);
}

const BINS = 36;
/** 매물대(가격대별 거래량). 봉 하나의 거래량을 그 봉의 저가~고가 구간에 고르게 나눠 담는다. */
function drawProfile(el: HTMLDivElement, chart: IChartApi, s: ISeriesApi<"Candlestick">, candles: Candle[], fmt?: (p: number) => string) {
  el.innerHTML = "";
  if (!candles.length || !candles.some((c) => c.v)) return;
  const lr = chart.timeScale().getVisibleLogicalRange();
  const a = Math.max(0, Math.floor(lr?.from ?? 0)), b = Math.min(candles.length - 1, Math.ceil(lr?.to ?? candles.length - 1));
  const vis = candles.slice(a, b + 1).filter((c) => c.v);
  if (vis.length < 5) return;
  const lo = Math.min(...vis.map((c) => c.l)), hi = Math.max(...vis.map((c) => c.h)), step = (hi - lo) / BINS || 1;
  const vol = new Array(BINS).fill(0);
  for (const c of vis) {
    const i0 = Math.min(BINS - 1, Math.floor((c.l - lo) / step)), i1 = Math.min(BINS - 1, Math.floor((c.h - lo) / step));
    for (let i = i0; i <= i1; i++) vol[i] += (c.v ?? 0) / (i1 - i0 + 1);
  }
  const total = vol.reduce((x, y) => x + y, 0), max = Math.max(...vol);
  const right = chart.priceScale("right").width(), plotW = el.clientWidth - right, maxW = plotW * 0.22;
  const last = candles[candles.length - 1].c;
  const top3 = [...vol.keys()].sort((x, y) => vol[y] - vol[x]).slice(0, 3);
  const f = fmt ?? ((p: number) => p.toLocaleString("en-US", { maximumFractionDigits: 2 }));
  let html = "";
  vol.forEach((v, i) => {
    const yTop = s.priceToCoordinate(lo + (i + 1) * step), yBot = s.priceToCoordinate(lo + i * step);
    if (yTop == null || yBot == null) return;
    const h = Math.max(1, yBot - yTop - 1), w = (v / max) * maxW, mid = lo + (i + 0.5) * step;
    const poc = i === top3[0], above = mid > last;
    const color = poc ? "rgba(245,158,11,0.55)" : above ? "rgba(59,130,246,0.22)" : "rgba(232,54,93,0.22)";
    html += `<div style="position:absolute;top:${yTop}px;height:${h}px;width:${w}px;right:${right}px;background:${color};border-radius:2px 0 0 2px"></div>`;
    if (top3.includes(i) && h >= 6) {
      html += `<div style="position:absolute;top:${yTop + h / 2 - 7}px;right:${right + w + 4}px;font-size:10.5px;line-height:14px;white-space:nowrap;opacity:.85">`
        + `${poc ? "★ " : ""}${f(lo + i * step)}~${f(lo + (i + 1) * step)} · ${((v / total) * 100).toFixed(1)}%</div>`;
    }
  });
  el.innerHTML = html;
}

function ohlcText(c: Candle | null, fmt?: (p: number) => string): string {
  if (!c) return "";
  const f = fmt ?? ((p: number) => p.toLocaleString("en-US", { maximumFractionDigits: 2 }));
  const chg = c.o ? (c.c / c.o - 1) * 100 : 0;
  const col = chg >= 0 ? "#e8365d" : "#3b82f6";
  const row = (k: string, v: string, style = "") => `<div style="display:flex;justify-content:space-between;gap:14px"><span style="opacity:.6">${k}</span><span style="${style}">${v}</span></div>`;
  return `<div style="opacity:.6;margin-bottom:2px">${c.t.slice(5, 16).replace("T", " ")}</div>`
    + row("시가", f(c.o)) + row("고가", f(c.h)) + row("저가", f(c.l))
    + row("종가", `${f(c.c)} (${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%)`, `color:${col};font-weight:700`)
    + (c.v != null ? row("거래량", c.v.toLocaleString("en-US", { maximumFractionDigits: 2 })) : "");
}

/** 0~100 보조지표(RSI) 작은 패널. 30·50·70 기준선. 확대 범위 유지. */
export function OscChart({ candles, values, recent }: { candles: Candle[]; values: (number | null)[]; recent?: number }) {
  const box = useRef<HTMLDivElement>(null);
  const dark = useDark();
  const ref = useRef<{ chart: IChartApi; s: ISeriesApi<"Line">; first: boolean } | null>(null);
  useEffect(() => {
    if (!box.current) return;
    const chart = createChart(box.current, { ...baseOptions(dark),
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.1, bottom: 0.1 } }, localization: { locale: "ko-KR", priceFormatter: (p: number) => p.toFixed(0) } });
    const s = chart.addSeries(LineSeries, { color: "#8b5cf6", lineWidth: 2, priceLineVisible: false });
    for (const lv of [30, 50, 70]) s.createPriceLine({ price: lv, color: lv === 50 ? (dark ? "#555" : "#bbb") : "#f97316", lineWidth: 1, lineStyle: 2, title: String(lv) });
    ref.current = { chart, s, first: true };
    return () => { chart.remove(); ref.current = null; };
  }, [dark]);
  useEffect(() => {
    const r = ref.current;
    if (!r || !candles.length) return;
    const range = r.chart.timeScale().getVisibleLogicalRange();
    const times = candles.map((c) => kstNaive(c.t));
    const pts = values.map((v, i) => (v == null ? null : { time: times[i], value: v })).filter(Boolean) as { time: UTCTimestamp; value: number }[];
    r.s.setData(pts);
    if (r.first) { if (recent && pts.length > recent) r.chart.timeScale().setVisibleLogicalRange({ from: pts.length - recent, to: pts.length + 3 }); else r.chart.timeScale().fitContent(); r.first = false; }
    else if (range) r.chart.timeScale().setVisibleLogicalRange(range);
  }, [candles, values, dark, recent]);   // 다크 모드가 늦게 잡히면 위 effect 가 차트를 새로 만든다 → 데이터도 다시 넣어야 한다
  return <div ref={box} className="h-[120px] w-full" />;
}


// 같은 시각이 두 번 오면 차트가 거부한다: 먼저 온 줄만 남긴다
function uniqByTime<T extends Record<string, any>>(rows: T[]): T[] {
  const seen = new Set<number>();
  return rows.filter((r) => { const k = kstFromIso(r.t); if (seen.has(k)) return false; seen.add(k); return true; });
}
// 5초마다 새 배열이 와도 내용이 같으면(줄 수·처음·마지막 줄) 차트를 다시 만들지 않게 effect 의존값으로 쓴다
const rowsKey = (rows: Record<string, any>[]) => `${rows.length}|${rows[0]?.t}|${JSON.stringify(rows[rows.length - 1])}`;

/** 평가금액 선: 분홍 = 봇, 회색 점선 = 처음 시점에 그냥 샀다면 (가격 비례). */
export function EquityChart({ rows }: { rows: Equity[] }) {
  const box = useRef<HTMLDivElement>(null);
  const dark = useDark();
  useEffect(() => {
    if (!box.current || rows.length < 2) return;
    const chart = createChart(box.current, { ...baseOptions(dark), handleScroll: false, handleScale: false,
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.15, bottom: 0.1 } } });
    const ai = chart.addSeries(LineSeries, { color: "#d62976", lineWidth: 2, title: "AI", priceLineVisible: false });
    const hold = chart.addSeries(LineSeries, { color: dark ? "#6b6b72" : "#b0b0b6", lineWidth: 1, lineStyle: 2,
      title: "그냥 샀다면", priceLineVisible: false });
    const uniq = uniqByTime(rows);
    const p0 = uniq[0].price, e0 = uniq[0].equity;
    ai.setData(uniq.map((r) => ({ time: kstFromIso(r.t), value: r.equity })));
    hold.setData(uniq.map((r) => ({ time: kstFromIso(r.t), value: p0 ? e0 * (r.price / p0) : e0 })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [rowsKey(rows), dark]);      // eslint-disable-line react-hooks/exhaustive-deps
  return <div ref={box} className="h-[160px] w-full" />;
}

/** 값 하나짜리 선 (하이퍼리퀴드 평가금액). */
export function SimpleLine({ rows, color = "#d62976" }: { rows: { t: string; v: number }[]; color?: string }) {
  const box = useRef<HTMLDivElement>(null);
  const dark = useDark();
  useEffect(() => {
    if (!box.current || rows.length < 2) return;
    const chart = createChart(box.current, { ...baseOptions(dark), handleScroll: false, handleScale: false,
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.15, bottom: 0.1 } } });
    const line = chart.addSeries(LineSeries, { color, lineWidth: 2, priceLineVisible: false });
    line.setData(uniqByTime(rows).map((r) => ({ time: kstFromIso(r.t), value: r.v })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [rowsKey(rows), dark, color]);      // eslint-disable-line react-hooks/exhaustive-deps
  return <div ref={box} className="h-[160px] w-full" />;
}

// 여러 선 (−1~+1 같은 한 축). 4시간마다 바뀌는 값이라 계단 선으로 그린다. 범례가 값 표시를 겸한다: 마우스를 올리면 그 시점 값, 아니면 마지막 값.
// 색은 dataviz 검증(밝은·어두운 화면 각각 통과)한 것만 넘긴다. lines 는 모듈 상수로 넘길 것(매 렌더 새 배열이면 차트를 다시 만든다).
export type LineDef = { key: string; label: string; light: string; dark: string; width?: 1 | 2 | 3; dashed?: boolean };
export function MultiLine({ rows, lines }: { rows: Record<string, any>[]; lines: LineDef[] }) {
  const box = useRef<HTMLDivElement>(null);
  const dark = useDark();
  const [hover, setHover] = useState<Record<string, number | undefined> | null>(null);
  useEffect(() => {
    if (!box.current || rows.length < 2) return;
    const chart = createChart(box.current, { ...baseOptions(dark), handleScroll: false, handleScale: false,
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.08 } } });
    const ss = lines.map((l) => {
      const s = chart.addSeries(LineSeries, { color: dark ? l.dark : l.light, lineWidth: l.width ?? 2, lineStyle: l.dashed ? LineStyle.Dashed : LineStyle.Solid,
        lineType: LineType.WithSteps, priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "price", precision: 2, minMove: 0.01 },
        autoscaleInfoProvider: () => ({ priceRange: { minValue: -1, maxValue: 1 } }) });
      s.setData(uniqByTime(rows.filter((r) => r[l.key] != null)).map((r) => ({ time: kstFromIso(r.t), value: +r[l.key] })));
      return s;
    });
    ss[0].createPriceLine({ price: 0, color: dark ? "#6b6b72" : "#a1a1a8", lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: "" });
    chart.subscribeCrosshairMove((p) => setHover(p.time ? Object.fromEntries(lines.map((l, i) => [l.key, (p.seriesData.get(ss[i]) as { value?: number } | undefined)?.value])) : null));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [rowsKey(rows), dark, lines]);      // eslint-disable-line react-hooks/exhaustive-deps
  const lastOf = (k: string) => { for (let i = rows.length - 1; i >= 0; i--) if (rows[i][k] != null) return +rows[i][k]; return undefined; };
  const f = (v?: number) => (v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)}`);
  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-[13px]">
        {lines.map((l) => (
          <span key={l.key} className="inline-flex items-center gap-1.5">
            <span className="inline-block w-4" style={{ borderTop: `${l.width ?? 2}px ${l.dashed ? "dashed" : "solid"} ${dark ? l.dark : l.light}` }} />
            <span className="text-sub">{l.label}</span><b className="tabular-nums">{f(hover ? hover[l.key] : lastOf(l.key))}</b>
          </span>))}
        <span className="text-sub">{hover ? "· 가리킨 시점" : "· 마지막 값"}</span>
      </div>
      <div ref={box} className="h-[240px] w-full" />
    </div>);
}
