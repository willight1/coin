"use client";
// 메인 화면 하나에 전부: 왼쪽 = 상태·지금 상황·차트·AI 판단, 오른쪽 = 사고판 기록·전략 성적·배운 점.
import { CaretDownIcon, PaperPlaneTiltIcon, PauseIcon, PlayIcon, WarningCircleIcon } from "@phosphor-icons/react";
import { useCallback, useEffect, useState } from "react";
import { EquityChart, PriceChart, type Candle } from "@/components/PriceChart";
import { DeskPanel, type DeskData } from "@/components/DeskPanel";
import { HlMoney, HlPanel, Seg, useFetch, usd, type HlData } from "@/components/HlPanel";
import {
  ACTION, DESC, EMOJI, LIB, SOURCE, ago, hhmm, ko, koParams, koSub, pct, tone, won,
} from "@/lib/ko";

type Any = Record<string, any>;
// 받침 있으면 a(을·은), 없으면 b(를·는): 솔라나를, 비트코인을
const josa = (w: string, a: string, b: string) => {
  const c = w.charCodeAt(w.length - 1) - 0xac00;
  return w + (c >= 0 && c < 11172 && c % 28 ? a : b);
};
// 맨 위 탭 목록용: 봇마다 켜져 있는지·돈이 얼마인지 (/api/overview)
type OverviewData = {
  trade: { running: boolean; tick: { time: string; equity: number; krw: number; coin: number; market: string } | null };
  dt: { coin: string; ko: string; running: boolean; equity: number | null; budget: number | null; pos: boolean; live: boolean; halt: string }[];
  hlAi?: { running: boolean; equity: number | null; start: number | null; pos: Record<string, Any> } | null;
  hlPaper?: { running: boolean; equity: number | null; start: number | null; pos: Record<string, Any> } | null;
  hl: { running: boolean; live: boolean; equity: number | null; start: number | null; pos: Record<string, Any>; halt: string; saved: string | null; krw: number | null };
};
type Data = {
  state: Any; running: boolean; inboxPending: boolean; decisions: Any[]; trades: Any[];
  equity: { t: string; equity: number; price: number }[]; lessons: string; review: Any | null;
  coin: string; coinKo: string; coins: { coin: string; ko: string; equity: number | null; pos: boolean }[];
};

const pillCls = { "": "bg-chip text-ink", on: "bg-[#dcfce7] text-[#166534] dark:bg-[#0f2e1d] dark:text-[#4ade80]",
  live: "bg-[#ffe4ec] text-[#be185d] dark:bg-[#3b0d22] dark:text-[#f472b6]", warn: "bg-warn-soft text-warn" };

// ------------------------------------------------------------------ 작은 조각
// 쉬운 설명: easy 칸, 없으면 예전 답 속 "(쉬운 설명: …)" 부분
const easyOf = (easy?: string, tech?: string) => easy || tech?.match(/쉬운 설명[:：]\s*([^)]+)/)?.[1]?.trim() || "";

const Card = ({ title, children, className = "" }: { title?: string; children: React.ReactNode; className?: string }) => (
  <section className={`rounded-[20px] border border-line bg-card p-4 shadow-card sm:p-5 ${className}`}>
    {title && <h2 className="mb-3 text-[15px] font-bold">{title}</h2>}
    {children}
  </section>
);

