"use client";
// 하이퍼리퀴드 롱·숏 추세 봇 화면 — 차트 중심 배치: 가운데 큰 실시간 차트(지표 겹침, 확대 유지, 전체화면),
// 오른쪽에 포지션·투표·체결, 아래에 다른 코인 요약·평가금액 선·안전장치.
import { ArrowsInIcon, ArrowsOutIcon, WarningCircleIcon } from "@phosphor-icons/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { OscChart, PriceChart, SimpleLine, type Candle, type Overlay } from "@/components/PriceChart";
import { ago, sgn } from "@/lib/ko";
import { ema, last, rsi, sma } from "@/lib/ta";

type Any = Record<string, any>;
export type HlData = { state: Any; news?: Any[]; exposure?: { t: string; coin: string; v: number }[]; sentiment?: Record<string, Record<string, string>>; sentimentPrev?: Record<string, Record<string, string>>; sentimentRows?: number; whales?: Any; whaleAlerts?: Any[]; ai?: Any; running: boolean; trades: Record<string, string>[]; equity: { t: string; v: number }[]; krw: number | null; paper?: boolean; inst?: string; now?: string; chat?: Chat[] };
type Chat = { t: number; who: string; text: string };

export const COIN_KO: Record<string, string> = { BTC: "비트코인", ETH: "이더리움" };
// 고래 청산 지도에서 지금 가격 ±10% 안에 청산가가 있는 금액: dn = 아래쪽 롱, up = 위쪽 숏
const near10 = (bins: Any[]) => ({ dn: bins.filter((b) => b.pct >= -10 && b.pct < 0).reduce((a, b) => a + b.long, 0) as number,
  up: bins.filter((b) => b.pct >= 0 && b.pct < 10).reduce((a, b) => a + b.short, 0) as number });
export const usd = (x?: number | null, d = 0) => (x == null ? "-" : `$${x.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}`);
export const tone = (x: number) => (!x ? "" : x > 0 ? "text-up" : "text-down");   // 국내식: 이익·롱 빨강, 손실·숏 파랑
export const fmtPx = (v: number) => (v >= 1000 ? v.toLocaleString("en-US", { maximumFractionDigits: 0 }) : v.toFixed(2));
export const Card = ({ title, children, right, className = "" }: { title?: string; children: React.ReactNode; right?: React.ReactNode; className?: string }) => (
  <section className={`rounded-[20px] border border-line bg-card p-4 shadow-card ${className}`}>
    {(title || right) && <div className="mb-3 flex items-center justify-between gap-2"><h2 className="text-[14.5px] font-bold">{title}</h2>{right}</div>}{children}
  </section>
);
// 용어 설명 — 카드마다 '이 숫자들이 뭐예요?'를 펼쳐 둔다(사용자 요청 2026-11-17). 접으면 이 기기에 기억한다
export function Help({ id, items, title = "이 숫자들이 뭐예요?", wide }: { id: string; items: [string, React.ReactNode][]; title?: string; wide?: boolean }) {
  const [open, setOpen] = useState(true);
  useEffect(() => {                                                   // 휴대폰 폭에서는 처음에 접어 둔다(차트가 한참 아래로 밀려서). 한 번 누르면 그 선택을 기억
    let v: string | null = null; try { v = localStorage.getItem(`help:${id}`); } catch { /* 저장소 없음 */ }
    if (v === "0" || (v == null && window.innerWidth < 768)) setOpen(false);
  }, [id]);
  const toggle = () => { const o = !open; setOpen(o); try { localStorage.setItem(`help:${id}`, o ? "1" : "0"); } catch { /* */ } };
  return (
    <div className="mt-3 rounded-xl bg-chip px-3 py-2 text-[13px] leading-relaxed" onClick={(e) => e.stopPropagation()}>
      <button onClick={toggle} aria-expanded={open} className="press flex w-full items-center justify-between gap-2 text-left font-semibold text-accent">
        <span>{title}</span><span className="text-[12px] font-normal text-sub">{open ? "접기" : "펼치기"}</span></button>
      {open && <dl className={`mt-1.5 grid gap-x-6 gap-y-1.5 ${wide ? "md:grid-cols-2 xl:grid-cols-3" : ""}`}>
        {items.map(([k, v]) => <div key={k} className="min-w-0"><dt className="font-semibold">{k}</dt><dd className="text-sub">{v}</dd></div>)}</dl>}
    </div>);
}
export function Seg<T extends string>({ items, value, onChange }: { items: [T, string][]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="flex rounded-full bg-chip p-0.5">
      {items.map(([v, t]) => <button key={v} onClick={() => onChange(v)}
        className={`press rounded-full px-3 py-1 text-[13.5px] ${value === v ? "bg-card font-semibold text-accent shadow-card" : "text-sub"}`}>{t}</button>)}
    </div>
  );
}
const VOTE_WHY: Record<string, string> = {
  "일봉 이평50": "하루 종가가 50일 평균선 위·아래", "일봉 이평50+ADX25": "같은 것인데 추세가 뚜렷할 때만", "일봉 RSI추세": "하루 기세가 70 넘으면 롱, 30 밑이면 숏",
  "일봉 이평100": "하루 종가가 100일 평균선 위·아래", "4h 이평200": "4시간봉이 200봉 평균선 위·아래", "4h 정배열": "짧은 평균선들이 차례로 위면 롱, 아래면 숏",
  "4h RSI추세": "4시간 기세가 70 넘으면 롱, 30 밑이면 숏", "4h 모멘텀90": "15일 전보다 높으면 롱, 낮으면 숏",
  "일봉 전체 계정 롱숏 z 역방향(>1 숏/<-1 롱)": "바이낸스 개인 계정이 한쪽으로 몰리면 그 반대로 (몰림이 없으면 표 없음)",
  "일봉 이평50 + 미결제약정 과열(z>2) 시 절반": "50일 평균선 방향대로, 단 선물 미결제약정이 과열이면 절반만",
  "일봉 이평50 + 옵션 변동성 급등(z>2) 시 절반": "50일 평균선 방향대로, 단 옵션 시장이 큰 출렁임을 예상하면 절반만",
};
// 메인 노출 = 분석가(추세·파생·거시·심리)별 평균의 평균 (bot/hl_signals.combine). 봇이 아직 desk 를 안 적었으면 전체 평균
export const deskExpo = (s: Any, c: string) => {
  const d = Object.values(s.desk?.[c] ?? {}) as number[], v = Object.values(s.votes?.[c] ?? {}) as number[];
  const xs = d.length ? d : v;
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0;
};

// 내 돈 — 맨 위 헤더 오른쪽(스크롤해도 붙어 있음). 핵심 4개만, 나머지는 아래 '봇 설정' 탭
export function HlMoney({ d }: { d: HlData }) {
  const s = d.state; const krw = d.krw;
  const eq = s.equity_usd ?? 0, start = s.start_usd ?? 0;
  const ret = start ? (eq / start - 1) * 100 : 0;
  const totalUpnl = (Object.values(s.pos ?? {}) as Any[]).reduce((a: number, p: Any) => a + (p.upnl ?? 0), 0);
  const kr = (x: number) => (krw ? `${Math.round(x * krw).toLocaleString("ko-KR")}원` : "");
  const sg = (x: number) => (x > 0 ? "+" : x < 0 ? "−" : "");
  return (
    <div className="flex flex-wrap items-end gap-x-6 gap-y-1">
      {([[<>{usd(eq, 2)} <span className="text-[13.5px] font-normal text-sub">{kr(eq)}</span></>, "평가금액"],
        [<span className={tone(ret)}>{sg(ret)}{Math.abs(ret).toFixed(2)}%</span>, `시작 ${usd(start)} 대비`],
        [<span className={tone(totalUpnl)}>{sg(totalUpnl)}{usd(Math.abs(totalUpnl), 2)} <span className="text-[13.5px] font-normal">{sg(totalUpnl)}{kr(Math.abs(totalUpnl))}</span></span>, "미실현 손익"]] as [React.ReactNode, string][])
        .map(([v, k], i) => <div key={i}><div className="text-[18px] font-bold leading-tight tabular-nums">{v}</div><div className="text-[12.5px] text-sub">{k}</div></div>)}
    </div>);
}

// 투표 상세 — 마우스를 올리거나(휴대폰은 눌러서) 규칙별 표를 본다
export function VotePop({ votes, side = "left", children }: { votes?: Record<string, number>; side?: "left" | "right"; children: React.ReactNode }) {
  const e = Object.entries(votes ?? {});
  if (!e.length) return <>{children}</>;
  const order = [...e].sort((a, b) => b[1] - a[1]);
  return (
    <span className="group relative inline-block cursor-help outline-none" tabIndex={0}>
      {children}
      <span className={`invisible absolute top-full z-40 mt-1.5 w-[380px] max-w-[85vw] rounded-xl border border-line bg-card p-3 text-left opacity-0 shadow-card transition-opacity group-hover:visible group-hover:opacity-100 group-focus:visible group-focus:opacity-100 ${side === "left" ? "left-0" : "right-0"}`}>
        <span className="mb-1.5 block text-[13px] font-bold">규칙별 표 · 롱 {e.filter(([, v]) => v > 0).length} · 숏 {e.filter(([, v]) => v < 0).length}{e.some(([, v]) => v === 0) ? ` · 0표 ${e.filter(([, v]) => v === 0).length}` : ""}</span>
        <span className="block max-h-[360px] space-y-0.5 overflow-y-auto">
          {order.map(([k, v]) => (
            <span key={k} className="flex items-start gap-2 text-[13px] leading-snug">
              <span className={`w-7 shrink-0 text-center font-bold ${v > 0 ? "text-up" : v < 0 ? "text-down" : "text-sub"}`}>{v > 0 ? (v < 1 ? "롱½" : "롱") : v < 0 ? "숏" : "–"}</span>
              <span className="text-sub">{VOTE_WHY[k] ?? k}</span>
            </span>))}
        </span>
      </span>
    </span>);
}

type Ind = { id: string; title: string; color: string; calc: (c: number[]) => (number | null)[]; bot: string[]; dashed?: boolean };
const IND: Ind[] = [
  { id: "sma50", title: "이평 50", color: "#f59e0b", calc: (c) => sma(c, 50), bot: ["1d"] },
  { id: "sma100", title: "이평 100", color: "#d62976", calc: (c) => sma(c, 100), bot: ["1d"] },
  { id: "sma200", title: "이평 200", color: "#0ea5e9", calc: (c) => sma(c, 200), bot: ["4h"] },
  { id: "ema20", title: "EMA 20", color: "#22c55e", calc: (c) => ema(c, 20), bot: ["4h"] },
  { id: "ema50", title: "EMA 50", color: "#84cc16", calc: (c) => ema(c, 50), bot: ["4h"] },
  { id: "ema100", title: "EMA 100", color: "#14b8a6", calc: (c) => ema(c, 100), bot: ["4h"] },
  { id: "mom90", title: "90봉 전 가격", color: "#a855f7", calc: (c) => c.map((_, i) => (i >= 90 ? c[i - 90] : null)), bot: ["4h"], dashed: true },
];

// 주소를 ms 마다 다시 읽는다. 주소가 비면 쉰다(Dashboard 가 안 보는 탭의 API 를 안 부르게)
export function useFetch<T>(url: string, ms: number) {
  const [data, setData] = useState<T | null>(null); const [error, setError] = useState("");
  useEffect(() => {
    if (!url) return;
    let alive = true;
    const load = async () => {
      try {
        const r = await fetch(url, { cache: "no-store" }); const j = await r.json();
        if (!alive) return;
        if (!r.ok) throw new Error(j.error ?? `응답 ${r.status}`);
        setData(j); setError("");
      } catch (e) { if (alive) setError(String((e as Error).message ?? e)); }
    };
    load(); const id = setInterval(load, ms); return () => { alive = false; clearInterval(id); };
  }, [url, ms]);
  return { data, error };
}