const Chip = ({ children, kind = "" }: { children: React.ReactNode; kind?: "" | "blue" | "warn" }) => (
  <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-[13px] leading-none ${
    kind === "blue" ? "bg-accent-soft font-semibold text-accent" : kind === "warn" ? "bg-warn-soft text-warn" : "bg-chip text-ink"}`}>
    {children}
  </span>
);

const Ring = ({ on = true, size = 36, children }: { on?: boolean; size?: number; children: React.ReactNode }) => (
  <div className={`shrink-0 rounded-full p-[2.5px] ${on ? "ring-ig" : "bg-line"}`} style={{ width: size, height: size }}>
    <div className="flex h-full w-full items-center justify-center rounded-full border-[2.5px] border-card bg-card"
      style={{ fontSize: size * 0.42 }}>{children}</div>
  </div>
);

const Row = ({ k, v }: { k: string; v: React.ReactNode }) => (
  <div className="flex items-start justify-between gap-4 py-[3px] text-[14px]">
    <span className="shrink-0 text-sub">{k}</span><span className="text-right">{v}</span>
  </div>
);

// ------------------------------------------------------------------ 위쪽
// 맨 위 봇 전환 바 — 봇마다 켜짐 점·평가금액·보유를 같이 보여준다 (예전 '봇 3개 요약 줄' 대신)
type Tab = { coin: string; ko: string; pos: boolean; on?: boolean; sub?: string; off?: boolean };
function Tabs({ tabs, coin, setCoin }: { tabs: Tab[]; coin: string; setCoin: (c: string) => void }) {
  return (
    <nav className="no-scrollbar mx-auto flex max-w-[2400px] gap-1.5 overflow-x-auto px-4 pt-3">
      {tabs.map((c) => {
        const act = c.coin === coin;
        return (
          <button key={c.coin} onClick={() => !c.off && setCoin(c.coin)} disabled={c.off}
            className={`press shrink-0 rounded-2xl px-3 py-1.5 text-left ${act ? "bg-ink text-bg" : "bg-chip"} ${c.off ? "cursor-default" : ""}`}>
            <div className={`flex items-center gap-1.5 text-[14px] ${act ? "font-semibold" : ""}`}>
              {c.on != null && <span className={`inline-block h-1.5 w-1.5 rounded-full ${c.on ? "bg-good" : "bg-bad"}`} />}{c.ko}{c.pos && <span className="text-[12.5px] opacity-70">· 보유 중</span>}</div>
            {c.sub && <div className={`text-[12.5px] tabular-nums ${act ? "opacity-80" : "text-sub"}`}>{c.sub}</div>}
          </button>);
      })}
    </nav>
  );
}

function Header({ d, tabs, onCmd, setCoin }: { d: Data; tabs: Tab[]; onCmd: (a: string) => void; setCoin: (c: string) => void }) {
  const s = d.state;
  const [confirm, setConfirm] = useState(false);
  const pills: [string, "" | "on" | "live" | "warn"][] = [
    [d.running ? "실행 중" : "꺼져 있음", d.running ? "on" : ""],
    [s.live ? "실제 돈으로 거래" : "연습 모드", s.live ? "live" : ""],
  ];
  if (s.llm_busy_since) pills.push(["AI가 생각 중", ""]);
  const route = String(s.llm_route_now ?? ""), primary = s.llm_primary ?? "claude_cli";
  if (route && route !== primary) pills.push([route === "api" ? "구독 한도 초과, API 키로 판단 중"
    : (s.api_total_usd_all ?? s.llm?.api_total_usd ?? 0) >= (s.api_total_budget_usd || Infinity) ? "API 예산 다 써서 구독으로 판단 중" : "API 연결 문제로 잠시 구독으로 판단 중", "warn"]);
  for (const x of [s.halt, s.weekly_stop && "7일 손실 한도로 멈춤", s.stop_file && "새로 사지 않기 켜짐"]) if (x) pills.push([x, "warn"]);
  const btn = "press inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-line bg-card px-3.5 py-2 text-[14px] font-semibold hover:bg-chip";
  return (
    <header className="sticky top-0 z-20 border-b border-line bg-bg/85 backdrop-blur">
      <Tabs tabs={tabs} coin={d.coin} setCoin={setCoin} />
      <div className="mx-auto flex max-w-[2400px] flex-wrap items-center gap-3 px-4 py-3">
        <Ring on={d.running} size={40}><span className="font-extrabold text-[#f7931a]">{d.coin === "BTC" ? "₿" : d.coin.slice(0, 3)}</span></Ring>
        <div className="min-w-0">
          <div className="text-[16.5px] font-bold">{d.coinKo} 자동매매 AI</div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {pills.map(([t, k]) => <span key={t} className={`rounded-full px-2.5 py-[3px] text-[12.5px] font-semibold ${pillCls[k]}`}>{t}</span>)}
          </div>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <button className={btn} onClick={() => onCmd("stop")} title="모든 코인: 이미 산 코인은 그대로 관리하고, 새 매수만 멈춥니다">
            <PauseIcon size={14} weight="bold" />새로 사지 않기</button>
          <button className={btn} onClick={() => onCmd("go")} title="멈춤 상태를 모두 풀고 다시 거래합니다">
            <PlayIcon size={14} weight="bold" />다시 시작</button>
          <button className={btn} onClick={() => onCmd("status")} title="지금 상태를 텔레그램으로 보내 줍니다">
            <PaperPlaneTiltIcon size={14} weight="bold" />텔레그램으로 상태</button>
          <div className="relative">
            <button className={`${btn} border-bad/30 text-bad`} onClick={() => setConfirm((x) => !x)}>
              전부 팔기<CaretDownIcon size={12} weight="bold" /></button>
            {confirm && (
              <div className="fade-in absolute right-0 top-11 z-30 w-64 rounded-[20px] border border-line bg-card p-4 shadow-card">
                <p className="text-[14.5px] leading-relaxed"><b>모든 코인 봇</b>이 가진 코인을 지금 시장가로 <b>전부 팔고</b>, 새 매수도 멈춥니다.</p>
                <div className="mt-3 flex gap-2">
                  <button className="press flex-1 rounded-full bg-bad px-3 py-2 text-[14px] font-semibold text-white"
                    onClick={() => { onCmd("flat"); setConfirm(false); }}>네, 전부 팔게요</button>
                  <button className="press rounded-full border border-line px-3 py-2 text-[14px]" onClick={() => setConfirm(false)}>취소</button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}

function Stats({ s }: { s: Any }) {
  const budget = s.budget_krw || 0;
  const eq = s.equity || 0;
  const total = budget ? (eq / budget - 1) * 100 : 0;
  const l = s.llm ?? {};
  const items: [React.ReactNode, string][] = [
    [won(eq, false), "지금 평가금액"],
    [<span className={tone(total)}>{pct(total)}</span>, "처음 예산 대비"],
    [<span className={tone(s.pnl_today)}>{won(s.pnl_today)}</span>, "오늘 손익"],
    [<span className={tone(s.pnl_7d)}>{won(s.pnl_7d)}</span>, "7일 손익"],
    [`${s.day?.buys ?? 0}번`, s.max_buys_per_day ? `오늘 매수 (최대 ${s.max_buys_per_day}번)` : "오늘 매수 (제한 없음)"],
    [`${(l.quick ?? 0) + (l.deep ?? 0)}번`, "오늘 AI 판단"],
  ];
  return (
    <Card>
      <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-6">
        {items.map(([v, k], i) => (
          <div key={i}><div className="text-[16.5px] font-bold tabular-nums">{v}</div><div className="mt-0.5 text-[13px] text-sub">{k}</div></div>
        ))}
      </div>
    </Card>
  );
}

function Stories({ s }: { s: Any }) {
  const sb = s.scoreboard ?? {};
  const cur = s.plan?.strategy, held = s.position?.strategy;
  return (
    <div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(70px,1fr))] gap-x-2 gap-y-3 rounded-[20px] border border-line bg-card px-3 py-3.5 shadow-card">
        {[...LIB, "hold"].map((sid) => {
          const v = sb[sid] ?? {};
          const r = v.d30?.ret as number | undefined;
          const using = sid === cur || sid === held;
          return (
            <div key={sid} className="text-center" title={DESC[sid]}>
              <div className="flex justify-center"><Ring on={using || !!v.entry_signal_now} size={56}>{EMOJI[sid]}</Ring></div>
              <div className={`mt-1.5 text-[12.5px] leading-tight ${using ? "font-bold" : ""}`}>{ko(sid)}</div>
              <div className={`mt-0.5 text-[12.5px] font-semibold ${r == null ? "text-sub" : tone(r)}`}>{r == null ? " " : pct(r, 1)}</div>
            </div>
          );
        })}
      </div>
      <p className="mx-1 mt-1.5 text-[13px] leading-relaxed text-sub">
        색깔 링은 지금 쓰고 있거나 지금 살 신호가 난 전략이에요. 숫자는 최근 30일 동안 그 전략대로 했다면 얻었을 수익률이에요 (실제 주문 없이 계산).
      </p>
    </div>
  );
}

function Now({ s, last, coinKo }: { s: Any; last?: Any; coinKo: string }) {
  const p = s.position, pl = s.plan ?? {};
  const conf = Math.round((pl.confidence ?? 0) * 100);
  const easy = easyOf(last?.decision?.easy || pl.easy, last?.decision?.reason || pl.reason);
  const reason = easy || last?.decision?.reason || pl.reason;
  let head: React.ReactNode;
  if (p) {
    const upct = p.cost_krw ? ((s.price * 0.9995 * p.volume) / p.cost_krw - 1) * 100 : 0;
    const held = (Date.now() - Date.parse(p.entry_time)) / 3.6e6;
    head = <>{josa(coinKo, "을", "를")} <b>{won(p.cost_krw, false)}</b>어치 들고 있어요. 지금{" "}
      <b className={tone(upct)}>{pct(upct)} ({won(s.unrealized)})</b>, 산 지 {held.toFixed(1)}시간 됐어요.</>;
  } else {
    head = <>{josa(coinKo, "은", "는")} 없고 <b>현금 {won(s.equity ?? 0, false)}</b>으로 기다리는 중이에요.</>;
  }
  const plan = !pl.strategy || pl.strategy === "none"
    ? <>AI는 지금 <b>사지 않고 기다리기</b>로 정했어요. (확신 {conf}%)</>
    : <>AI는 지금 <b>{EMOJI[pl.strategy]} {ko(pl.strategy)}</b> 전략을 쓰고 있어요. (확신 {conf}%) 신호가 나면 예산의 {Math.round(pl.size_pct)}%로 사고, {pl.stop_loss_pct}% 떨어지면 손절해요.</>;
  return (
    <Card title="지금 상황">
      <p className="text-[15.5px] leading-relaxed">{head}<br />{plan}</p>
      {reason && <p className="mt-2 text-[14.5px] leading-relaxed">💬 {easy ? reason : koSub(reason)} <span className="text-[13px] text-sub">({ago(last?.time)})</span></p>}
      <div className="mt-3 border-t border-line pt-2">
        {p && <>
          <Row k="산 가격" v={won(p.entry_price, false)} />
          <Row k="관리 전략" v={ko(p.strategy)} />
          <Row k="산 계기" v={SOURCE[p.source] ?? p.source} />
          <Row k="손절가" v={p.stop_price ? won(p.stop_price, false) : "없음 (전체 손실 한도만 적용)"} />
        </>}
        {pl.strategy && pl.strategy !== "none" && <Row k="전략 설정" v={koParams(pl.params)} />}
        {s.pending && <Row k="걸어 둔 주문" v={`${s.pending.price.toLocaleString("ko-KR")}원에 ${s.pending.side === "bid" ? "매수" : "매도"} 대기 (${ago(s.pending.placed_at)})`} />}
        <Row k="다음 AI 점검" v={`${hhmm(s.next_check_at)} (${s.next_interval_min ?? "-"}분마다)`} />
        <Row k={`${coinKo} 지금 가격`} v={won(s.price ?? 0, false)} />
      </div>
    </Card>
  );
}

function Chart({ d }: { d: Data }) {
  const [unit, setUnit] = useState(60);
  const { data: candles, error } = useFetch<Candle[]>(`/api/candles?unit=${unit}&coin=${d.coin}`, 15000);
  const p = d.state.position;
  return (
    <Card>
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-[15px] font-bold">가격과 매매 위치</h2>
        <Seg items={[["15", "15분"], ["60", "1시간"], ["240", "4시간"], ["1440", "하루"]]} value={String(unit)} onChange={(v) => setUnit(+v)} />
      </div>
      {error ? <p className="py-16 text-center text-[14.5px] text-sub">가격을 불러오지 못했어요: {error}</p>
        : !candles ? <div className="h-[300px] animate-pulse rounded-2xl bg-chip" />
        : <PriceChart candles={candles} trades={d.trades} entry={p?.entry_price} stop={p?.stop_price} />}
      <p className="mt-1 text-[13px] text-sub">빨간 봉은 오른 구간, 파란 봉은 내린 구간이에요. 화살표는 AI가 실제로 사고판 곳이에요.</p>
      {d.equity.length > 1 && <>
        <div className="mt-4 text-[14px] font-semibold">내 돈은 어떻게 변했나 <span className="font-normal text-sub">(분홍 AI · 회색 점선 그냥 {josa(d.coinKo, "을", "를")} 샀다면)</span></div>
        <EquityChart rows={d.equity} />
      </>}
    </Card>
  );
}

// ------------------------------------------------------------------ AI 판단 피드
function Post({ r }: { r: Any }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<Any | null>(null);
  const dd = r.decision, q = r.quick ?? {};
  const deep = !!dd;
  const calls: Any[] = r.calls ?? [];
  const sec = calls.reduce((a, c) => a + (c.sec ?? 0), 0);
  const route = calls.some((c) => c.route === "api") ? "API" : "구독";
  const tech = deep ? dd.reason : q.reason;
  const easy = easyOf(deep ? dd.easy : q.easy, tech);
  const body = easy || koSub(tech) || (deep ? "판단을 읽지 못해 그대로 유지했어요" : "점검 결과를 읽지 못했어요");
  const toggle = async () => {
    setOpen((x) => !x);
    if (!detail) {
      const j = await (await fetch(`/api/decision?time=${encodeURIComponent(r.time)}&coin=${r.coin ?? "BTC"}`)).json();
      setDetail(j);
    }
  };
  return (
    <article className="fade-in rounded-[20px] border border-line bg-card px-4 py-3.5 shadow-card">
      <div className="mb-2 flex items-center gap-2.5">
        <Ring on={deep} size={36}>{deep ? "🧠" : "👀"}</Ring>
        <div className="min-w-0">
          <div className="text-[14.5px] font-bold">{deep ? "AI 깊은 판단" : "AI 빠른 점검"}</div>
          <div className="text-[13px] text-sub">{ago(r.time)} · {hhmm(r.time)} · {won(r.price ?? 0, false)} · 계기: {(r.wake ?? []).join(", ")}</div>
        </div>
      </div>
      <p className="text-[15px] leading-relaxed">{body}</p>
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {deep ? <>
          <Chip>{dd.regime || "흐름 판단 없음"}</Chip>
          <Chip>{EMOJI[r.plan_after?.strategy] ?? ""} {ko(r.plan_after?.strategy)}</Chip>
          <Chip kind={["buy_now", "sell_now"].includes(dd.action) ? "blue" : ""}>{ACTION[dd.action] ?? dd.action}</Chip>
          <Chip>확신 {Math.round((dd.confidence ?? 0) * 100)}%</Chip>
        </> : <Chip kind={q.verdict === "escalate" || q.urgent_sell ? "blue" : ""}>{q.verdict === "escalate" || q.urgent_sell ? "깊은 판단 요청" : "지금 전략 그대로"}</Chip>}
        {q.urgent_sell && <Chip kind="warn">급하게 팔지 검토</Chip>}
        {(r.rejected ?? []).map((x: string) => <Chip key={x} kind="warn">막힘: {x}</Chip>)}
        {r.deep_skipped && <Chip kind="warn">{r.deep_skipped}</Chip>}
        <Chip>{route} {sec.toFixed(0)}초</Chip>
        <button onClick={toggle} className="press ml-auto text-[13px] font-semibold text-accent">{open ? "접기" : "자세한 근거"}</button>
      </div>
      {open && (
        <div className="mt-3 space-y-2">
          {easy && tech && <p className="rounded-2xl bg-chip p-3 text-[13.5px] leading-relaxed text-sub">전문가용 근거: {koSub(tech)}</p>}
          {!detail ? <div className="h-16 animate-pulse rounded-2xl bg-chip" /> : <>
            <details className="rounded-2xl bg-chip p-3 text-[13px]"><summary className="cursor-pointer text-sub">AI가 쓴 답 원문</summary><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap leading-relaxed">{detail.raw ?? JSON.stringify(detail.quick, null, 1)}</pre></details>
            {detail.notes?.length > 0 && <p className="text-[13px] text-sub">범위를 벗어나 고친 값: {detail.notes.join(", ")}</p>}
            <details className="rounded-2xl bg-chip p-3 text-[13px]">
              <summary className="cursor-pointer text-sub">AI가 본 시장 자료 ({(detail.snapshot_chars ?? 0).toLocaleString("ko-KR")}자)</summary>
              <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap">{JSON.stringify(detail.snapshot, null, 1)}</pre>
            </details>
          </>}
        </div>
      )}
    </article>
  );
}

function Feed({ d }: { d: Data }) {
  const [onlyDeep, setOnlyDeep] = useState(false);
  const [more, setMore] = useState(false);
  const list = d.decisions.filter((r) => r.decision || !onlyDeep);
  if (!d.decisions.length) {
    return <Card><p className="text-[15.5px]">아직 AI 판단 기록이 없어요.</p><p className="mt-1 text-[14px] text-sub">봇이 켜지면 바로 첫 판단을 하고, 그 뒤로 1~5분마다 점검해요.</p></Card>;
  }
  return (
    <div className="space-y-2.5">
      <div className="flex items-center justify-between px-1">
        <h2 className="text-[15.5px] font-bold">AI 판단</h2>
        <label className="flex cursor-pointer items-center gap-2 text-[14px] text-sub">
          <input type="checkbox" className="accent-[var(--accent)]" checked={onlyDeep} onChange={(e) => setOnlyDeep(e.target.checked)} />
          깊은 판단만 보기
        </label>
      </div>
      {list.slice(0, more ? 40 : 6).map((r) => <Post key={r.time + (r.kind ?? "")} r={r} />)}
      {list.length > 6 && (
        <button onClick={() => setMore((x) => !x)} className="press w-full rounded-full border border-line bg-card py-2.5 text-[14px] font-semibold text-sub">
          {more ? "접기" : `이전 판단 ${Math.min(list.length, 40) - 6}개 더 보기`}</button>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ 오른쪽 칸
function Trades({ d }: { d: Data }) {
  const tr = d.trades;
  const sells = tr.filter((t) => t.side === "매도");
  const realized = sells.reduce((a, t) => a + +t.pnl_krw, 0);
  const fee = tr.reduce((a, t) => a + +t.fee, 0);
  const rc: Any = d.state.reject_counts ?? {};
  return (
    <Card title="사고판 기록">
      {!tr.length ? <>
        <p className="text-[15px]">아직 사고판 기록이 없어요.</p>
        <p className="mt-1 text-[13.5px] leading-relaxed text-sub">AI가 살 만한 신호를 기다리는 중이에요. 하락장에서는 며칠 동안 안 살 수도 있어요.</p>
      </> : <>
        <div className="mb-3 flex flex-wrap gap-1.5">
          <Chip>판 횟수 {sells.length}번</Chip>
          <Chip>이긴 비율 {sells.length ? Math.round((sells.filter((t) => +t.pnl_krw > 0).length / sells.length) * 100) : 0}%</Chip>
          <Chip>확정 손익 <b className={`ml-1 ${tone(realized)}`}>{won(realized)}</b></Chip>
          <Chip>수수료 {won(fee, false)}</Chip>
        </div>
        <ul className="space-y-2.5">
          {tr.slice(0, 30).map((t) => {
            const buy = t.side === "매수";
            return (
              <li key={t.time + t.side} className="flex gap-2.5">
                <Ring on={buy} size={32}>{buy ? "🟢" : "🔵"}</Ring>
                <div className="min-w-0 flex-1">
                  <div className="text-[14.5px] font-bold">{t.side} {(+t.krw).toLocaleString("ko-KR")}원
                    {!buy && <span className={`ml-1.5 ${tone(+t.pnl_krw)}`}>{won(+t.pnl_krw)}</span>}</div>
                  <div className="text-[13px] text-sub">{t.time.slice(5, 16).replace("T", " ")} · 평균 {(+t.avg_price).toLocaleString("ko-KR")}원 · {ko(t.strategy)} · {SOURCE[t.source] ?? t.source}</div>
                  <p className="mt-0.5 text-[13.5px] leading-relaxed">{koSub(t.reason)}</p>
                </div>
              </li>
            );
          })}
        </ul>
      </>}
      {Object.keys(rc).length > 0 && (
        <div className="mt-4 border-t border-line pt-3">
          <div className="mb-1 text-[14px] font-semibold">안전장치가 막은 매수</div>
          {Object.entries(rc).sort((a, b) => (b[1] as number) - (a[1] as number)).slice(0, 6).map(([k, v]) => <Row key={k} k={k} v={`${v}번`} />)}
        </div>
      )}
    </Card>
  );
}

function Scores({ s }: { s: Any }) {
  const sb = s.scoreboard ?? {};
  if (!Object.keys(sb).length) return null;
  const cur = s.plan?.strategy;
  return (
    <Card title="전략 성적">
      <div className="grid grid-cols-[1fr_auto_auto] gap-x-3 gap-y-1.5 text-[14px]">
        <span className="text-[12.5px] text-sub">각 전략대로 했다면</span><span className="text-right text-[12.5px] text-sub">30일</span><span className="text-right text-[12.5px] text-sub">90일</span>
        {[...LIB, "hold"].filter((sid) => sb[sid]).map((sid) => {
          const a = sb[sid].d30?.ret, b = sb[sid].d90?.ret;
          return [
            <span key={sid + "n"} title={DESC[sid]} className="flex flex-wrap items-center gap-1">{EMOJI[sid]} {ko(sid)}
              {sid === cur && <Chip kind="blue">사용 중</Chip>}{sb[sid].entry_signal_now && <Chip kind="blue">살 신호</Chip>}</span>,
            <b key={sid + "a"} className={`text-right tabular-nums ${tone(a)}`}>{pct(a, 1)}</b>,
            <b key={sid + "b"} className={`text-right tabular-nums ${tone(b)}`}>{pct(b, 1)}</b>,
          ];
        })}
      </div>
      <p className="mt-3 text-[13px] text-sub">실제 주문 없이 계산했어요. 한 번 사고팔 때마다 수수료·체결 차이로 0.1%씩 뺐어요. 이름에 마우스를 올리면 규칙이 보여요.</p>
    </Card>
  );
}

function Usage({ s }: { s: Any }) {
  const l = s.llm ?? {}, lim = s.sub_limits ?? {};
  const tok = (x?: number) => (x ?? 0).toLocaleString("ko-KR");
  const reset = (t?: number) => t ? new Date(t * 1000).toLocaleString("ko-KR",
    { timeZone: "Asia/Seoul", month: "numeric", day: "numeric", weekday: "short", hour: "2-digit", minute: "2-digit" }) : "-";
  const cost = (Object.values(l.routes ?? {}) as Any[]).reduce((a, r) => a + (r.cost_usd ?? 0), 0);
  const apiCost = l.routes?.api?.cost_usd ?? 0;
  const bar = (name: string, label: string) => {
    const w = lim[name];
    if (!w) return null;
    const hi = w.pct >= 80;
    return (
      <div className="mb-2.5">
        <div className="flex justify-between text-[14px]"><span>{label}</span>
          <b className={hi ? "text-warn" : ""}>{Math.round(w.pct)}% 사용</b></div>
        <div className="mt-1 h-2 overflow-hidden rounded-full bg-chip">
          <div className={`h-full rounded-full ${hi ? "bg-warn" : "bg-accent"}`} style={{ width: `${Math.min(w.pct, 100)}%` }} /></div>
        <div className="mt-0.5 text-[13px] text-sub">{reset(w.resets_at)}에 다시 채워져요</div>
      </div>
    );
  };
  const spent = s.api_total_usd_all ?? l.api_total_usd ?? 0, budget = s.api_total_budget_usd ?? 0;
  const apiFirst = s.llm_primary === "api";
  return (
    <Card title="AI 사용량">
      {apiFirst && budget > 0 && (
        <div className="mb-3">
          <div className="flex justify-between text-[14px]"><span>API 예산</span>
            <b>${spent.toFixed(2)} / ${budget.toFixed(0)}</b></div>
          <div className="mt-1 h-2 overflow-hidden rounded-full bg-chip">
            <div className={`h-full rounded-full ${spent / budget >= 0.8 ? "bg-warn" : "bg-accent"}`} style={{ width: `${Math.min(spent / budget * 100, 100)}%` }} /></div>
          <div className="mt-0.5 text-[13px] text-sub">{spent >= budget ? "다 써서 지금은 구독으로 판단해요" : `남은 $${(budget - spent).toFixed(2)} 다 쓰면 구독으로 바꿔요`}</div>
          <div className="mt-2 rounded-2xl bg-chip px-3 py-2">
            <Row k="API 키로 판단한 횟수 (누적)" v={`${tok(l.api_total_calls)}번`} />
            <Row k="읽은 / 쓴 글자 조각 (누적)" v={`${tok(l.api_total_in)} / ${tok(l.api_total_out)}개`} />
            <Row k="원화로 약" v={won(spent * 1450, false)} />
            <Row k="마지막 사용" v={l.api_last_at ? ago(l.api_last_at) : "아직 없음"} />
          </div>
        </div>
      )}
      {lim.five_hour || lim.seven_day ? <>
        {bar("five_hour", "구독 5시간 한도")}
        {bar("seven_day", "구독 일주일 한도")}
        <p className="mb-3 text-[13px] leading-relaxed text-sub">
          내가 Claude를 쓴 것도 같이 들어간 숫자예요.{apiFirst ? " AI가 API로 판단하는 동안은 갱신되지 않아요." : " 한도가 차면 AI는 API 키로 넘어가요."} ({ago(lim.at)} 확인)</p>
      </> : <p className="mb-3 text-[14px] text-sub">구독 한도는 AI가 다음에 판단할 때 받아 와요.</p>}
      <div className="border-t border-line pt-2">
        <Row k="오늘 AI 호출" v={`${tok(l.calls)}번 (빠른 점검 ${tok(l.quick)} · 깊은 판단 ${tok(l.deep)})`} />
        <Row k="AI가 읽은 글자 조각" v={`${tok((l.in ?? 0) + (l.cache_creation ?? 0) + (l.cache_read ?? 0))}개`} />
        <Row k="그중 다시 읽기(싸게 처리)" v={`${tok(l.cache_read)}개`} />
        <Row k="AI가 쓴 글자 조각" v={`${tok(l.out)}개`} />
        <Row k="API였다면 오늘 비용" v={`약 ${(cost * 1450).toLocaleString("ko-KR", { maximumFractionDigits: 0 })}원 ($${cost.toFixed(2)})`} />
        <Row k="오늘 실제로 낸 API 비용" v={apiCost ? `$${apiCost.toFixed(2)}` : "0원 (전부 구독으로 처리)"} />
      </div>
      <p className="mt-2 text-[13px] leading-relaxed text-sub">글자 조각(토큰)은 AI가 글을 읽고 쓰는 단위예요. 한글 한 글자가 대략 1~2개예요. 오늘은 아침 9시부터 세요.</p>
    </Card>
  );
}

function Lessons({ d }: { d: Data }) {
  const r = d.review?.review;
  return (
    <Card title="AI가 배운 점">
      <p className="whitespace-pre-wrap text-[14px] leading-relaxed text-sub">
        {d.lessons ? koSub(d.lessons) : "아직 없어요. 매일 아침 9시 5분에 AI가 어제 판단을 돌아보고 여기에 적어요."}</p>
      {r && (
        <div className="mt-3 space-y-2 border-t border-line pt-3 text-[14px] leading-relaxed">
          <div className="font-semibold">어제 돌아보기 ({String(d.review?.time).slice(5, 10)})</div>
          <div><span className="text-good">잘한 판단</span><ul className="ml-4 list-disc text-sub">{(r.good ?? []).map((x: string, i: number) => <li key={i}>{koSub(x)}</li>)}</ul></div>
          <div><span className="text-bad">아쉬운 판단</span><ul className="ml-4 list-disc text-sub">{(r.bad ?? []).map((x: string, i: number) => <li key={i}>{koSub(x)}</li>)}</ul></div>
          {r.strategy_notes && <p className="text-sub">{koSub(r.strategy_notes)}</p>}
        </div>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ 페이지
export default function Dashboard() {
  const [coin, setCoinState] = useState("BTC");
  useEffect(() => { try { setCoinState(localStorage.getItem("coin") ?? "BTC"); } catch { /* 저장소 없음 */ } }, []);
  const setCoin = useCallback((c: string) => { setCoinState(c); try { localStorage.setItem("coin", c); } catch { /* */ } }, []);
  const inst = coin === "HLP" ? "paper" : coin === "HLA" ? "aipaper" : "";   // HLP = 연습용 쌍둥이, HLA = AI 연습 계좌
  const isPaper = !!inst, isHl = coin === "HL" || isPaper, isDesk = coin === "DESK";   // HLP = 하이퍼리퀴드 연습용 쌍둥이 (같은 화면, 다른 파일)
  const { data: raw, error } = useFetch<Data>(isHl || isDesk ? "" : `/api/dashboard?coin=${coin}`, 5000);
  const { data: ov } = useFetch<OverviewData>("/api/overview", 5000);
  const { data: hlRaw, error: hlErr } = useFetch<HlData>(isHl ? (isPaper ? `/api/hl?inst=${inst}` : "/api/hl") : "", 5000);
  const { data: desk, error: deskErr } = useFetch<DeskData>(isDesk ? "/api/hl/desk" : "", 5000);
  const hl = isDesk ? (desk as HlData | null) : hlRaw, hlError = isDesk ? deskErr : hlErr;      // 헤더(평가금액 등)는 둘 다 state·krw·running 만 쓴다
  const won = (x?: number | null) => (x == null ? "" : `${Math.round(x).toLocaleString("ko-KR")}원`);
  const t50 = ov?.trade.tick;
  const usdSub = (x?: { equity: number | null; start: number | null } | null) => {
    if (x?.equity == null) return undefined;
    return usd(x.equity) + (x.start ? ` (${pct((x.equity / x.start - 1) * 100, 1)})` : "");
  };
  const tabs: Tab[] = [
    ...(ov ? [{ coin: "SMA50", ko: "업비트 50일선", pos: (t50?.coin ?? 0) > 0, on: ov.trade.running, sub: t50 ? won(t50.equity) : "기록 없음", off: true }] : []),
    ...(ov?.dt.map((x) => ({ coin: x.coin, ko: `업비트 AI · ${x.ko}`, pos: x.pos, on: x.running, sub: won(x.equity) })) ?? raw?.coins ?? [{ coin: "BTC", ko: "비트코인", pos: false }]),
    { coin: "HL", ko: "하이퍼리퀴드 롱·숏", pos: Object.keys(ov?.hl.pos ?? {}).length > 0, on: ov?.hl.running,
      sub: usdSub(ov?.hl) },
    ...(ov?.hlPaper ? [{ coin: "HLP", ko: "하이퍼리퀴드 연습용", pos: Object.keys(ov.hlPaper.pos).length > 0, on: ov.hlPaper.running, sub: usdSub(ov.hlPaper) }] : []),
    ...(ov?.hlAi ? [{ coin: "HLA", ko: "AI 연습 계좌", pos: Object.keys(ov.hlAi.pos).length > 0, on: ov.hlAi.running, sub: usdSub(ov.hlAi) }] : []),
    { coin: "DESK", ko: "에이전트 상황실", pos: false, on: ov?.hl.running, sub: "분석가 → 메인 → 주문" }];
  // 판단 펼치기(/api/decision)가 어느 코인 기록인지 알도록
  const d = raw && { ...raw, decisions: raw.decisions.map((r): Any => ({ ...r, coin: raw.coin })) };
  const [toast, setToast] = useState("");
  const onCmd = useCallback(async (action: string) => {
    const label: Record<string, string> = { stop: "새 매수를 멈춥니다", go: "다시 시작합니다", status: "텔레그램으로 보냅니다", flat: "전부 팝니다" };
    try {
      const r = await fetch("/api/command", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action }) });
      setToast(r.ok ? `10초 안에 ${label[action]}` : "명령을 보내지 못했어요");
    } catch {
      setToast("명령을 보내지 못했어요");
    }
    setTimeout(() => setToast(""), 3500);
  }, []);

  if (isHl || isDesk) {
    const hs = hl?.state ?? {};
    return (
      <>
        <header className="sticky top-0 z-20 border-b border-line bg-bg/85 backdrop-blur">
          <Tabs tabs={tabs} coin={coin} setCoin={setCoin} />
          <div className="mx-auto flex max-w-[2400px] flex-wrap items-center gap-3 px-4 py-3">
            <Ring on={!!hl?.running} size={40}><span className="font-extrabold text-[#50d2c1]">HL</span></Ring>
            <div className="min-w-0">
              <div className="text-[16.5px] font-bold">{isDesk ? "에이전트 상황실 · 하이퍼리퀴드 봇" : inst === "aipaper" ? "AI 연습 계좌 (가상 돈, AI 가 15분마다 판단)" : isPaper ? "하이퍼리퀴드 연습용 봇 (가상 돈)" : "하이퍼리퀴드 롱·숏 추세 봇"}</div>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {([[hl?.running ? "실행 중" : "꺼져 있음", pillCls[hl?.running ? "on" : ""]],
                  [hs.live ? "실제 돈으로 거래" : "연습 모드 (진짜 돈 없음)", pillCls[hs.live ? "live" : ""]],
                  ...(hs.halt ? [[hs.halt, pillCls.warn]] : [])] as [string, string][])
                  .map(([t, c]) => <span key={t} className={`rounded-full px-2.5 py-[3px] text-[12.5px] font-semibold ${c}`}>{t}</span>)}
              </div>
            </div>
            {hl && <div className="w-full min-w-0 lg:ml-auto lg:w-auto"><HlMoney d={hl} /></div>}
          </div>
        </header>
        {isDesk && desk ? <DeskPanel d={desk} goHl={() => setCoin("HL")} /> : !isDesk && hl ? <HlPanel key={coin} d={hl} goDesk={isPaper ? undefined : () => setCoin("DESK")} /> : <main className="mx-auto max-w-[2400px] px-4 py-6">{hlError
          ? <Card><p className="text-[14.5px] text-warn">하이퍼리퀴드 봇 정보를 읽지 못했어요: {hlError}</p></Card>
          : <div className="animate-pulse rounded-[20px] bg-chip" style={{ height: 240 }} />}</main>}
      </>
    );
  }
  if (!d) {
    return (
      <main className="mx-auto max-w-[2400px] space-y-3 px-4 py-6">
        {error ? <Card><p className="flex items-center gap-2 text-[15.5px]"><WarningCircleIcon size={18} className="text-warn" />봇 정보를 읽지 못했어요</p>
          <p className="mt-1 text-[14px] text-sub">{error}</p></Card>
          : [120, 90, 220, 300].map((h, i) => <div key={i} className="animate-pulse rounded-[20px] bg-chip" style={{ height: h }} />)}
      </main>
    );
  }
  const s = d.state;
  const stale = d.running && s.updated_at && Date.now() - Date.parse(s.updated_at) > 90_000;
  return (
    <>
      <Header d={d} tabs={tabs} onCmd={onCmd} setCoin={setCoin} />
      <main className="mx-auto grid max-w-[2400px] gap-5 px-4 py-5 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="min-w-0 space-y-4">
          {!s.updated_at && <Card><p className="text-[15.5px]">봇이 아직 한 번도 실행되지 않았어요.</p></Card>}
          {stale && <Card className="border-warn/40"><p className="flex items-center gap-2 text-[14.5px] text-warn"><WarningCircleIcon size={16} />화면 정보가 90초 넘게 새로 오지 않았어요. AI 판단이 오래 걸리거나 봇이 멈췄을 수 있어요.</p></Card>}
          {error && <p className="px-1 text-[13.5px] text-warn">새로고침 실패: {error} (마지막으로 받은 정보를 보여 주는 중)</p>}
          <Stats s={s} />
          <Stories s={s} />
          <Now s={s} last={d.decisions.find((r) => r.decision)} coinKo={d.coinKo} />
          <Chart d={d} />
          <Feed d={d} />
        </div>
        <aside className="no-scrollbar min-w-0 space-y-4 lg:sticky lg:top-[84px] lg:max-h-[calc(100dvh-100px)] lg:self-start lg:overflow-y-auto">
          <Trades d={d} />
          <Usage s={s} />
          <Scores s={s} />
          <Lessons d={d} />
          {d.inboxPending && !d.running && <p className="px-1 text-[13.5px] text-warn">보낸 명령이 기다리는 중이에요. 봇이 꺼져 있어서 켜지면 처리돼요.</p>}
        </aside>
      </main>
      {toast && <div className="fade-in fixed bottom-6 left-1/2 z-40 -translate-x-1/2 rounded-full bg-ink px-4 py-2.5 text-[14.5px] font-semibold text-bg shadow-card">{toast}</div>}
    </>
  );
}