const RECENT = 120;   // 차트를 처음 열 때 보여 주는 최신 봉 수 (1분봉이면 최근 2시간). 봉 단위를 바꿔도 같은 개수로 유지된다
function LiveChart({ d, coin, setCoin, coins, full, setFull, won }: { d: HlData; coin: string; setCoin: (c: string) => void; coins: string[]; full: boolean; setFull: (f: boolean) => void; won: (x?: number | null) => string }) {
  const [interval, setInterval_] = useState<"1m" | "5m" | "15m" | "1h" | "4h" | "1d">("1m");   // 처음엔 1분봉, 최신 RECENT 봉만 확대 (사용자 요청 2026-11-16)
  const [on, setOn] = useState<Record<string, boolean>>(() => Object.fromEntries(IND.map((i) => [i.id, true])));
  const [showRsi, setShowRsi] = useState(true);
  const { data: candles, error } = useFetch<Candle[]>(`/api/hl/candles?coin=${coin}&interval=${interval}`, interval === "1m" || interval === "5m" ? 5000 : 15000);
  const closes = useMemo(() => candles?.map((c) => c.c) ?? [], [candles]);
  const lines = useMemo(() => IND.map((i) => ({ ...i, values: closes.length ? i.calc(closes) : [] })), [closes]);
  const r = useMemo(() => (closes.length ? rsi(closes, 14) : []), [closes]);
  const overlays: Overlay[] = useMemo(() => lines.filter((l) => on[l.id]).map((l) => ({ color: l.color, values: l.values, dashed: l.dashed })), [lines, on]);
  const tradesKey = d.trades.map((t) => `${t.time}${t.coin}${t.side}`).join();      // 5초마다 새 배열이 와도 체결이 같으면 차트를 다시 그리지 않게
  const trades = useMemo(() => d.trades.filter((t) => t.coin === coin), [tradesKey, coin]);      // eslint-disable-line react-hooks/exhaustive-deps
  const resvKey = ((d.state.resv ?? []) as Any[]).filter((x) => x.coin === coin).map((x) => x.px).join(",");     // 예약 주문 가격 → 차트의 파란 점선
  const levels = useMemo(() => (resvKey ? resvKey.split(",").map(Number) : []), [resvKey]);
  const p = d.state.pos?.[coin]; const px = d.state.marks?.[coin]; const rsiNow = last(r);
  const upnl = p ? (p.upnl ?? 0) : 0;
  useEffect(() => {
    if (!full) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFull(false); };
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  }, [full, setFull]);
  return (
    <section className={full ? "fixed inset-0 z-50 flex flex-col bg-bg p-3" : "rounded-[20px] border border-line bg-card p-4 shadow-card"}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Seg items={coins.map((c) => [c, COIN_KO[c] ?? c] as [string, string])} value={coin} onChange={setCoin} />
        <Seg items={[["1m", "1분"], ["5m", "5분"], ["15m", "15분"], ["1h", "1시간"], ["4h", "4시간"], ["1d", "하루"]]} value={interval} onChange={setInterval_} />
        <div className="ml-auto flex flex-wrap items-center gap-2 text-[14px]">
          {full && (() => { const eq = d.state.equity_usd ?? 0, st = d.state.start_usd ?? 0, ret = st ? (eq / st - 1) * 100 : 0;
            const tot = (Object.values(d.state.pos ?? {}) as Any[]).reduce((a: number, q: Any) => a + (q.upnl ?? 0), 0);
            return <span className="rounded-full bg-chip px-2.5 py-1 tabular-nums">내 돈 <b>{usd(eq, 2)}</b>{won(eq)}
              <span className={`ml-1.5 ${tone(ret)}`}>{ret > 0 ? "+" : ""}{ret.toFixed(2)}%</span>
              <span className="ml-1.5 text-sub">미실현</span> <span className={tone(tot)}>{tot > 0 ? "+" : ""}{usd(tot, 2)}</span></span>; })()}
          {px && <span className="font-bold tabular-nums">${fmtPx(px)}</span>}
          {p && <span className={`rounded-full px-2 py-[2px] text-[13px] font-semibold ${p.size > 0 ? "bg-[#ffe4ec] text-[#be185d] dark:bg-[#3b0d22] dark:text-[#f472b6]" : "bg-[#dbeafe] text-[#1d4ed8] dark:bg-[#0f1f3d] dark:text-[#60a5fa]"}`}>
            {p.size > 0 ? "롱" : "숏"} {Math.abs(p.size)}개 <span className={tone(upnl)}>{upnl > 0 ? "+" : ""}{usd(upnl, 2)}</span></span>}
          <button onClick={() => setFull(!full)} className="press rounded-full border border-line bg-card p-1.5" title={full ? "작게 보기 (Esc)" : "전체화면"}>
            {full ? <ArrowsInIcon size={14} weight="bold" /> : <ArrowsOutIcon size={14} weight="bold" />}</button>
        </div>
      </div>
      <div className="mb-2 flex flex-wrap gap-1.5">
        {lines.map((l) => { const v = last(l.values); const used = l.bot.includes(interval); return (
          <button key={l.id} onClick={() => setOn((o) => ({ ...o, [l.id]: !o[l.id] }))} title={used ? "봇이 이 봉에서 실제로 보는 선" : "참고용"}
            className={`press flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[13px] ${on[l.id] ? "border-line bg-card" : "border-transparent bg-chip text-sub line-through"}`}>
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: l.color }} />{l.title}{used && " ●"}
            {v != null && on[l.id] && <span className="tabular-nums text-sub">{fmtPx(v)}</span>}</button>); })}
        <button onClick={() => setShowRsi((x) => !x)} className={`press rounded-full border px-2.5 py-1 text-[13px] ${showRsi ? "border-line bg-card" : "border-transparent bg-chip text-sub line-through"}`}>
          RSI 14{rsiNow != null && showRsi && <span className={`ml-1.5 tabular-nums ${rsiNow >= 70 ? "text-up" : rsiNow <= 30 ? "text-down" : "text-sub"}`}>{rsiNow.toFixed(1)}</span>}</button>
      </div>
      <div className={full ? "min-h-0 flex-1" : ""}>
        {error ? <p className="py-16 text-center text-[14.5px] text-sub">가격을 불러오지 못했어요: {error}</p>
          : !candles ? <div className="h-[420px] animate-pulse rounded-2xl bg-chip" />
          : <>
            <PriceChart recent={RECENT} candles={candles} trades={trades} overlays={overlays} fmtPrice={fmtPx} height={full ? (showRsi ? "calc(100dvh - 270px)" : "calc(100dvh - 140px)") : "clamp(320px, 55dvh, 600px)"}
              entry={p?.entry} entryLabel="" stop={p?.liq ?? null} stopLabel="" stopColor="#e5484d"
              guard={d.state.guard?.[coin]?.cut >= 1 ? d.state.guard?.[coin]?.line : null} levels={levels} />
            {showRsi && <OscChart recent={RECENT} candles={candles} values={r} />}
          </>}
      </div>
      <p className="mt-1 text-[12.5px] leading-relaxed text-sub">● = 이 봉에서 봇이 투표에 쓰는 선 · 화살표 = 체결 · 초록 점선 = 진입가 · <b className="text-warn">주황 실선 = 이익 보호 정리선</b>(4시간 종가가 넘으면 75% 정리) · 빨간 점선 = 강제청산가 · 파란 점선 = 예약 주문 가격 · 15초마다 갱신, 확대한 범위는 유지돼요.</p>
      {!full && <Help id="chart" title="차트의 선과 숫자가 뭐예요?" wide items={[
        ["봉(캔들)", "막대 하나가 고른 시간(1분·4시간 등) 동안의 가격이에요. 빨강은 그동안 오른 봉, 파랑은 내린 봉이에요."],
        ["이평 50 · 100 · 200", "최근 50·100·200개 봉 종가의 평균을 이은 선이에요. 가격이 선 위에 있으면 오르는 흐름, 아래면 내리는 흐름으로 봐요. 봇의 핵심 규칙이 이 선이에요."],
        ["EMA 20 · 50 · 100", "최근 가격에 더 무게를 준 평균선이에요. 짧은 선(20)이 긴 선(100)보다 차례로 위에 있으면 상승 흐름, 차례로 아래면 하락 흐름이에요."],
        ["90봉 전 가격", "90봉 전(4시간봉이면 15일 전) 가격이에요. 지금 가격이 이 선보다 높으면 그때보다 올랐다는 뜻이에요."],
        ["RSI 14", "최근 14봉 동안 오른 힘이 얼마나 셌는지 0~100으로 나타내요. 70 이상이면 많이 오른 상태(과열), 30 이하면 많이 내린 상태(과매도)예요."],
        ["이름 옆 숫자", "그 선의 지금 값(가격)이에요. 이름을 누르면 선을 끄고 켤 수 있어요."],
      ]} />}
    </section>
  );
}

// 다음 재조정 예상 — bot/hl_live.rebalance 와 같은 계산(지금 투표·배수·가격 기준). 투표는 4시간 종가에서 바뀔 수 있다.
const BAND = 0.30;
const MMR: Record<string, number> = { BTC: 0.0125, ETH: 0.02 };   // 유지증거금률 = 1 / (2 × 최대 배수). BTC 40배, ETH 25배
const REBAL_KST = [1, 5, 9, 13, 17, 21];
export function nextRebal() {
  const now = new Date(), k = (now.getUTCHours() + 9) % 24, n = REBAL_KST.find((x) => x > k) ?? REBAL_KST[0];
  const mins = (((n - k + 24) % 24 || 24) * 60 - now.getUTCMinutes());
  return { at: `${n}시`, left: mins >= 60 ? `${Math.floor(mins / 60)}시간 ${mins % 60}분 후` : `${mins}분 후` };
}
function predict(s: Any, c: string, n: number): { text: string; hot: boolean } | null {
  const mark = s.marks?.[c], vs = Object.values(s.votes?.[c] ?? {}) as number[];
  if (!mark || !vs.length) return null;
  const g = s.guard?.[c] ?? {}, cur = s.pos?.[c]?.size ?? 0, side = Math.sign(cur);
  if (side && g.line && g.cut >= 1 && (side < 0 ? mark >= g.line : mark <= g.line)) return { text: "지금 가격으로 마감하면 이익 보호 발동 → 75% 정리", hot: true };
  const hold: string[] = s.hold ?? [];                                                   // 거래를 멈춘 코인(/hl_hold): 그 몫은 나머지 코인이 쓴다
  if (s.scalp?.coin === c) return { text: "분봉 단타가 1분마다 판단 (4시간 재조정 없음)", hot: false };
  if ((s.manual ?? []).includes(c)) return { text: hold.includes(c) ? "매매 없음 (거래를 멈춘 코인)" : "매매 없음 (직접 주문한 코인)", hot: false };
  const e0 = deskExpo(s, c), half = s.half?.[c] != null && Math.sign(e0) === s.half[c];   // 절반 모드(HL_HALF)
  const share = (s.equity_usd ?? 0) / Math.max(1, n - hold.length), e = e0 * (g.cut ?? 1) * (half ? (s.half_k ?? 0.5) : 1);
  const target = share * e * (s.lev?.[c] ?? 0) / mark, delta = target - cur;
  if (Math.abs(delta) * mark < Math.max(10, share * 0.003)) return { text: "매매 없음", hot: false };
  if (target && cur && Math.sign(target) === side && Math.abs(delta) < BAND * Math.abs(target))
    return { text: `매매 없음 (목표와 차이 ${Math.round(Math.abs(delta) / Math.abs(target) * 100)}% < 완충 30%)`, hot: false };
  const amt = `${Math.abs(delta).toPrecision(3)}개`;
  if (!target) return { text: "전부 정리", hot: true };
  if (!cur) return { text: `${target > 0 ? "롱" : "숏"} 진입 ${amt}`, hot: true };
  if (Math.sign(target) !== side) return { text: `${target > 0 ? "롱" : "숏"}으로 뒤집기`, hot: true };
  return { text: `${Math.abs(target) < Math.abs(cur) ? "줄임" : "늘림"} ${amt}`, hot: true };
}
// 같은 계좌(교차 증거금): 두 코인이 같은 비율 x 로 움직일 때 평가금액 E + A·x 가 유지증거금 M·(1+x) 에 닿는 x
function jointLiq(s: Any): number | null {
  const ps = Object.entries(s.pos ?? {}) as [string, Any][];
  if (!ps.length) return null;
  let A = 0, M = 0;
  for (const [c, p] of ps) { const m = s.marks?.[c] ?? p.entry; A += p.size * m; M += (MMR[c] ?? 0.02) * Math.abs(p.size) * m; }
  const x = (M - (s.equity_usd ?? 0)) / (A - M);
  return Number.isFinite(x) ? x : null;
}

function CoinMini({ s, c, n, won, active, onClick }: { s: Any; c: string; n: number; won: (x?: number | null) => string; active: boolean; onClick: () => void }) {
  const p = s.pos?.[c], mark = s.marks?.[c], votes: Record<string, number> = s.votes?.[c] ?? {}, g = s.guard?.[c] ?? {};
  const vals = Object.values(votes), L = vals.filter((v) => v > 0).length, S = vals.filter((v) => v < 0).length;
  const expo = deskExpo(s, c);
  const upnl = p ? (p.upnl ?? 0) : 0;      // 가상 포지션의 미실현 손익은 /api/hl 이 채워 준다
  const gap = (x?: number | null) => (x && mark ? `${x > mark ? "+" : "−"}${Math.abs((x / mark - 1) * 100).toFixed(1)}%` : "");
  const pr = predict(s, c, n);
  const Stat = ({ k, v, sub, cls = "" }: { k: string; v: React.ReactNode; sub?: string; cls?: string }) => (
    <div className="min-w-0"><div className="text-[12.5px] text-sub">{k}</div><div className={`text-[14.5px] font-semibold tabular-nums ${cls}`}>{v}</div>{sub && <div className="text-[12px] text-sub tabular-nums">{sub}</div>}</div>);
  return (
    <div onClick={onClick} role="button" className={`press flex h-full flex-col rounded-[20px] border bg-card p-4 text-left shadow-card ${active ? "border-ink" : "border-line"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[16.5px] font-bold">{COIN_KO[c] ?? c}</span>
        <span className="text-[14.5px] tabular-nums text-sub">{mark ? `$${fmtPx(mark)}` : ""}</span>
        {p && <span className={`rounded-full px-2 py-[2px] text-[13px] font-semibold ${p.size > 0 ? "bg-[#ffe4ec] text-[#be185d] dark:bg-[#3b0d22] dark:text-[#f472b6]" : "bg-[#dbeafe] text-[#1d4ed8] dark:bg-[#0f1f3d] dark:text-[#60a5fa]"}`}>
          {p.size > 0 ? "롱" : "숏"} {Math.abs(p.size)}개 · {s.lev?.[c] ?? "-"}배</span>}
        {s.half?.[c] != null && <span title="투표 방향이 바뀌면(새 추세) 전액으로 돌아가요" className="rounded-full bg-warn-soft px-2 py-[2px] text-[13px] font-semibold text-warn">{s.half_k === 0 ? `새 추세 대기 (${s.half[c] < 0 ? "숏" : "롱"} 투표가 끝나면 진입)` : "절반 모드"}</span>}
        {(s.manual ?? []).includes(c) && <span title="/hl_auto 를 보내면 봇이 다시 맡아요" className="rounded-full bg-warn-soft px-2 py-[2px] text-[13px] font-semibold text-warn">{s.scalp?.coin === c ? `분봉 단타 ${s.scalp.lev}배${s.scalp.off ? " (끄는 중)" : ""}` : (s.hold ?? []).includes(c) ? "거래 멈춤 (이익 보호만)" : "직접 주문 (이익 보호만)"}</span>}
        <span className="ml-auto" onClick={(e) => e.stopPropagation()}>
          <VotePop side="right" votes={votes}><span className="rounded-full bg-chip px-2 py-[2px] text-[13px] underline decoration-dotted underline-offset-2">투표 롱 {L}·숏 {S} → {expo > 0 ? "+" : ""}{expo.toFixed(2)}</span></VotePop></span>
      </div>
      {p ? <>
        <div className={`mt-1.5 text-[26px] font-bold leading-tight tabular-nums ${tone(upnl)}`}>{upnl > 0 ? "+" : upnl < 0 ? "−" : ""}{usd(Math.abs(upnl), 2)}
          <span className="ml-2 text-[14.5px] font-semibold">{won(Math.abs(upnl)).replace(" ≈ ", upnl < 0 ? "−" : "+")}</span></div>
        <div className="mt-3 grid grid-cols-3 gap-2">
          <Stat k="진입가" v={`$${fmtPx(p.entry)}`} sub={`지금 ${gap(p.entry)}`} />
          <Stat k="이익 보호 정리선" cls="text-warn" v={g.cut < 1 ? "발동함 · 25% 남김" : g.line ? `$${fmtPx(g.line)}` : "다음 마감부터"}
            sub={g.cut < 1 ? "방향 바뀔 때까지" : g.line ? `${gap(g.line)} · 4시간 종가 기준` : ""} />
          <Stat k="강제청산 (이 코인만)" v={p.liq ? `$${fmtPx(p.liq)}` : "-"} sub={p.liq ? gap(p.liq) : ""} />
        </div>
      </> : <div className="mt-2 text-[15.5px]">현금 (포지션 없음)</div>}
      {s.scalp?.coin === c && <div className="mt-2 rounded-xl bg-warn-soft px-2.5 py-1.5 text-[13px] leading-relaxed text-warn">
        분봉 단타 · 1분봉 투표 롱 {Math.round((s.scalp.long ?? 0) * 100)}%·숏 {Math.round((s.scalp.short ?? 0) * 100)}% (문턱 {Math.round(s.scalp.th * 100)}%)
        {s.scalp.sl ? ` · 손절선 ${usd(s.scalp.sl)}` : ""}{s.scalp.block ? ` · 손절 뒤 ${s.scalp.block > 0 ? "롱" : "숏"} 쉬는 중` : ""}</div>}
      {((s.resv ?? []) as Any[]).filter((x) => x.coin === c).map((x) => <div key={x.id} className="mt-2 rounded-xl bg-accent-soft px-2.5 py-1.5 text-[13px] text-accent">예약 {x.id}번 · {x.text}</div>)}
      {pr && <div className={`mt-auto pt-2.5`}><div className={`rounded-xl px-2.5 py-1.5 text-[13.5px] ${pr.hot ? "bg-warn-soft font-semibold text-warn" : "bg-chip"}`}>{nextRebal().at} 재조정 예상: {pr.text}</div></div>}
    </div>);
}

function RiskCard({ s, coins }: { s: Any; coins: string[] }) {
  const x = jointLiq(s), nr = nextRebal();
  const side = x == null ? "" : x > 0 ? "오르면" : "내리면";
  return (
    <div className="flex h-full flex-col rounded-[20px] border border-line bg-card p-4 shadow-card">
      <div className="text-[14.5px] font-bold">계좌 전체</div>
      <div className="mt-2 text-[12.5px] text-sub">두 코인이 같이 {side || "움직이면"} 강제청산</div>
      <div className="text-[26px] font-bold leading-tight tabular-nums">{x == null ? "-" : `${x > 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(1)}%`}</div>
      {x != null && <div className="mt-0.5 text-[13px] tabular-nums text-sub">{coins.filter((c) => s.pos?.[c]).map((c) => `${COIN_KO[c] ?? c} $${fmtPx((s.marks?.[c] ?? 0) * (1 + x))}`).join(" · ")}</div>}
      <p className="mb-2.5 mt-1 text-[12px] leading-relaxed text-sub">같은 계좌를 같이 쓰는 교차 증거금이라, 코인 하나씩 본 청산가보다 가까워요.</p>
      <div className="mt-auto flex items-baseline justify-between border-t border-line pt-2.5">
        <span className="text-[12.5px] text-sub">다음 재조정</span><span className="text-[16.5px] font-bold tabular-nums">{nr.at} <span className="text-[13px] font-normal text-sub">({nr.left})</span></span></div>
      <div className="mt-1 flex items-baseline justify-between">
        <span className="text-[12.5px] text-sub">펀딩비 (받음+ / 냄−)</span><span className={`text-[14.5px] font-semibold tabular-nums ${tone(s.funding?.total ?? 0)}`}>{(s.funding?.total ?? 0) >= 0 ? "+" : "−"}${Math.abs(s.funding?.total ?? 0).toFixed(3)}</span></div>
      <div className="mt-1 flex items-baseline justify-between">
        <span className="text-[12.5px] text-sub">{s.live ? "출금 가능" : "현금"}</span><span className="text-[14.5px] font-semibold tabular-nums">{usd(s.live ? s.withdrawable_usd : s.cash_usd, 2)}</span></div>
    </div>);
}

// 분석가(서브에이전트)별 의견 → 메인. state.desk·state.analyst 는 봇이 재조정 때 적는다. AI 운용역은 그림자(기록만, 매매 안 함)
export const ANALYSTS: [string, string][] = [["추세", "가격 흐름"], ["파생", "펀딩비·청산·미결제약정·쏠림"], ["거시·심리", "스테이블코인·공포탐욕·옵션"]];
export function ExpoBar({ v }: { v: number }) {      // 가운데가 0, 왼쪽 숏(파랑)·오른쪽 롱(빨강)
  return (
    <div className="relative h-2 rounded-full bg-chip">
      <div className={`absolute inset-y-0 rounded-full ${v >= 0 ? "bg-up" : "bg-down"}`} style={v >= 0 ? { left: "50%", width: `${Math.min(1, v) * 50}%` } : { right: "50%", width: `${Math.min(1, -v) * 50}%` }} />
      <div className="absolute inset-y-[-2px] left-1/2 w-px bg-sub/50" />
    </div>);
}
function DeskCard({ s, coin, ai, goDesk, paper, trader }: { s: Any; coin: string; ai?: Any; goDesk?: () => void; paper?: boolean; trader?: boolean }) {
  const desk: Record<string, number> = s.desk?.[coin] ?? {}, votes: Record<string, number> = s.votes?.[coin] ?? {}, who: Record<string, string> = s.analyst ?? {};
  const main = deskExpo(s, coin), a = ai?.coins?.[coin];
  const row = "grid grid-cols-[76px_1fr_48px] items-center gap-x-2";
  return (
    <Card title={`분석가 의견 · ${COIN_KO[coin] ?? coin}`} right={goDesk ? <button onClick={goDesk} className="press text-[13px] text-accent">상황실 ›</button> : <span className="text-[12.5px] text-sub">−1 숏 · +1 롱</span>}>
      {!Object.keys(desk).length ? <p className="text-[14px] text-sub">다음 4시간 점검 때 채워져요.</p> : (
        <ul className="space-y-2.5 text-[14px]">
          {ANALYSTS.filter(([k]) => k in desk).map(([k, what]) => {
            const mine = Object.fromEntries(Object.entries(votes).filter(([n]) => who[n] === k));
            return (
              <li key={k} className={row}>
                <VotePop side="left" votes={mine}><span className="font-bold underline decoration-dotted underline-offset-2">{k}</span></VotePop>
                <ExpoBar v={desk[k]} /><span className={`text-right font-semibold tabular-nums ${tone(desk[k])}`}>{sgn(desk[k])}</span>
                <span className="col-span-3 text-[12.5px] text-sub">{what} · 규칙 {Object.keys(mine).length}개</span>
              </li>);
          })}
          <li className={`${row} border-t border-line pt-2.5`}>
            <span className="font-bold">메인</span><ExpoBar v={main} /><span className={`text-right font-bold tabular-nums ${tone(main)}`}>{sgn(main)}</span>
            <span className="col-span-3 text-[12.5px] text-sub">분석가 의견의 평균이에요(−1 숏 · +1 롱). 봇은 이 값에 배수를 곱해 포지션을 정해요.</span>
          </li>
        </ul>)}
      {!paper && <div className="mt-3 rounded-xl bg-chip p-2.5 text-[13.5px] leading-relaxed">
        <div className="flex items-baseline justify-between gap-2"><b>{trader ? "AI 운용역 (이 계좌를 운용)" : "AI 운용역 (그림자)"}</b>{a && <span className={`font-semibold tabular-nums ${tone(a.exposure)}`}>{sgn(a.exposure)}</span>}</div>
        {a ? <><p className="mt-1">{ai.easy}</p><p className="mt-1 text-[12.5px] text-sub">{a.reason}</p><p className="mt-1 text-[12px] text-sub">{ago(ai.time)} · {trader ? "이 값에 배수를 곱해 가상으로 사고팔아요. 15분마다 다시 판단해요." : "기록만 하고 매매에는 쓰지 않아요."}</p></>
          : <p className="mt-1 text-sub">{trader ? "첫 판단을 기다리는 중이에요." : "다음 4시간 점검 때 첫 의견이 기록돼요. 매매에는 쓰지 않아요."}</p>}
      </div>}
      <Help id="desk" items={[
        ["숫자 (−1 ~ +1)", "−1은 '전부 숏(내린다)', +1은 '전부 롱(오른다)', 0은 중립이에요. 막대는 가운데가 0이고 왼쪽 파랑이 숏, 오른쪽 빨강이 롱이에요."],
        ["추세", "가격이 평균선 위인지 아래인지 같은 가격 흐름 규칙들의 평균이에요."],
        ["파생", "선물 시장 규칙들이에요. 펀딩비(롱·숏이 주고받는 돈)가 과열인지, 강제청산이 급증했는지, 미결제약정(열려 있는 선물 계약 총액)이 과열인지, 개인 계정이 한쪽으로 몰렸는지를 봐요."],
        ["거시·심리", "스테이블코인 시가총액(코인 시장에 들어와 있는 달러), 공포·탐욕 지수, 옵션 시장이 예상하는 출렁임을 보는 규칙들이에요."],
        ["메인", "세 분석가 숫자의 평균이에요. 봇은 '이 코인 몫의 돈 × 메인 × 배수'만큼 포지션을 잡아요. 메인이 −0.9면 거의 최대로 숏이에요."],
        [trader ? "AI 운용역" : "AI 운용역 (그림자)", trader ? "AI가 지표를 보고 고른 숫자(−1~+1)예요. 이 계좌는 이 숫자대로 가상 매매해요." : "같은 자료를 AI에게 보여 주고 '나라면 얼마'를 받아 적은 숫자예요. 기록만 하고 매매에는 쓰지 않아요."],
      ]} />
    </Card>);
}

// 직접 조작 — /api/command 가 state/hl_commands.txt 에 적고 봇이 30초 안에 처리한다 (텔레그램 /hl_* 와 같은 명령)
function ControlCard() {
  const [msg, setMsg] = useState("");
  const send = async (action: string, ask?: string) => {
    if (ask && !window.confirm(ask)) return;
    try {
      const r = await fetch("/api/command", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action }) });
      setMsg(r.ok ? "보냈어요. 30초 안에 처리하고 텔레그램으로 알려 줘요." : "명령을 보내지 못했어요");
    } catch { setMsg("명령을 보내지 못했어요"); }
    setTimeout(() => setMsg(""), 6000);
  };
  const Btn = ({ a, label, ask, warn }: { a: string; label: string; ask?: string; warn?: boolean }) => (
    <button onClick={() => send(a, ask)} className={`press rounded-xl border px-3 py-2 text-[14px] font-semibold ${warn ? "border-warn text-warn" : "border-line"}`}>{label}</button>);
  return (
    <Card title="직접 조작">
      <div className="flex flex-wrap gap-2">
        <Btn a="hl_status" label="상태를 텔레그램으로" />
        <Btn a="hl_half" label="절반으로 줄이기" ask="지금 포지션을 절반으로 줄이고, 투표 방향이 바뀌면(새 추세) 전액으로 들어가요. 진행할까요?" />
        <Btn a="hl_wait" label="전부 정리하고 대기" ask="지금 포지션을 전부 정리하고, 투표 방향이 바뀔 때까지 기다려요. 진행할까요?" />
        <Btn a="hl_go" label="멈춤·대기 풀기" ask="멈춤·대기·절반을 풀어요. 다음 4시간 점검부터 투표대로 전액으로 들어가요. 진행할까요?" />
        <Btn a="hl_stop" label="전부 정리하고 멈춤" warn ask="지금 포지션을 전부 정리하고 봇을 멈춰요. 진행할까요?" />
      </div>
      {msg && <p className="mt-2 text-[13.5px] font-semibold text-accent">{msg}</p>}
      <p className="mt-2 text-[12.5px] leading-relaxed text-sub">텔레그램에서도 /hl_status · /hl_half · /hl_wait · /hl_go · /hl_stop 으로 같은 일을 할 수 있어요. 코인별 주문(/hl_long · /hl_short · /hl_close)과 예약 주문(/hl_at 코인 가격 close, 예: /hl_at ETH 2% close)은 화면 위쪽 '주문 대화창'이나 텔레그램에서 해요. 포지션이 바뀌는 명령은 60초 안에 /hl_yes 를 보내야 실행돼요.</p>
    </Card>);
}

// 주문 대화창 — 텔레그램에 말하듯 적으면 봇이 답한다(같은 명령함). 주문은 봇의 확인 문구 뒤 '실행'(/hl_yes)을 눌러야 나간다. 기록 logs/hl_chat.jsonl
const YES = "/hl_yes 를 보내면 실행해요";
function ChatCard({ d }: { d: HlData }) {
  const [text, setText] = useState("");
  const [local, setLocal] = useState<Chat[] | null>(null);            // 보낸 직후 서버가 돌려준 기록 (다음 새로고침 전까지 화면에 바로 보이게)
  const [err, setErr] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const tail = (x?: Chat[] | null) => x?.[x.length - 1]?.t ?? 0;
  const rows = (tail(local) > tail(d.chat) ? local : d.chat) ?? [];
  useEffect(() => { box.current?.scrollTo({ top: 1e9 }); }, [tail(rows)]);
  const send = async (t: string) => {
    if (!t.trim()) return;
    setText(""); setErr("");
    try {
      const r = await fetch("/api/command", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "hl_chat", text: t }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error);
      setLocal(j.chat);
    } catch (e) { setErr(`보내지 못했어요${e instanceof Error && e.message ? ` (${e.message})` : ""}`); setText(t); }
  };
  const now = Math.max(d.now ? Date.parse(d.now) / 1000 : 0, tail(rows));
  const ci = rows.map((m) => m.who === "bot" && m.text.includes(YES)).lastIndexOf(true);           // 마지막 확인 문구 — 그 뒤에 '실행'을 안 눌렀고 60초가 안 지났으면 버튼
  const left = ci < 0 || rows.slice(ci + 1).some((m) => m.who === "me" && m.text === "/hl_yes") ? 0 : Math.round(60 - (now - rows[ci].t));
  const last = rows[rows.length - 1];
  const hm = (t: number) => new Date(t * 1000).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
  return (
    <Card title="주문 대화창" right={<span className="text-[12.5px] text-sub">실계좌 · 텔레그램과 같은 봇</span>}>
      <div ref={box} className="h-[300px] space-y-2 overflow-y-auto rounded-xl bg-chip p-2.5">
        {!rows.length && <p className="p-2 text-[13.5px] leading-relaxed text-sub">말하듯 적으면 봇이 답해요. 주문은 봇이 금액과 예상 청산가를 보여 준 뒤 '실행'을 눌러야 나가요.</p>}
        {rows.map((m, i) => (
          <div key={i} className={`flex ${m.who === "me" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[88%] whitespace-pre-wrap break-words rounded-2xl px-3 py-2 text-[13.5px] leading-relaxed ${m.who === "me" ? "bg-accent text-white" : "border border-line bg-card"}`}>
              {m.who === "me" ? (m.text === "/hl_yes" ? "실행" : m.text) : m.text.replace(/\d+초 안에 \/hl_yes 를 보내면 실행해요/, "60초 안에 아래 '실행'을 누르면 실행해요")}
              <span className={`ml-2 text-[11px] ${m.who === "me" ? "text-white/70" : "text-sub"}`}>{hm(m.t)}</span>
            </div>
          </div>))}
        {last?.who === "me" && now - last.t < 120 && <p className="px-1 text-[12.5px] text-sub">봇이 처리하고 있어요…</p>}
      </div>
      {left > 0 && (
        <div className="mt-2 flex items-center gap-2 rounded-xl border border-warn p-2">
          <span className="flex-1 text-[13px] leading-snug">위 내용대로 실행할까요? <span className="text-sub">(약 {left}초 남음, 안 누르면 취소돼요)</span></span>
          <button onClick={() => send("/hl_yes")} className="press rounded-xl bg-warn px-4 py-2 text-[14px] font-bold text-card">실행</button>
        </div>)}
      {!d.running && <p className="mt-2 text-[13px] font-semibold text-warn">봇이 꺼져 있어서 지금은 답하지 못해요.</p>}
      <form onSubmit={(e) => { e.preventDefault(); send(text); }} className="mt-2 flex gap-2">
        <input value={text} onChange={(e) => setText(e.target.value)} maxLength={300} placeholder="예: 20배로 100달러 롱쳐" aria-label="봇에게 보낼 말"
          className="min-w-0 flex-1 rounded-xl border border-line bg-card px-3 py-2 text-[16px] outline-none focus:border-accent" />
        <button type="submit" disabled={!text.trim()} className="press rounded-xl bg-accent px-4 py-2 text-[14px] font-bold text-white disabled:opacity-40">보내기</button>
      </form>
      {err && <p className="mt-1 text-[13px] font-semibold text-warn">{err}</p>}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {["지금 상태 알려줘", "20배로 100달러 롱쳐", "20배로 100달러 숏쳐", "비트 정리해", "비트 63000 오면 100달러 롱 예약", "예약 뭐 있어", "단타 켜줘", "단타 꺼줘"].map((x) => (
          <button key={x} onClick={() => setText(x)} className="press rounded-full bg-chip px-2.5 py-1 text-[12.5px] text-sub">{x}</button>))}
      </div>
      <Help id="chat" title="대화창 쓰는 법" items={[
        ["배수 골라 주문", "'20배로 100달러 롱쳐'라고 적으면 넣는 돈 100달러 × 20배 = 2,000달러어치 롱을 잡아요. '10배로 300달러 숏쳐'도 같아요. 계좌 전부를 쓰려면 '계좌 20배 롱'이라고 적어요. 교차 마진이라 손실이 넣는 돈에서 멈추지 않고 계좌 전체로 버텨요. 실제 청산가는 실행 전에 봇이 보여 줘요."],
        ["말로 주문", "'비트 100달러 롱', '비트 계좌 2배로 숏', '비트 정리해'처럼 적으면 봇이 주문 금액과 주문 뒤 예상 청산가를 보여 줘요. 그 뒤 '실행'을 눌러야 주문이 나가요. 60초 안에 안 누르면 취소돼요."],
        ["예약 주문", "'비트 63000 오면 100달러 롱 예약', '2% 수익이면 정리 예약'처럼 적어요. 봇이 30초마다 가격을 보고 닿으면 지정가로 주문해요. '예약 뭐 있어', '예약 1번 취소'도 돼요."],
        ["직접 주문한 코인", "직접 주문하거나 예약이 실행된 코인은 '비트 다시 봇한테 맡겨'라고 할 때까지 봇이 사고팔지 않아요(이익 보호만 걸려요)."],
        ["답이 늦을 때", "보통 10~20초 안에 답해요. 봇이 지정가 주문을 채우는 동안(최대 5분)은 답이 늦어요. 주문은 전부 지정가라 체결에도 몇 분 걸릴 수 있어요."],
        ["기록", "봇이 텔레그램으로 보내는 알림(체결·경고)도 여기에 같이 떠요. 여기서 한 대화의 답도 텔레그램에 똑같이 가요. 아래 예시 버튼은 입력칸에 글만 채워요."],
      ]} />
    </Card>);
}

const eok = (x: number) => (x >= 1e8 ? `${(x / 1e8).toFixed(2)}억` : `${(x / 1e4).toFixed(0)}만`) + " 달러";
const pct = (x?: string, d = 1) => (x ? `${(+x * 100).toFixed(d)}%` : "-");

// 시장 심리 — research/hl_sentiment.py 가 매시간 쌓는 기록의 마지막 값. 봇 판단에는 쓰지 않는다.
function SentimentCard({ r, rows, coin, krw }: { r?: Record<string, string>; rows: number; coin: string; krw?: number | null }) {
  if (!r) return <Card title="시장 심리 (참고용)"><p className="text-[14px] text-sub">아직 기록이 없어요. 매시간 5분에 한 줄씩 쌓여요.</p></Card>;
  const whale = (g: string, label: string) => {
    const L = +r[`${g}_long_usd`] || 0, S = +r[`${g}_short_usd`] || 0, sh = L + S ? S / (L + S) : 0;
    return (
      <li>
        <div className="flex justify-between"><span>{label}</span><b className={sh > 0.5 ? "text-down" : "text-up"}>숏 {Math.round(sh * 100)}%</b></div>
        <div className="mt-1 flex h-1.5 overflow-hidden rounded-full bg-chip"><div className="bg-up" style={{ width: `${(1 - sh) * 100}%` }} /><div className="bg-down" style={{ width: `${sh * 100}%` }} /></div>
        <div className="mt-0.5 text-[13px] text-sub">롱 {r[`${g}_long_n`]}명 {eok(L)} · 숏 {r[`${g}_short_n`]}명 {eok(S)}</div>
      </li>);
  };
  return (
    <Card title={`시장 심리 · ${COIN_KO[coin] ?? coin} (참고용)`} right={<span className="text-[12.5px] text-sub">{ago(r.time)}</span>}>
      <ul className="space-y-2.5 text-[14px]">
        {whale("m60", "하이퍼리퀴드 이번 달 수익 상위 60명")}
        {whale("all30", "하이퍼리퀴드 누적 수익 상위 30명")}
        <li>바이낸스 롱 비중: 상위 트레이더 금액 <b>{pct(r.bn_top_pos_long)}</b> · 계정 {pct(r.bn_top_acc_long)} · 전체 계정 {pct(r.bn_global_long)}</li>
        <li>펀딩비: 바이낸스 {pct(r.bn_funding_8h, 4)}/8시간 · 하이퍼리퀴드 {pct(r.hl_funding_1h, 4)}/1시간 <span className="text-sub">(+면 롱이 숏에게 냄)</span></li>
        <li>미결제약정: 바이낸스 {eok(+r.bn_oi_usd)} · 하이퍼리퀴드 {eok(+r.hl_oi_usd)}</li>
      </ul>
      <p className="mt-2 text-[12.5px] leading-relaxed text-sub">봇 판단에는 쓰지 않아요. 과거 기록이 없는 데이터라 지금부터 쌓아서 나중에 같은 합격 기준으로 재 볼 거예요 (지금 {Math.floor(rows / 2)}시간치).</p>
      <Help id="sentiment" items={[
        ["수익 상위 60명 · 30명 — 숏 N%", "하이퍼리퀴드에서 돈을 잘 번 지갑들이 이 코인에 건 돈 중 숏이 차지하는 비율이에요(금액 기준). 숏 80%면 잘 버는 사람들 돈의 80%가 '내린다' 쪽이에요. 아래 줄은 롱·숏 각각 몇 명이 얼마를 걸었는지예요."],
        ["바이낸스 롱 비중", "50%보다 크면 롱이 더 많다는 뜻이에요. '상위 트레이더 금액'은 큰 계정들이 건 돈 중 롱 비율, '계정'은 큰 계정 중 롱인 계정 수 비율, '전체 계정'은 개인까지 모든 계정 중 롱인 비율이에요. 개인이 한쪽으로 심하게 몰리면 반대로 가는 일이 많아서, 봇 규칙 하나가 '전체 계정'을 반대 방향으로 써요."],
        ["펀딩비", "선물 가격을 현물 가격에 맞추려고 롱과 숏이 정해진 시간마다 주고받는 돈이에요. +0.0100%/8시간이면 롱이 8시간마다 포지션 금액의 0.01%를 숏에게 줘요. +가 클수록 롱이 몰린 과열, −면 숏이 몰린 상태예요. 숏을 들고 있으면 +일 때 받는 쪽이에요."],
        ["미결제약정", "아직 정리되지 않고 열려 있는 선물 계약의 총액이에요. 늘면 새 돈이 들어오는 중이고, 갑자기 크게 불면 과열로 봐요(이때 봇 규칙 하나가 절반만 걸어요)."],
        ["억 달러", krw ? `1억 달러는 약 ${Math.round(krw).toLocaleString("ko-KR")}억 원이에요.` : "1억 달러 = 100,000,000달러예요."],
      ]} />
    </Card>);
}

// 고래 — research/hl_sentiment.py 가 매시 5분 logs/hl_whales_latest.json·hl_whale_alerts.jsonl 에 쓴다 (참고용, 한 줄 전체)
const bare = (x: number) => eok(Math.abs(x)).replace(" 달러", "");
function WhaleSection({ w, alerts, coin, krw }: { w?: Any; alerts?: Any[]; coin: string; krw?: number | null }) {
  const c = w?.coins?.[coin];
  const px: number = c?.price ?? 0;
  const bins: Any[] = c?.bins ?? [];
  const max = Math.max(1, ...bins.map((b) => Math.max(b.long, b.short)));
  const { dn, up } = near10(bins);
  const grp = (g: string, label: string) => {
    const G = c?.groups?.[g] ?? {}, L = G.long ?? 0, S = G.short ?? 0, sh = L + S ? S / (L + S) : 0;
    return (
      <li>
        <div className="flex justify-between"><span>{label} <span className="text-sub">({G.n ?? 0}곳)</span></span><b className={sh > 0.5 ? "text-down" : "text-up"}>{sh > 0.5 ? `숏 ${Math.round(sh * 100)}%` : `롱 ${Math.round((1 - sh) * 100)}%`}</b></div>
        <div className="mt-1 flex h-2 overflow-hidden rounded-full bg-chip"><div className="bg-up" style={{ width: `${(1 - sh) * 100}%` }} /><div className="bg-down" style={{ width: `${sh * 100}%` }} /></div>
        <div className="mt-0.5 text-[13px] text-sub">롱 {eok(L)} · 숏 {eok(S)}</div>
      </li>);
  };
  const rows = [...bins].sort((x, y) => y.pct - x.pct);
  const at = rows.findIndex((b) => b.pct < 0);
  const line = (b: Any) => (
    <li key={b.pct} className="grid grid-cols-[136px_1fr_110px] items-center gap-2 tabular-nums">
      <span className="text-sub">${Math.round(px * (1 + b.pct / 100)).toLocaleString()}~ <span className="text-[12px]">({b.pct >= 0 ? "+" : ""}{b.pct}%)</span></span>
      <div className="h-2.5 rounded-full bg-chip">
        {b.short > 0 && <div className="h-2.5 rounded-full bg-down" style={{ width: `${(b.short / max) * 100}%` }} />}
        {b.long > 0 && <div className="h-2.5 rounded-full bg-up" style={{ width: `${(b.long / max) * 100}%` }} />}
      </div>
      <span className="text-right">{b.short > 0 ? <b className="text-down">숏 {bare(b.short)}</b> : <b className="text-up">롱 {bare(b.long)}</b>}</span>
    </li>);
  const now = <li key="now" className="border-y border-line py-0.5 text-center text-[13px] font-bold">지금 ${px.toLocaleString()}</li>;
  const tag = (t: string) => t && <span className={`rounded px-1 text-[12px] bg-chip ${t === "smart" ? "text-up" : "text-warn"}`}>{t === "smart" ? "실적 상위" : "실적 약함"}</span>;
  return (
    <Card title={`고래 · ${COIN_KO[coin] ?? coin} (참고용, 봇 판단에 안 씀)`} right={<span className="text-[12.5px] text-sub">{w ? `${ago(w.time)} · 매시 5분 갱신` : ""}</span>} className="border-0 p-0 shadow-none">
      <div className="mb-4 [&>div]:mt-0"><Help id="whale" title="고래 화면 읽는 법" wide items={[
        ["고래", "하이퍼리퀴드에서 돈을 크게 굴리는 지갑 약 100곳이에요. 누가 어느 쪽에 얼마를 걸었는지 거래소가 공개해서 볼 수 있어요."],
        ["실적 상위 지갑 — 숏 N%", "이번 달이나 누적으로 돈을 많이 번 지갑들이에요. 이 지갑들이 이 코인에 건 돈 중 숏 비율이에요(막대 빨강 = 롱, 파랑 = 숏). 아래 줄은 롱·숏에 건 돈의 합계예요."],
        ["실적 약한 큰 지갑", "계좌는 100만 달러가 넘는데 누적으로는 잃은 지갑들이에요. 잘 버는 쪽과 방향이 다르면 눈여겨봐요."],
        ["청산 지도", "고래들의 강제청산 가격을 지금 가격에서 1%씩 칸을 나눠 모은 거예요. '$66,000~ (+2%) 숏 0.50억'은 가격이 2% 올라 그 구간에 닿으면 숏 0.5억 달러어치가 강제로 정리된다는 뜻이에요. 지금 가격 위에는 숏의 청산가, 아래에는 롱의 청산가가 쌓여요."],
        ["왜 보나요", "큰 덩어리가 쌓인 가격에 닿으면 강제 매매가 한꺼번에 나와서 가격이 그 방향으로 더 빨리 움직일 수 있어요. '±10% 안: 아래 롱 · 위 숏'은 지금 가격에서 10% 안쪽에 쌓인 금액이에요."],
        ["큰 포지션 10개", "가장 큰 포지션들이에요. 방향 = 롱·숏과 건 금액, 지갑 = 지갑 주소 앞부분, 진입가 = 들어간 평균 가격, 청산가 = 강제청산 가격(괄호는 지금 가격에서 몇 % 떨어져 있는지), 배수 = 자기 돈의 몇 배를 걸었는지, 누적 수익 = 그 지갑이 지금까지 번 돈(−면 잃은 돈)이에요."],
        ["고래 알림 기록", "실적 상위 지갑들의 포지션 합계가 1시간 사이 크게 바뀌거나, 1천만 달러가 넘는 포지션이 새로 열리거나 정리됐을 때 텔레그램으로 보낸 알림이에요."],
        ["억 · 만 달러", krw ? `1억 달러는 약 ${Math.round(krw).toLocaleString("ko-KR")}억 원, 100만 달러는 약 ${(krw / 100).toFixed(1)}억 원이에요.` : "1억 달러 = 100,000,000달러예요."],
      ]} /></div>
      {!c ? <p className="text-[14px] text-sub">아직 기록이 없어요. 매시간 5분에 갱신돼요.</p> : (
      <div className="grid gap-6 xl:grid-cols-2 min-[1800px]:grid-cols-[1fr_1.4fr_1fr]">
        <div className="min-w-0">
          <ul className="space-y-2.5 text-[14px]">
            {grp("smart", "실적 상위 지갑 (이번 달·누적 수익 상위)")}
            {grp("weak", "실적 약한 큰 지갑 (누적 손실)")}
          </ul>
          <p className="mt-4 text-[14px] font-bold">청산 지도 <span className="font-normal text-sub">— 고래 청산가를 1% 칸으로</span></p>
          <p className="mt-0.5 text-[13.5px]">±10% 안: 아래 <b className="text-up">롱 {eok(dn)}</b> · 위 <b className="text-down">숏 {eok(up)}</b></p>
          <ul className="mt-1.5 space-y-1 text-[13px]">
            {rows.length === 0 ? <li className="text-sub">±15% 안에 고래 청산가가 없어요.</li>
              : [...rows.slice(0, at < 0 ? rows.length : at).map(line), now, ...(at < 0 ? [] : rows.slice(at).map(line))]}
          </ul>
        </div>
        <div className="min-w-0">
          <p className="text-[14px] font-bold">큰 포지션 10개</p>
          <ul className="mt-1.5 space-y-1.5 text-[13.5px] tabular-nums md:hidden">
            {(c.big ?? []).slice(0, 10).map((b: Any, i: number) => (
              <li key={i} className="rounded-xl bg-chip px-2.5 py-1.5">
                <div className="flex items-center gap-1.5"><b className={b.usd > 0 ? "text-up" : "text-down"}>{b.usd > 0 ? "롱" : "숏"} {bare(b.usd)}</b><span>{b.who}</span>{tag(b.tag)}
                  <span className={`ml-auto ${b.pnl_all >= 0 ? "text-up" : "text-down"}`}>누적 {b.pnl_all >= 0 ? "+" : "−"}{bare(b.pnl_all)}</span></div>
                <div className="text-[12.5px] text-sub">진입 ${Math.round(b.entry).toLocaleString()} · 청산 {b.liq ? `$${Math.round(b.liq).toLocaleString()} (${b.liq > px ? "+" : ""}${((b.liq / px - 1) * 100).toFixed(0)}%)` : "-"} · {b.lev}배</div>
              </li>))}
          </ul>
          <div className="mt-1.5 hidden overflow-x-auto md:block">
            <table className="w-full text-[13px] tabular-nums">
              <thead className="text-left text-sub"><tr>{["방향", "지갑", "진입가", "청산가", "배수", "누적 수익"].map((h) => <th key={h} className="pb-1 pr-2 font-normal">{h}</th>)}</tr></thead>
              <tbody>
                {(c.big ?? []).slice(0, 10).map((b: Any, i: number) => (
                  <tr key={i} className="border-t border-line">
                    <td className="py-1 pr-2"><b className={b.usd > 0 ? "text-up" : "text-down"}>{b.usd > 0 ? "롱" : "숏"} {bare(b.usd)}</b></td>
                    <td className="py-1 pr-2"><span className="mr-1">{b.who}</span>{tag(b.tag)}</td>
                    <td className="py-1 pr-2">${Math.round(b.entry).toLocaleString()}</td>
                    <td className="py-1 pr-2">{b.liq ? <>${Math.round(b.liq).toLocaleString()} <span className="text-sub">({b.liq > px ? "+" : ""}{((b.liq / px - 1) * 100).toFixed(0)}%)</span></> : "-"}</td>
                    <td className="py-1 pr-2">{b.lev}배</td>
                    <td className={`py-1 ${b.pnl_all >= 0 ? "text-up" : "text-down"}`}>{b.pnl_all >= 0 ? "+" : "−"}{bare(b.pnl_all)}</td>
                  </tr>))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="min-w-0 xl:col-span-2 min-[1800px]:col-span-1">
          <p className="text-[14px] font-bold">고래 알림 기록 <span className="font-normal text-sub">— 텔레그램으로 보낸 것, 두 코인 모두</span></p>
          {!(alerts ?? []).length ? <p className="mt-1.5 text-[13.5px] text-sub">아직 알림이 없어요. 실적 상위 순포지션이 1시간에 크게 바뀌거나, 1천만 달러 넘는 포지션이 열리거나 정리되면 여기에 쌓여요.</p> : (
            <ul className="mt-1.5 max-h-[420px] space-y-2 overflow-y-auto pr-1 text-[13px]">
              {(alerts ?? []).map((a: Any, i: number) => (
                <li key={i} className="rounded-xl bg-chip px-2.5 py-2">
                  <div className="text-[12px] text-sub">{ago(a.time)}</div>
                  {(a.msgs as string[]).map((m, j) => <p key={j} className="mt-1 whitespace-pre-line leading-relaxed">{m}</p>)}
                </li>))}
            </ul>)}
        </div>
      </div>)}
      <p className="mt-3 text-[12.5px] leading-relaxed text-sub">하이퍼리퀴드 큰 지갑 약 100곳 기준이에요. 지갑별 기록을 지금부터 쌓아서 2~3개월 뒤 같은 합격 기준으로 재 볼 거예요. 지갑을 지금 순위로 고르기 때문에 과거를 재는 데는 쓰면 안 돼요.</p>
    </Card>);
}

// 시간대별 방향판 — 1분~1시간은 bot/hl_tfboard.py(참고용), 4시간·일봉은 봇이 실제로 쓴 표(state.votes)
const TF_KO: [string, string][] = [["1m", "1분"], ["5m", "5분"], ["15m", "15분"], ["30m", "30분"], ["1h", "1시간"]];
function TfBoard({ s, coin }: { s: Any; coin: string }) {
  const { data, error } = useFetch<Any>("/api/hl/tfboard", 60_000);
  const votes: Record<string, number> = s.votes?.[coin] ?? {};
  const count = (pre: string) => { const v = Object.entries(votes).filter(([k]) => k.startsWith(pre)).map(([, x]) => x);
    return { long: v.filter((x) => x > 0).length, short: v.filter((x) => x < 0).length, n: v.length }; };
  const rows: [string, Any | undefined, boolean][] = [...TF_KO.map(([k, ko]) => [ko, data?.coins?.[coin]?.[k], false] as [string, Any | undefined, boolean]),
    ["4시간", count("4h "), true], ["일봉", count("일봉 "), true]];
  return (
    <Card title={`시간대별 방향판 · ${COIN_KO[coin] ?? coin}`} right={<span className="text-[12.5px] text-sub">1분마다 갱신</span>}>
      <ul className="space-y-1.5 text-[14px]">
        {rows.map(([ko, r, bot]) => {
          const L = r?.long ?? 0, S = r?.short ?? 0, n = r?.n || 1;
          return (
            <li key={ko} className="grid grid-cols-[52px_1fr_108px] items-center gap-2">
              <VotePop side="left" votes={bot ? Object.fromEntries(Object.entries(votes).filter(([k]) => k.startsWith(ko === "일봉" ? "일봉 " : "4h ")).map(([k, x]) => [k.replace(/^(일봉|4h) /, ""), x])) : r?.votes}>
                <span className={`underline decoration-dotted underline-offset-2 ${bot ? "font-bold" : "text-sub"}`}>{ko}</span></VotePop>
              <div className="flex h-2 overflow-hidden rounded-full bg-chip">
                <div className="bg-up" style={{ width: `${(L / n) * 100}%` }} /><div className="ml-auto bg-down" style={{ width: `${(S / n) * 100}%` }} />
              </div>
              <span className="text-right tabular-nums">{r ? <>롱 <b className="text-up">{L}</b> · 숏 <b className="text-down">{S}</b></> : "…"}</span>
            </li>);
        })}
      </ul>
      {error && <p className="mt-2 text-[12.5px] text-warn">{error}</p>}
      <p className="mt-2 text-[12.5px] leading-relaxed text-sub"><b>굵은 4시간·일봉</b>은 봇이 실제로 매매에 쓰는 표예요. 1분~1시간은 같은 지표 {data?.rules?.length ?? ""}개를 짧은 봉에 적용한 참고용이에요. 짧은 봉은 측정에서 전부 탈락해서 매매에는 쓰지 않아요.</p>
      <Help id="tf" items={[
        ["롱 N · 숏 N", "그 길이의 봉에 규칙들을 적용했을 때 '오른다(롱)'에 표를 준 규칙 수와 '내린다(숏)'에 표를 준 규칙 수예요. 막대는 빨강이 롱, 파랑이 숏이에요."],
        ["어떻게 읽나요", "짧은 봉(1분~1시간)과 긴 봉(4시간·일봉)이 같은 색이면 단기와 장기 흐름이 같은 방향이고, 다르면 큰 흐름 안에서 잠깐 반대로 움직이는 중이에요. 줄 이름을 누르면 규칙별 표가 나와요."],
      ]} />
    </Card>);
}

// 코인니스 최근 속보 (logs/coinness.jsonl 끝부분, 참고용)
function NewsCard({ news }: { news?: Any[] }) {
  return (
    <Card title="코인니스 속보 (참고용)" right={<span className="text-[12.5px] text-sub">10분마다 수집</span>}>
      {!(news ?? []).length ? <p className="text-[14px] text-sub">아직 없어요.</p> : (
        <ul className="space-y-1.5 text-[14px]">
          {(news ?? []).map((x, i) => (
            <li key={i} className="flex gap-2 leading-snug">
              <span className="w-[60px] shrink-0 text-[12.5px] tabular-nums text-sub">{ago(x.t)}</span>
              <span className={x.imp ? "font-semibold" : ""}>{x.imp && <span className="mr-1 text-warn">★</span>}{x.title}</span>
            </li>))}
        </ul>)}
      <p className="mt-2 text-[12.5px] leading-relaxed text-sub">봇은 이 중 '24시간 강제 청산' 집계만 투표 규칙 하나(청산 급증)에 써요. 나머지 속보는 측정에서 예측력이 없었어요.</p>
      <Help id="news" items={[
        ["왼쪽 시간", "그 속보가 나온 지 얼마나 됐는지예요."],
        ["★ 굵은 글씨", "코인니스가 '중요'로 표시한 속보예요."],
        ["24시간 강제 청산", "지난 하루 동안 선물 시장에서 강제로 정리된 포지션의 총액이에요. 평소보다 훨씬 크면(급증) 한쪽이 한꺼번에 털렸다는 뜻이라, 봇 규칙 하나가 그 뒤 3일은 반대 방향에 표를 줘요."],
      ]} />
    </Card>);
}

// 사이드용 고래 한눈 요약 → 자세히는 아래 '고래' 탭
function WhaleMini({ w, coin, go }: { w?: Any; coin: string; go: () => void }) {
  const c = w?.coins?.[coin]; if (!c) return null;
  const sh = (g: string) => { const G = c.groups?.[g] ?? {}, L = G.long ?? 0, S = G.short ?? 0; return L + S ? S / (L + S) : 0; };
  const bins: Any[] = c.bins ?? [];
  const { dn, up } = near10(bins);
  const row = (k: string, x: number) => (
    <li className="grid grid-cols-[110px_1fr_68px] items-center gap-2"><span className="text-sub">{k}</span>
      <div className="flex h-2 overflow-hidden rounded-full bg-chip"><div className="bg-up" style={{ width: `${(1 - x) * 100}%` }} /><div className="bg-down" style={{ width: `${x * 100}%` }} /></div>
      <b className={`text-right ${x > 0.5 ? "text-down" : "text-up"}`}>{x > 0.5 ? `숏 ${Math.round(x * 100)}%` : `롱 ${Math.round((1 - x) * 100)}%`}</b></li>);
  return (
    <Card title={`고래 · ${COIN_KO[coin] ?? coin}`} right={<button onClick={go} className="press text-[13px] text-accent">자세히 ›</button>}>
      <ul className="space-y-1.5 text-[13.5px]">{row("실적 상위", sh("smart"))}{row("실적 약한 큰손", sh("weak"))}</ul>
      <p className="mt-2 text-[13.5px]">±10% 안 청산: 아래 <b className="text-up">롱 {bare(dn)}</b> · 위 <b className="text-down">숏 {bare(up)}</b></p>
      <p className="mt-1 text-[12px] text-sub">{ago(w.time)} · 참고용</p>
      <Help id="whalemini" items={[
        ["실적 상위 · 실적 약한 큰손", "하이퍼리퀴드 큰 지갑 중 돈을 잘 번 쪽과 잃은 쪽이에요. '숏 90%'는 그 지갑들이 이 코인에 건 돈의 90%가 숏(내린다)이라는 뜻이에요."],
        ["±10% 안 청산", "가격이 10% 안에서 내리면 강제로 정리될 고래 롱 금액(아래)과, 오르면 강제로 정리될 고래 숏 금액(위)이에요. 단위는 달러예요."],
      ]} />
    </Card>);
}

function TradeList({ trades, won, n }: { trades: Record<string, string>[]; won: (x?: number | null) => string; n: number }) {
  if (!trades.length) return <p className="text-[14.5px] text-sub">아직 없어요.</p>;
  return (
    <ul className="space-y-2.5">
      {trades.slice(0, n).map((t) => (
        <li key={t.time + t.coin + t.side} className="text-[14px]">
          <div className="font-bold"><span className={t.side.startsWith("롱") ? "text-up" : "text-down"}>{t.side}</span> {COIN_KO[t.coin] ?? t.coin} {(+t.size).toLocaleString("en-US")}개 · {usd(+t.notional_usd)}{won(+t.notional_usd)}</div>
          <div className="text-[13px] text-sub">{t.time.slice(5, 16).replace("T", " ")} · ${fmtPx(+t.price)} · 평가 {usd(+t.equity_usd)}
            {t.pnl_usd && +t.pnl_usd !== 0 && <> · 실현 <b className={tone(+t.pnl_usd)}>{+t.pnl_usd > 0 ? "+" : "−"}${Math.abs(+t.pnl_usd).toFixed(2)}</b></>}{t.fee_usd && <> · 수수료 ${(+t.fee_usd).toFixed(3)}</>}</div>
          <p className="mt-0.5 leading-relaxed">{t.why}</p>
        </li>))}
    </ul>);
}

// 지표 해설 — 지금 값이 보통 어느 쪽 신호로 읽히는지 한 줄씩(사용자 요청 2026-11-17). 정해 둔 기준으로 계산한다(LLM 아님).
// v: +1 상승 쪽 · −1 하락 쪽 · 0 중립. 봇이 실제로 매매에 쓰는 것은 '봇 규칙'·분석가 줄뿐이고 나머지는 참고용 통념이다.
type Rd = { k: string; v: number; tag?: string; text: string };
function readings(d: HlData, coin: string): { bot: Rd[]; whale: Rd[]; market: Rd[] } {
  const s = d.state, ko = COIN_KO[coin] ?? coin, pc = (x: number) => `${Math.round(x * 100)}%`;
  const bot: Rd[] = [], whale: Rd[] = [], market: Rd[] = [];
  const votes = Object.values(s.votes?.[coin] ?? {}) as number[], main = deskExpo(s, coin);
  if (votes.length) {
    const L = votes.filter((x) => x > 0).length, S = votes.filter((x) => x < 0).length;
    bot.push({ k: "봇 규칙 (실제 매매 기준)", v: main <= -0.3 ? -1 : main >= 0.3 ? 1 : 0,
      text: `규칙 ${votes.length}개 중 숏 ${S}표·롱 ${L}표, 최종 의견 ${sgn(main)}이에요. ` + (main <= -0.6 ? `봇은 ${ko}이 더 내린다고 보고 숏을 크게 잡는 중이에요.` : main <= -0.3 ? "봇은 내리는 쪽으로 보지만 확신은 중간이에요." : main >= 0.6 ? `봇은 ${ko}이 더 오른다고 보고 롱을 크게 잡는 중이에요.` : main >= 0.3 ? "봇은 오르는 쪽으로 보지만 확신은 중간이에요." : "규칙들이 갈려서 봇은 거의 걸지 않아요.") });
  }
  const why: Record<string, [string, string]> = {
    "추세": ["가격이 주요 평균선들 아래에 있어요. 내리는 흐름이 이어지는 중이에요.", "가격이 주요 평균선들 위에 있어요. 오르는 흐름이 이어지는 중이에요."],
    "파생": ["선물 시장 지표(펀딩비·청산·미결제약정·계정 쏠림)가 내리는 쪽을 가리켜요.", "선물 시장 지표(펀딩비·청산·미결제약정·계정 쏠림)가 오르는 쪽을 가리켜요."],
    "거시·심리": ["코인 시장으로 들어오는 돈과 시장 분위기가 약해요.", "코인 시장으로 들어오는 돈과 시장 분위기가 좋아요."] };
  for (const [k, x] of Object.entries(s.desk?.[coin] ?? {}) as [string, number][]) {
    const v = x <= -0.3 ? -1 : x >= 0.3 ? 1 : 0;
    bot.push({ k: `분석가 · ${k}`, v, text: `${sgn(x)}. ` + (v ? why[k]?.[v < 0 ? 0 : 1] ?? "" : "이 묶음의 규칙들은 방향이 갈려서 중립이에요.") + (v && Math.abs(x) < 0.99 ? " 일부 규칙은 반대이거나 쉬고 있어요." : "") });
  }
  const a = d.ai?.coins?.[coin];
  if (a) bot.push({ k: d.inst === "aipaper" ? "AI 운용역" : "AI 운용역 (참고)", v: a.exposure <= -0.3 ? -1 : a.exposure >= 0.3 ? 1 : 0, text: `${sgn(a.exposure)}. ${d.ai?.easy ?? ""}` });

  const side = (sh: number, who: string, n?: number): Rd | null => !Number.isFinite(sh) ? null : ({ k: who, v: sh >= 0.6 ? -1 : sh <= 0.4 ? 1 : 0,
    text: sh >= 0.6 ? `${who}${n ? ` ${n}곳` : ""}이 건 돈의 ${pc(sh)}가 숏이에요. 돈을 잘 버는 쪽이 '더 내린다'에 걸고 있어요.`
      : sh <= 0.4 ? `${who}${n ? ` ${n}곳` : ""}이 건 돈의 ${pc(1 - sh)}가 롱이에요. 돈을 잘 버는 쪽이 '오른다'에 걸고 있어요.` : `롱 ${pc(1 - sh)}·숏 ${pc(sh)}로 갈려 있어요. 뚜렷한 방향이 없어요.` });
  const c = d.whales?.coins?.[coin];
  if (c) {
    const g = (x: string) => { const G = c.groups?.[x] ?? {}, L = G.long ?? 0, S = G.short ?? 0; return { sh: L + S ? S / (L + S) : NaN, n: G.n as number | undefined }; };
    const sm = g("smart"), wk = g("weak"), x = side(sm.sh, "실적 상위 큰 지갑", sm.n);
    if (x) whale.push(x);
    if (Number.isFinite(wk.sh)) whale.push({ k: "실적 약한 큰 지갑", v: wk.sh <= 0.4 ? -1 : wk.sh >= 0.6 ? 1 : 0,
      text: wk.sh <= 0.4 ? `계속 잃어 온 큰 지갑들은 ${pc(1 - wk.sh)}가 롱이에요. 잃는 쪽이 롱에 몰려 있으면 보통 반대(하락)로 읽어요.${sm.sh >= 0.6 ? " 잘 버는 쪽은 숏이라 둘이 정반대예요." : ""}`
        : wk.sh >= 0.6 ? `계속 잃어 온 큰 지갑들은 ${pc(wk.sh)}가 숏이에요. 잃는 쪽이 숏에 몰려 있으면 보통 반대(상승)로 읽어요.` : "롱·숏이 비슷해서 읽을 게 없어요." });
    const { dn, up } = near10(c.bins ?? []);
    if (up + dn > 0) whale.push(up > 2 * dn ? { k: "청산 지도", v: 1, tag: "위로 튈 위험", text: `지금 가격 위 10% 안에 고래 숏 ${eok(up)}의 청산가가 몰려 있어요(아래 롱은 ${eok(dn)}). 가격이 그쪽으로 오르면 숏이 강제로 되사면서 더 급하게 오를 수 있어요. 숏을 들고 있다면 조심할 자리예요.` }
      : dn > 2 * up ? { k: "청산 지도", v: -1, tag: "아래로 꺼질 위험", text: `지금 가격 아래 10% 안에 고래 롱 ${eok(dn)}의 청산가가 몰려 있어요(위 숏은 ${eok(up)}). 가격이 그쪽으로 내리면 롱이 강제로 팔리면서 더 급하게 내릴 수 있어요.` }
      : { k: "청산 지도", v: 0, text: `위 10% 안 숏 ${eok(up)}, 아래 10% 안 롱 ${eok(dn)}로 비슷해요. 어느 쪽으로 가든 강제청산이 비슷하게 나와요.` });
  }
  const m = d.sentiment?.[coin], pv = d.sentimentPrev?.[coin];
  if (m) {
    for (const [g, who] of [["m60", "이번 달 수익 상위 60명"], ["all30", "누적 수익 상위 30명"]] as [string, string][]) {
      const L = +m[`${g}_long_usd`] || 0, S = +m[`${g}_short_usd`] || 0, x = side(L + S ? S / (L + S) : NaN, who);
      if (x) market.push(x);
    }
    const gl = +m.bn_global_long, tp = +m.bn_top_pos_long;
    // 기준은 2020-09 이후 바이낸스 일별 기록의 분포(state/hl_metrics_BTC.pkl): 전체 계정 롱 비중 5%·50%·95% = 42·60·74%, 상위 트레이더(금액) = 51·56·68%.
    // 둘 다 평소에도 절반을 넘으므로 '50% 초과'가 아니라 '평소 범위를 벗어났는지'로 읽는다. 전체 계정은 봇 규칙의 표(z 점수)가 있으면 그것을 따른다.
    const gv = Object.entries(s.votes?.[coin] ?? {}).find(([k]) => k.includes("전체 계정"))?.[1] as number | undefined;
    if (gl) { const v = gv != null ? Math.sign(gv) : gl >= 0.70 ? -1 : gl <= 0.45 ? 1 : 0;
      market.push({ k: "바이낸스 전체 계정 (개인)", v,
        text: `계정의 ${pc(gl)}가 롱이에요(평소 가운데 값은 60%). ` + (v < 0 ? "평소보다 훨씬 많이 '오른다'에 몰려 있어요. 개인이 한쪽에 심하게 몰리면 반대로 가는 일이 많아서 하락 쪽 신호로 읽어요. 봇 규칙 하나도 이걸로 숏에 표를 줘요."
          : v > 0 ? "평소보다 훨씬 많이 '내린다'에 몰려 있어요. 개인이 한쪽에 심하게 몰리면 반대로 가는 일이 많아서 상승 쪽 신호로 읽어요." : "평소 범위 안이라 쏠림 신호는 아니에요.") }); }
    if (tp) market.push({ k: "바이낸스 상위 트레이더 (금액)", v: tp >= 0.68 ? 1 : tp <= 0.505 ? -1 : 0,
      text: `큰 계정들이 건 돈의 ${pc(tp)}가 롱이에요. 이 숫자는 평소에도 51~68%(가운데 56%)라 절반을 넘는 게 보통이에요. ` + (tp >= 0.68 ? "지금은 그 범위보다 높아서 큰 계정들이 평소보다 뚜렷하게 오르는 쪽에 걸고 있어요."
        : tp <= 0.505 ? "지금은 그 범위보다 낮아서 큰 계정들이 평소와 달리 내리는 쪽으로 기울었어요." : "지금은 평소 범위 안이라 이 숫자만으로는 신호가 아니에요.") });
    const f = +m.bn_funding_8h, fp = `${(f * 100).toFixed(4)}%`;
    if (Number.isFinite(f)) market.push({ k: "펀딩비", v: f >= 0.0003 ? -1 : f <= -0.0001 ? 1 : 0,
      text: f >= 0.0003 ? `8시간에 ${fp}로 높아요. 롱이 비싼 값을 내며 버티는 과열이라 꺾일 위험이 있어요.`
        : f <= -0.0001 ? `8시간에 ${fp}로 마이너스예요. 숏이 돈을 내면서 버티는 중이라 숏이 몰린 상태예요. 이럴 때는 반등이 급하게 나올 수 있어요.` : `8시간에 ${fp}로 평소(0.01% 안팎) 수준이에요. 롱·숏 어느 쪽도 과열이 아니에요.` });
    if (pv && +pv.bn_oi_usd && +pv.price) {
      const o = +m.bn_oi_usd / +pv.bn_oi_usd - 1, p = +m.price / +pv.price - 1, t = `하루 사이 가격 ${p >= 0 ? "+" : ""}${(p * 100).toFixed(1)}%, 미결제약정 ${o >= 0 ? "+" : ""}${(o * 100).toFixed(1)}%예요. `;
      market.push(Math.abs(p) < 0.01 || Math.abs(o) < 0.03 ? { k: "미결제약정 변화", v: 0, text: t + "큰 변화가 없어서 새로 읽을 게 없어요." }
        : p < 0 && o > 0 ? { k: "미결제약정 변화", v: -1, text: t + "가격이 내리는데 계약이 늘었어요. 새 숏이 들어오는 중이라 하락에 힘이 실려 있어요." }
        : p < 0 ? { k: "미결제약정 변화", v: 0, tag: "롱 정리 중", text: t + "가격이 내리면서 계약이 줄었어요. 롱이 손 털고 나가는 매물이에요. 다 털리면 하락이 잦아들 수 있어요." }
        : o > 0 ? { k: "미결제약정 변화", v: 1, text: t + "가격이 오르면서 계약이 늘었어요. 새 롱이 들어오는 중이라 상승에 힘이 실려 있어요." }
        : { k: "미결제약정 변화", v: 0, tag: "숏 정리 반등", text: t + "가격이 오르는데 계약이 줄었어요. 숏이 되사면서 오르는 반등이라, 새 돈이 들어온 상승보다 힘이 약할 수 있어요." });
    }
  }
  return { bot, whale, market };
}
function Reads({ rows, cols }: { rows: Rd[]; cols?: boolean }) {
  if (!rows.length) return null;
  return (
    <ul className={`grid gap-x-6 gap-y-2 text-[13.5px] leading-relaxed ${cols ? "md:grid-cols-2 min-[1800px]:grid-cols-3" : ""}`}>
      {rows.map((x) => (
        <li key={x.k} className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5"><b>{x.k}</b>
            <span className={`rounded-full px-2 py-[1px] text-[12px] font-semibold ${x.v > 0 ? "bg-up/15 text-up" : x.v < 0 ? "bg-down/15 text-down" : "bg-chip text-sub"}`}>{x.tag ?? (x.v > 0 ? "상승 쪽" : x.v < 0 ? "하락 쪽" : "중립")}</span></div>
          <p className="text-sub">{x.text}</p>
        </li>))}
    </ul>);
}
function ReadCard({ d, coin }: { d: HlData; coin: string }) {
  const R = readings(d, coin), all = [...R.bot, ...R.whale, ...R.market].filter((x) => !x.tag && !x.k.startsWith("분석가"));   // 분석가 줄은 '봇 규칙'과 같은 표라 개수에서 뺀다
  const dn = all.filter((x) => x.v < 0).length, up = all.filter((x) => x.v > 0).length, mid = all.length - dn - up;
  if (!all.length) return null;
  return (
    <Card title={`지금 지표 해설 · ${COIN_KO[coin] ?? coin}`} right={<span className="text-[12.5px] text-sub">값이 바뀌면 해설도 바뀌어요</span>}>
      <p className="mb-3 text-[15px] font-semibold">하락 쪽 <span className="text-down">{dn}</span> · 상승 쪽 <span className="text-up">{up}</span> · 중립 {mid} →{" "}
        {dn >= 2 * Math.max(1, up) ? <span className="text-down">지금 지표는 대체로 내리는 쪽을 가리켜요.</span> : up >= 2 * Math.max(1, dn) ? <span className="text-up">지금 지표는 대체로 오르는 쪽을 가리켜요.</span> : "지금 지표는 서로 엇갈려요."}</p>
      <Reads rows={[...R.bot, ...R.whale, ...R.market]} cols />
      <p className="mt-3 text-[12.5px] leading-relaxed text-sub">각 지표를 보통 이렇게 읽는다는 해설이에요. 봇이 실제로 매매에 쓰는 것은 '봇 규칙'과 분석가 줄뿐이고, 고래·시장 심리 줄은 아직 과거 성적을 재 보지 못한 참고용이에요. 맞는다는 보장은 없어요.</p>
    </Card>);
}

type RefTab = "whale" | "market" | "log" | "bot";
const REF_TABS: [RefTab, string][] = [["whale", "고래"], ["market", "시장 심리·속보"], ["log", "기록"], ["bot", "봇 설정"]];

export function HlPanel({ d, goDesk }: { d: HlData; goDesk?: () => void }) {
  const s = d.state; const krw = d.krw;
  const coins: string[] = Object.keys(s.votes ?? {}).length ? Object.keys(s.votes) : ["BTC", "ETH"];
  const [coin, setCoin] = useState(coins[0]);
  const [full, setFull] = useState(false);
  const [tab, setTabState] = useState<RefTab>("whale");
  useEffect(() => { try { const t = localStorage.getItem("hlRefTab") as RefTab | null; if (t && REF_TABS.some(([k]) => k === t)) setTabState(t); } catch { /* 저장소 없음 */ } }, []);
  const setTab = (t: RefTab) => { setTabState(t); try { localStorage.setItem("hlRefTab", t); } catch { /* */ } };
  const goWhale = () => { setTab("whale"); document.getElementById("hl-ref")?.scrollIntoView({ behavior: "smooth" }); };
  const eq = s.equity_usd ?? 0, start = s.start_usd ?? 0;
  const won = (x?: number | null) => (x == null || !krw ? "" : ` ≈ ${Math.round(x * krw).toLocaleString("ko-KR")}원`);
  const stale = d.running && s.saved && Date.now() - Date.parse(s.saved) > 120_000;
  const kill = s.kill_drawdown_pct > 0 && start ? start * (1 - s.kill_drawdown_pct / 100) : null;
  const expo = (d.exposure ?? []).filter((r) => r.coin === coin);
  const expoMoved = new Set(expo.map((r) => r.v)).size > 1;
  return (
    <main className="mx-auto max-w-[2400px] space-y-4 px-3 py-3 sm:px-4 sm:py-4">
      {stale && <Card><p className="flex items-center gap-2 text-[14.5px] text-warn"><WarningCircleIcon size={16} />화면 정보가 2분 넘게 새로 오지 않았어요. 봇이 멈췄거나 인터넷이 끊겼을 수 있어요.</p></Card>}
      {!s.live && <Card><p className="text-[14.5px] leading-relaxed"><b>연습 모드</b>예요. 진짜 돈은 안 들어가 있고, 가상 자산 {usd(start)}으로 기록만 해요.
        {d.inst === "aipaper" ? <> 여기는 <b>AI 운용역</b>이 15분마다 15분~일봉 지표·규칙 표·고래 자료를 보고 스스로 방향과 크기를 정해요. 규칙 봇과 성적을 비교하려는 계좌예요.</>
          : d.paper && <> 실계좌 봇과 <b>같은 투표</b>를 대기 없이 그대로 따르는 쌍둥이예요. 실계좌 봇은 '하이퍼리퀴드 롱·숏' 탭에 있어요.</>}</p></Card>}
      {s.live && eq <= 0 && <Card><p className="text-[14.5px] leading-relaxed text-warn"><b>선물 계좌에 돈이 없어요.</b> 하이퍼리퀴드 앱에서 Transfer → Spot → Perps 로 USDC를 옮기면 바로 시작해요.</p></Card>}
      {/* 1) 지금 상황 띠 */}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-[1fr_1fr_minmax(260px,0.62fr)]">
        {coins.map((c) => <CoinMini key={c} s={s} c={c} n={coins.length} won={won} active={coin === c} onClick={() => setCoin(c)} />)}
        <div className="md:col-span-2 xl:col-span-1"><RiskCard s={s} coins={coins} /></div>
      </div>
      {/* 주문 대화창(실계좌만): 휴대폰은 상황 띠 바로 아래, PC 는 지표 해설 오른쪽 */}
      <div className={d.paper ? "" : "grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_400px]"}>
        {!d.paper && <div className="min-w-0 lg:order-2"><ChatCard d={d} /></div>}
        <div className="min-w-0 space-y-4"><ReadCard d={d} coin={coin} />
      <Help id="strip" title="위 카드와 맨 위 숫자 읽는 법" wide items={[
        ["색", "빨강 = 롱(오르면 이익)·이익, 파랑 = 숏(내리면 이익)·손실이에요."],
        ["평가금액 · 시작 대비 · 미실현 손익", "평가금액은 지금 다 정리하면 남는 계좌 돈이에요. '시작 대비'는 처음 넣은 돈에서 몇 % 늘거나 줄었는지, '미실현 손익'은 아직 정리하지 않은 포지션에서 지금 벌거나 잃고 있는 돈이에요."],
        ["숏 0.05개 · 5배", "지금 들고 있는 방향과 수량이에요. '5배'는 봇이 이 코인에 쓰는 배수예요. 가격 출렁임이 작을수록 배수가 커져요."],
        ["투표 롱 1·숏 27 → −0.87", "규칙 28개 중 롱에 1표, 숏에 27표라는 뜻이에요. 화살표 뒤 숫자는 분석가별로 평균 낸 최종 의견이고 −1이 전부 숏, +1이 전부 롱이에요. 누르면 규칙별 표가 나와요."],
        ["큰 숫자 (+$24.16)", "이 코인 포지션에서 지금 벌거나 잃고 있는 돈이에요. 정리하기 전까지는 계속 바뀌어요."],
        ["진입가", "이 포지션을 잡은 평균 가격이에요. 아래 %는 그 가격이 지금 가격보다 몇 % 위(+)·아래(−)인지예요. 숏은 진입가가 지금 가격보다 위일 때 이익이에요."],
        ["이익 보호 정리선", "4시간봉이 이 가격을 넘어 마감하면(숏은 위로, 롱은 아래로) 봇이 포지션의 75%를 정리해요. 거래 중 가장 좋았던 가격에서 5% 되돌린 자리예요. 아래 %는 지금 가격에서 그 선까지 거리예요."],
        ["강제청산 (이 코인만)", "가격이 여기까지 가면 거래소가 포지션을 강제로 닫고 넣은 돈을 거의 다 잃어요. 다른 코인 가격은 그대로라고 본 값이라, 실제로는 '계좌 전체' 카드의 숫자가 더 정확해요."],
        ["N시 재조정 예상", "다음 4시간 점검 때 투표와 가격이 지금 그대로라면 봇이 할 일이에요. '목표와 차이 5% < 완충 30%'는 목표 수량과 지금 수량이 30% 넘게 벌어지지 않아 수수료를 아끼려고 그대로 둔다는 뜻이에요."],
        ["분봉 단타", "주문 대화창에 '단타 켜줘'(/hl_scalp on)라고 하면 켜져요. 봇이 1분마다 1분봉 투표(가격 규칙 16개 중 롱·숏 표 비율)를 보고, 60% 넘는 쪽으로 계좌의 20배를 잡아요. 반대쪽이 60%를 넘으면 뒤집어요. '손절선'은 거래소에 걸어 둔 손절 주문 가격으로, 평균 진입가에서 2% 불리한 곳이에요(닿으면 시장가로 전부 정리, 계좌 약 −40%). 손절된 뒤에는 투표가 반대쪽으로 넘어갈 때까지 쉬어요. 과거 측정에서는 이 방식이 20배로 9~25일 안에 계좌의 99%를 잃었어요. 끄려면 '단타 꺼줘'(/hl_scalp off)."],
        ["예약 N번", "주문 대화창이나 텔레그램 /hl_at 으로 걸어 둔 예약 주문이에요. 가격이 거기에 닿으면 봇이 지정가로 주문해요. 목록은 /hl_list, 취소는 /hl_cancel 번호예요."],
        ["두 코인이 같이 오르면 강제청산 +13%", "두 코인이 지금 가격에서 같은 비율로 이만큼 움직이면 계좌 전체가 강제청산돼요. 숫자가 작을수록 위험해요. 아래 가격은 그때의 코인별 가격이에요."],
        ["펀딩비 (받음+ / 냄−)", "선물에서 롱과 숏이 1시간마다 주고받는 돈을 지금까지 합친 값이에요. +면 받은 돈, −면 낸 돈이에요."],
        [s.live ? "출금 가능" : "현금", s.live ? "포지션 보증금으로 묶이지 않고 남아 있는 돈이에요." : "가상 계좌에 남은 돈이에요(포지션 손익은 빼고)."],
      ]} />
        </div>
      </div>
      {/* 2) 차트 + 판단 사이드 (사이드는 스크롤해도 따라옴) */}
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_400px]">
        <div className="min-w-0 space-y-4">
          <LiveChart d={d} coin={coin} setCoin={setCoin} coins={coins} full={full} setFull={setFull} won={won} />
          <div className="grid items-start gap-4 md:grid-cols-2">
            <WhaleMini w={d.whales} coin={coin} go={goWhale} />
            <Card title="최근 체결" right={<button onClick={() => { setTab("log"); document.getElementById("hl-ref")?.scrollIntoView({ behavior: "smooth" }); }} className="press text-[13px] text-accent">전체 ›</button>}>
              <TradeList trades={d.trades} won={won} n={2} />
            </Card>
          </div>
        </div>
        <aside className="min-w-0 space-y-4 lg:sticky lg:top-[132px]">
          <DeskCard s={s} coin={coin} ai={d.ai} goDesk={goDesk} paper={d.paper && d.inst !== "aipaper"} trader={d.inst === "aipaper"} />
          <TfBoard s={s} coin={coin} />
        </aside>
      </div>
      {/* 3) 참고 정보 탭 */}
      <section id="hl-ref" className="scroll-mt-[140px] rounded-[20px] border border-line bg-card p-4 shadow-card">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <Seg items={REF_TABS} value={tab} onChange={setTab} />
          {tab !== "log" && tab !== "bot" && <span className="text-[13px] text-sub">{COIN_KO[coin] ?? coin} 기준 · 위에서 코인을 바꾸면 같이 바뀌어요</span>}
        </div>
        {tab === "whale" && <><div className="mb-4 rounded-xl bg-chip p-3"><p className="mb-2 text-[14px] font-bold">지금 고래 지표 해설</p><Reads rows={readings(d, coin).whale} cols /></div><WhaleSection w={d.whales} alerts={d.whaleAlerts} coin={coin} krw={krw} /></>}
        {tab === "market" && (
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="rounded-xl bg-chip p-3 lg:col-span-2"><p className="mb-2 text-[14px] font-bold">지금 시장 심리 해설</p><Reads rows={readings(d, coin).market} cols /></div>
            <SentimentCard r={d.sentiment?.[coin]} rows={d.sentimentRows ?? 0} coin={coin} krw={krw} />
            <NewsCard news={d.news} />
          </div>)}
        {tab === "log" && (
          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="사고판 기록"><div className="max-h-[560px] overflow-y-auto pr-1"><TradeList trades={d.trades} won={won} n={50} /></div>
              <Help id="trades" items={[
                ["첫 줄", "무엇을 했는지(숏 진입·숏 늘림·숏 줄임·숏 청산 등), 코인, 수량, 그 금액이에요. '숏 줄임(일부 되사기)'는 숏을 일부 정리했다는 뜻이에요."],
                ["둘째 줄", "체결 시각 · 체결 가격 · 그때 계좌 평가금액이에요. '실현'은 그 체결로 확정된 손익, '수수료'는 거래소에 낸 돈이에요."],
                ["셋째 줄", "봇이 그렇게 한 이유예요. '투표 롱 1·숏 27 → 노출 −0.87 × 배수 5.00'은 의견 −0.87에 배수 5를 곱한 크기로 맞췄다는 뜻이고, '변동성 49%'는 최근 20일 가격 출렁임(연 환산), '지정가 100%'는 전부 지정가(싼 수수료)로 체결됐다는 뜻이에요."],
              ]} /></Card>
            <div className="space-y-4">
              <Card title="평가금액 (달러)">
                {d.equity.length < 2 ? <p className="text-[14px] text-sub">4시간마다 한 점씩 쌓여요.</p> : <SimpleLine rows={d.equity} />}
                <p className="mt-2 text-[12.5px] leading-relaxed text-sub">계좌 돈(남은 돈 + 포지션 손익)이 시간에 따라 어떻게 변했는지예요. 4시간마다 한 점이에요.</p>
              </Card>
              <Card title={`투표 노출 변화 · ${COIN_KO[coin] ?? coin} (+1 전부 롱 · −1 전부 숏)`}>
                {expoMoved ? <SimpleLine rows={expo} color="#3b82f6" />
                  : <p className="text-[14px] text-sub">{expo.length ? `기록 ${expo.length}번 모두 ${expo[0].v > 0 ? "+" : ""}${expo[0].v.toFixed(2)}로 같아서 선을 그리지 않았어요. 값이 바뀌면 그려져요.` : "4시간마다 한 점씩 쌓여요."}</p>}
                <p className="mt-2 text-[12.5px] leading-relaxed text-sub">봇의 최종 의견(메인)이 시간에 따라 어떻게 변했는지예요. 0 위로 올라가면 롱, 아래로 내려가면 숏으로 돌아선 거예요.</p>
              </Card>
            </div>
          </div>)}
        {tab === "bot" && (
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="lg:col-span-2">{d.paper
              ? <Card title="직접 조작"><p className="text-[14px] leading-relaxed text-sub">{d.inst === "aipaper" ? "AI 연습 계좌는 명령을 받지 않아요. AI 판단만으로 가상 매매해요. " : ""}연습용 봇은 텔레그램에서 /hlp_status · /hlp_half · /hlp_wait · /hlp_go · /hlp_stop · /hlp_long · /hlp_short · /hlp_close · /hlp_auto 로 조작해요(확인은 /hlp_yes). 전부 가상 체결이에요. 이 화면의 버튼과 /hl_* 명령은 실계좌 봇('하이퍼리퀴드 롱·숏' 탭)에만 적용돼요.</p></Card>
              : <ControlCard />}</div>
            <Card title="안전장치">
              <ul className="space-y-1 text-[14px] leading-relaxed">
                <li>· {kill ? <>시작 자산 대비 <b>{s.kill_drawdown_pct}%</b> 넘게 줄면({usd(kill)}) 전부 팔고 멈춰요.</> : <>자동 정지선 <b>없음</b> (꺼 둠). 거래소 강제청산만 남아 있어요.</>}</li>
                <li>· {d.paper ? "텔레그램 /hlp_stop 을 보내면" : "위 '전부 정리하고 멈춤'(텔레그램 /hl_stop)을 누르면"} 전량 청산 후 멈춰요. <code className="rounded bg-chip px-1">{d.paper ? "state/HL_PAPER_STOP" : "state/HL_STOP"}</code> 파일을 만들어도 같아요.</li>
                <li>· 이익 보호: 거래 중 최고 이익에서 4시간 종가가 5% 되돌리면 75% 정리, 방향이 바뀔 때까지 25%만 들고 있어요.</li>
                <li>· 재조정 완충 30%: 같은 방향이고 목표와 차이가 30% 미만이면 사고팔지 않아요(수수료 절약).</li>
                <li>· 상태 저장 {ago(s.saved)}{s.halt && <span className="ml-1 font-semibold text-warn">· {s.halt}</span>}</li>
              </ul>
            </Card>
            <Card title="전략">
              <ul className="space-y-1 text-[14px] leading-relaxed">
                <li>· 4시간봉이 닫힐 때(1·5·9·13·17·21시) 분석가(추세·파생·거시·심리)별 의견을 평균해 방향과 크기를 정해요. 봇은 30초마다 잔고를 새로 읽어요.</li>
                <li>· {s.live ? "주문은 전부 지정가로만 걸어요(수수료 0.015%, 시장가는 0.045%). 가장 유리한 호가에 걸고 15초마다 따라가요. 5분 안에 못 채운 것 중 늘리는 몫은 버리고, 줄이는 몫은 30초마다 다시 걸어요. 시장가는 자동 정지선 청산에만 써요." : "연습 모드라 주문은 그 순간 가격으로 가상 체결해요."}</li>
                <li>· 배수 = 목표 변동성 {Math.round((s.target_vol ?? 0.6) * 100)}% ÷ 20일 출렁임, 범위 {s.min_lev ?? 0}~{s.max_lev ?? 3}배.
                  {(s.min_lev ?? 0) >= 2 && <b className="text-warn"> 하한 {s.min_lev}배는 과거 측정에서 {s.min_lev >= 5 ? "몇 달 안에 전액 손실" : "최대 손실폭 89%"}이었어요.</b>}</li>
                <li>· 투표단은 거래가 끝날 때와 매주 월요일, 지표 전부를 새 데이터로 다시 재서 합격한 것만 남겨요{s.signals_updated ? ` (마지막 수정 ${ago(s.signals_updated)})` : ""}.</li>
                {krw && <li>· 원화는 업비트 USDT 가격({krw.toLocaleString("ko-KR")}원/달러) 기준이에요.</li>}
              </ul>
              <Help id="bot" items={[
                ["롱 · 숏", "롱은 가격이 오르면 버는 포지션, 숏은 내리면 버는 포지션이에요."],
                ["배수", "내 돈의 몇 배만큼 포지션을 잡는지예요. 5배면 가격이 1% 불리하게 움직일 때 내 돈이 5% 줄어요."],
                ["목표 변동성 ÷ 20일 출렁임", "최근 20일 가격이 얌전하면 배수를 키우고, 크게 출렁이면 줄이는 계산이에요."],
                ["투표단 · 합격", "과거 데이터로 재서 기준(실전 구간 수익, 이익÷손실 1.2 이상, 3구간 중 2구간 이익, 30회 이상 거래)을 넘은 규칙만 표를 줘요."],
                ["강제청산 · 자동 정지선", "강제청산은 손실이 넣은 돈에 가까워지면 거래소가 포지션을 닫는 것이고, 자동 정지선은 그 전에 봇이 스스로 전부 정리하고 멈추는 선이에요."],
              ]} />
            </Card>
          </div>)}
      </section>
    </main>
  );
}
