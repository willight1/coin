"use client";
// 에이전트 상황실 — 하이퍼리퀴드 봇의 분석가(서브에이전트) → 메인 → 안전장치 → 주문 흐름과 AI 운용역(그림자)이 돌아가는 모습.
// 데이터: /api/hl/desk (state/hl_live.json, logs/hl_desk.jsonl, logs/hl_ai_shadow.csv 등). 보기만 하는 화면이고 조작은 하이퍼리퀴드 탭 '봇 설정'에 있다.
import { ArrowDownIcon, ArrowRightIcon } from "@phosphor-icons/react";
import { useState } from "react";
import { ANALYSTS, COIN_KO, Card, ExpoBar, Help, Seg, VotePop, deskExpo, fmtPx, nextRebal, tone } from "@/components/HlPanel";
import { MultiLine, type LineDef } from "@/components/PriceChart";
import { ago, sgn } from "@/lib/ko";

type Any = Record<string, any>;
export type DeskData = Any;

// 선 색: dataviz 검증 통과(밝은 화면 #ffffff, 어두운 화면 #161619 각각). 롱·숏 색(빨강·파랑)과 겹치지 않게 골랐다. 메인은 글자색, AI 는 점선
const LINES: LineDef[] = [
  { key: "추세", label: "추세", light: "#1baf7a", dark: "#199e70" },
  { key: "파생", label: "파생", light: "#eda100", dark: "#c98500" },
  { key: "거시·심리", label: "거시·심리", light: "#6250d6", dark: "#9085e9" },
  { key: "main", label: "메인", light: "#1c1c1e", dark: "#f2f2f4", width: 3 },
  { key: "ai", label: "AI 그림자", light: "#e87ba4", dark: "#d55181", dashed: true },
];
const hm = (iso?: string | null) => (iso ? new Date(iso).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }) : "-");
const secAgo = (iso?: string | null) => (iso ? (Date.now() - Date.parse(iso)) / 1000 : Infinity);
type St = "ok" | "late" | "bad" | "none";
const Dot = ({ st }: { st: St }) => <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${st === "ok" ? "bg-good" : st === "late" ? "bg-warn" : st === "bad" ? "bg-bad" : "bg-line"}`} />;
const ST_KO: Record<St, string> = { ok: "정상", late: "늦음", bad: "꺼짐", none: "아직 없음" };

function Node({ n, title, st, at, children, grow }: { n: number; title: string; st: St; at?: string | null; children: React.ReactNode; grow: string }) {
  return (
    <section className={`min-w-0 rounded-[20px] border border-line bg-card p-4 shadow-card ${grow}`}>
      <div className="mb-2.5 flex items-center gap-2">
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-chip text-[12px] font-bold tabular-nums">{n}</span>
        <h2 className="text-[14.5px] font-bold">{title}</h2>
        <span className="ml-auto inline-flex items-center gap-1.5 text-[12.5px] text-sub"><Dot st={st} />{ST_KO[st]}{at ? ` · ${ago(at)}` : ""}</span>
      </div>
      {children}
    </section>);
}
const Arrow = () => (
  <div className="flex shrink-0 items-center justify-center text-sub"><ArrowRightIcon size={18} className="hidden xl:block" /><ArrowDownIcon size={18} className="xl:hidden" /></div>);
const Row = ({ k, v, sub }: { k: React.ReactNode; v: React.ReactNode; sub?: React.ReactNode }) => (
  <div className="flex items-baseline justify-between gap-2 text-[13.5px]"><span className="min-w-0 text-sub">{k}</span><span className="shrink-0 text-right font-semibold tabular-nums">{v}{sub && <span className="ml-1 font-normal text-sub">{sub}</span>}</span></div>);

const KINDS: [string, string][] = [["all", "전체"], ["check", "점검"], ["ai", "AI"], ["trade", "체결"], ["reselect", "재선정"], ["whale", "고래"]];

export function DeskPanel({ d, goHl }: { d: DeskData; goHl: () => void }) {
  const s: Any = d.state ?? {};
  const coins: string[] = d.coins ?? ["BTC", "ETH"];
  const [coin, setCoin] = useState(coins[0]);
  const [kind, setKind] = useState("all");
  const desk: Record<string, number> = s.desk?.[coin] ?? {}, votes: Record<string, number> = s.votes?.[coin] ?? {}, who: Record<string, string> = s.analyst ?? {};
  const last: Any | undefined = d.last?.coins?.[coin], prev: Any | undefined = d.prev?.coins?.[coin];
  const main = deskExpo(s, coin), lev = s.lev?.[coin], p = s.pos?.[coin], g = s.guard?.[coin] ?? {};
  const botSt: St = !d.running ? "bad" : secAgo(s.saved) > 120 ? "late" : "ok";
  const checked = d.last?.time as string | undefined;               // 마지막 점검 시각
  const half = s.half?.[coin] != null, waiting = half && s.half_k === 0, stopped = !!(s.stop_file || s.halt);
  // 1) 데이터 수집
  const inp = (key: string): { st: St; text: string } => {
    const x = s.inputs?.[key];
    if (!x?.ok && !x?.fail) return { st: "none", text: "다음 점검 때 받아요" };
    if (x.fail && (!x.ok || x.fail > x.ok)) return { st: "late", text: x.ok ? `받기 실패 → ${ago(new Date(x.ok * 1000).toISOString())} 값 사용` : "받기 실패 → 이번엔 표 없음" };
    const iso = new Date(x.ok * 1000).toISOString();
    return { st: secAgo(iso) > 7 * 3600 ? "late" : "ok", text: ago(iso) };
  };
  const whaleAt = d.whales?.time as string | undefined, wg = d.whales?.coins?.[coin];
  const sources: [string, string, { st: St; text: string }][] = [
    ["가격봉 (일봉·4시간)", "하이퍼리퀴드", { st: botSt, text: botSt === "bad" ? "봇이 꺼져 있어요" : "점검 때마다" }],
    ["펀딩비", "바이낸스", inp(`funding_${coin}`)],
    ["공포탐욕·스테이블코인·청산", "alternative.me · DefiLlama · 코인니스", inp("external")],
    ["미결제약정·계정 쏠림·옵션 변동성", "바이낸스 · Deribit", inp(`coin_${coin}`)],
    ["고래 포지션 (참고)", "하이퍼리퀴드", { st: !whaleAt ? "none" : secAgo(whaleAt) > 2 * 3600 ? "late" : "ok", text: whaleAt ? ago(whaleAt) : "기록 없음" }],
  ];
  const worst = (xs: St[]): St => (xs.includes("bad") ? "bad" : xs.includes("late") ? "late" : xs.every((x) => x === "none") ? "none" : "ok");
  const share = (a?: number, b?: number) => (a != null && b != null && a + b > 0 ? Math.round((a / (a + b)) * 100) : null);
  const sm = share(wg?.smart?.short, wg?.smart?.long), wk = share(wg?.weak?.long, wg?.weak?.short);
  // 5) 주문
  const lt = d.lastTrade as Record<string, string> | null, maker = lt?.why?.match(/지정가 (\d+)%/)?.[1], nr = nextRebal();
  const a = d.ai?.coins?.[coin], sc = d.score?.[coin];
  const rows: Any[] = d.series?.[coin] ?? [];
  const events: Any[] = (d.events ?? []).filter((e: Any) => kind === "all" || e.kind === kind);
  const result = last?.result ?? (stopped ? "매매 없음 (정지)" : waiting && !p ? "매매 없음 (새 추세 대기)" : "다음 점검에서 정해져요");
  return (
    <main className="mx-auto max-w-[2400px] space-y-4 px-3 py-3 sm:px-4 sm:py-4">
      <div className="flex flex-wrap items-center gap-2">
        <Seg items={coins.map((c) => [c, COIN_KO[c] ?? c] as [string, string])} value={coin} onChange={setCoin} />
        <span className="text-[13.5px] text-sub">4시간마다(1·5·9·13·17·21시) 왼쪽에서 오른쪽으로 한 번 돌아요 · 마지막 점검 {checked ? `${hm(checked)} (${ago(checked)})` : "기록 없음"} · 다음 {nr.at} ({nr.left})</span>
        <button onClick={goHl} className="press ml-auto text-[13.5px] text-accent">차트·조작은 하이퍼리퀴드 탭 ›</button>
      </div>

      {/* 1) 흐름도 */}
      <div className="flex flex-col gap-2 xl:flex-row xl:items-stretch">
        <Node n={1} title="데이터 수집" st={worst(sources.map((x) => x[2].st))} grow="xl:flex-[1.25]">
          <ul className="space-y-1.5">
            {sources.map(([name, src, x]) => (
              <li key={name} className="flex items-start gap-2 text-[13.5px]">
                <span className="mt-[7px]"><Dot st={x.st} /></span>
                <span className="min-w-0 flex-1"><span className="font-semibold">{name}</span><span className="block text-[12px] text-sub">{src}</span></span>
                <span className="shrink-0 text-right text-[12.5px] tabular-nums text-sub">{x.text}</span>
              </li>))}
          </ul>
        </Node>
        <Arrow />
        <Node n={2} title="분석가" st={!Object.keys(desk).length ? "none" : botSt} at={checked} grow="xl:flex-[1.35]">
          {!Object.keys(desk).length ? <p className="text-[13.5px] text-sub">다음 4시간 점검 때 의견이 채워져요.</p> : (
            <ul className="space-y-2 text-[14px]">
              {ANALYSTS.filter(([k]) => k in desk).map(([k, what]) => {
                const mine = Object.fromEntries(Object.entries(votes).filter(([n]) => who[n] === k));
                const dv = last?.desk?.[k] != null && prev?.desk?.[k] != null ? last.desk[k] - prev.desk[k] : null;
                return (
                  <li key={k} className="grid grid-cols-[76px_1fr_48px] items-center gap-x-2">
                    <VotePop side="left" votes={mine}><span className="font-bold underline decoration-dotted underline-offset-2">{k}</span></VotePop>
                    <ExpoBar v={desk[k]} /><span className={`text-right font-semibold tabular-nums ${tone(desk[k])}`}>{sgn(desk[k])}</span>
                    <span className="col-span-3 text-[12.5px] text-sub">{what} · 규칙 {Object.keys(mine).length}개{dv == null ? "" : Math.abs(dv) < 0.005 ? " · 직전과 같음" : ` · 직전보다 ${dv > 0 ? "롱 쪽으로" : "숏 쪽으로"} ${Math.abs(dv).toFixed(2)}`}</span>
                  </li>);
              })}
              <li className="border-t border-line pt-2 text-[12.5px] leading-relaxed text-sub">
                <b className="text-ink">고래 (참고 · 표 없음)</b> {sm == null ? "기록 없음" : <>실적 상위 지갑 {sm >= 50 ? `숏 ${sm}%` : `롱 ${100 - sm}%`}{wk != null && <> · 잃어 온 큰손 {wk >= 50 ? `롱 ${wk}%` : `숏 ${100 - wk}%`}</>}</>}. 과거 기록이 쌓이면 측정해서 표를 줄지 정해요.
              </li>
            </ul>)}
        </Node>
        <Arrow />
        <Node n={3} title="메인" st={!Object.keys(desk).length ? "none" : botSt} at={checked} grow="xl:flex-[0.85]">
          <div className={`text-[30px] font-bold leading-none tabular-nums ${tone(main)}`}>{sgn(main)}</div>
          <div className="mb-2 mt-1 text-[13px] text-sub">{main > 0.05 ? "롱 쪽" : main < -0.05 ? "숏 쪽" : "중립"} · −1 전부 숏 ~ +1 전부 롱</div>
          <ExpoBar v={main} />
          <div className="mt-3 space-y-1">
            <Row k="합치는 방법" v={`분석가 ${Object.keys(desk).length || "-"}명 평균`} />
            <Row k="규칙" v={`${Object.keys(votes).length}개`} sub={`롱 ${Object.values(votes).filter((v) => v > 0).length} · 숏 ${Object.values(votes).filter((v) => v < 0).length}`} />
            <Row k="배수" v={lev != null ? `${lev}배` : "-"} sub="변동성에 맞춤" />
          </div>
        </Node>
        <Arrow />
        <Node n={4} title="안전장치" st={stopped ? "late" : botSt} at={checked} grow="xl:flex-1">
          <ul className="space-y-1.5 text-[13.5px]">
            {([
              [waiting ? "새 추세 대기" : "절반 모드", half, half ? (waiting ? `${s.half[coin] < 0 ? "숏" : "롱"} 의견이 끝날 때까지 안 들어감` : "목표의 절반만") : "꺼짐"],
              ["이익 보호", g.cut != null && g.cut < 1, g.cut != null && g.cut < 1 ? "발동 — 25%만 유지" : p && g.line ? `정리선 $${fmtPx(g.line)}` : "포지션이 생기면 추적"],
              ["완충 30%", !!last?.result?.includes("완충"), last?.result?.includes("완충") ? "이번엔 차이가 작아 그대로 둠" : "차이가 작으면 안 건드림"],
              ["정지", stopped, stopped ? (s.halt || "전부 정리하고 멈춤") : "꺼짐"],
            ] as [string, boolean, string][]).map(([k, on, text]) => (
              <li key={k} className="flex items-start gap-2">
                <span className={`mt-[1px] shrink-0 rounded-full px-2 py-[1px] text-[12px] font-semibold ${on ? "bg-warn-soft text-warn" : "bg-chip text-sub"}`}>{on ? "걸림" : "통과"}</span>
                <span className="min-w-0"><b>{k}</b> <span className="text-sub">{text}</span></span>
              </li>))}
          </ul>
          <div className={`mt-3 rounded-xl px-2.5 py-1.5 text-[13.5px] font-semibold ${result.startsWith("매매 없음") || result.startsWith("다음") ? "bg-chip" : "bg-warn-soft text-warn"}`}>결과: {result}</div>
        </Node>
        <Arrow />
        <Node n={5} title="주문" st={botSt} at={lt?.time} grow="xl:flex-1">
          <div className="text-[15.5px] font-bold">{p ? <span className={p.size > 0 ? "text-up" : "text-down"}>{p.size > 0 ? "롱" : "숏"} {Math.abs(p.size)}개</span> : "현금 (포지션 없음)"}</div>
          {p && <div className="text-[13px] tabular-nums text-sub">진입가 ${fmtPx(p.entry)}{last?.target != null ? ` · 목표 ${last.target}개` : ""}</div>}
          <div className="mt-3 space-y-1">
            <Row k="마지막 체결" v={lt ? `${COIN_KO[lt.coin] ?? lt.coin} ${lt.side}` : "없음"} />
            {lt && <Row k="" v={`${lt.size}개 @ $${fmtPx(+lt.price)}`} sub={hm(lt.time)} />}
            {maker && <Row k="지정가로 채운 비율" v={`${maker}%`} />}
            <Row k="주문 방식" v="지정가로만" sub="(호가 따라가기, 최대 5분)" />
            <Row k="다음 점검" v={nr.at} sub={`(${nr.left})`} />
          </div>
        </Node>
      </div>

      {/* AI 운용역 — 같은 판을 보고 의견만 기록 */}
      <section className="rounded-[20px] border border-dashed border-line bg-card p-4 shadow-card">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-[14.5px] font-bold">AI 운용역 (그림자)</h2>
          <span className="rounded-full bg-chip px-2 py-[2px] text-[12.5px] font-semibold text-sub">매매 안 함 · 기록만</span>
          <span className="ml-auto inline-flex items-center gap-1.5 text-[12.5px] text-sub"><Dot st={!d.ai ? "none" : secAgo(d.ai.time) > 5 * 3600 ? "late" : "ok"} />{d.ai ? `${hm(d.ai.time)} (${ago(d.ai.time)}) · ${d.ai.model ?? ""}` : "아직 없음"}</span>
        </div>
        {!a ? <p className="mt-2 text-[13.5px] text-sub">4시간 점검 직후, 분석가 의견과 지표를 그대로 보고 "나라면 얼마나 걸지"를 적어요. 다음 점검 때 첫 의견이 기록돼요.</p> : (
          <div className="mt-3 grid gap-4 lg:grid-cols-[minmax(240px,0.6fr)_minmax(0,1.4fr)]">
            <div className="space-y-2 text-[14px]">
              {([["AI 운용역", a.exposure], ["규칙 메인", a.rule]] as [string, number][]).map(([k, v]) => (
                <div key={k} className="grid grid-cols-[76px_1fr_48px] items-center gap-x-2"><span className="font-bold">{k}</span><ExpoBar v={v} /><span className={`text-right font-semibold tabular-nums ${tone(v)}`}>{sgn(v)}</span></div>))}
              <p className="text-[12.5px] text-sub">{Math.sign(a.exposure) !== Math.sign(a.rule) ? "규칙과 방향이 달라요." : Math.abs(a.exposure - a.rule) < 0.05 ? "규칙과 거의 같아요." : Math.abs(a.exposure) < Math.abs(a.rule) ? "규칙보다 덜 걸겠다는 의견이에요." : "규칙보다 더 걸겠다는 의견이에요."}</p>
            </div>
            <div className="text-[14px] leading-relaxed"><p>{d.ai.easy}</p><p className="mt-1.5 text-[13px] text-sub">{a.reason}</p></div>
          </div>)}
      </section>

      <Help id="deskflow" title="흐름도 읽는 법" wide items={[
        ["흐름", "왼쪽부터 순서대로예요. ① 자료를 받고 → ② 분석가들이 의견을 내고 → ③ 메인이 평균을 내고 → ④ 안전장치가 걸러서 → ⑤ 주문을 내요. 4시간마다 한 번 돌아요."],
        ["점 색", "초록 = 정상, 주황 = 늦음(자료가 오래됐거나 받기 실패), 빨강 = 꺼짐, 회색 = 아직 기록 없음이에요."],
        ["숫자 (−1 ~ +1)", "−1은 전부 숏(내린다), +1은 전부 롱(오른다), 0은 중립이에요. 메인 숫자에 배수를 곱한 만큼 포지션을 잡아요."],
        ["펀딩비", "선물에서 롱과 숏이 정해진 시간마다 주고받는 돈이에요. 한쪽이 너무 많이 내면 그쪽이 과열이라고 봐요."],
        ["공포탐욕 지수", "시장 분위기를 0(극단적 공포)~100(극단적 탐욕)으로 나타낸 숫자예요."],
        ["스테이블코인", "달러에 값이 묶인 코인(USDT 등)의 시가총액이에요. 늘면 코인 시장에 달러가 들어오는 중이에요."],
        ["청산", "지난 하루 강제로 정리된 선물 포지션 총액이에요. 급증하면 한쪽이 한꺼번에 털린 거예요."],
        ["미결제약정", "아직 정리되지 않고 열려 있는 선물 계약 총액이에요. 갑자기 크게 불면 과열로 봐요."],
        ["계정 쏠림", "바이낸스 계정들이 롱·숏 중 한쪽으로 얼마나 몰렸는지예요. 심하게 몰리면 반대로 가는 일이 많아요."],
        ["옵션 변동성", "옵션 시장이 앞으로 가격이 얼마나 출렁일 거라고 보는지예요. 급등하면 큰 움직임을 예상한다는 뜻이에요."],
        ["걸림 · 통과", "안전장치가 이번 점검에서 작동했으면 '걸림', 아니면 '통과'예요. 완충 30%는 목표와 차이가 작으면 수수료를 아끼려고 그대로 두는 장치예요."],
        ["AI 그림자 성적표", "'의견'은 AI가 의견을 낸 횟수, '방향 다름'은 규칙과 롱·숏이 달랐던 횟수예요. '규칙 메인'과 'AI'의 %는 각자 의견대로 배수 없이 탔다면 났을 가상 손익이에요."],
      ]} />

      {/* 2) 의견 흐름 */}
      <Card title={`의견 흐름 · ${COIN_KO[coin] ?? coin}`} right={<span className="text-[12.5px] text-sub">−1 전부 숏 · +1 전부 롱 · 4시간마다 한 점</span>}>
        {rows.length < 2 ? <p className="text-[14px] text-sub">점검 기록이 {rows.length}번 쌓였어요. 두 번부터 선이 그려져요. 아래 활동 기록에서 점검마다 숫자를 볼 수 있어요.</p>
          : <MultiLine rows={rows} lines={LINES} />}
      </Card>

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        {/* 3) 활동 기록 */}
        <Card title="활동 기록" right={<Seg items={KINDS as [string, string][]} value={kind} onChange={setKind} />}>
          {!events.length ? <p className="text-[14px] text-sub">아직 기록이 없어요.</p> : (
            <ol className="max-h-[640px] space-y-3 overflow-y-auto pr-1">
              {events.map((e, i) => (
                <li key={`${e.t}${i}`} className="border-l-2 border-line pl-3">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="rounded-full bg-chip px-2 py-[1px] text-[12px] font-semibold">{KINDS.find(([k]) => k === e.kind)?.[1] ?? e.kind}</span>
                    <b className="text-[14px]">{e.title}</b><span className="text-[12.5px] tabular-nums text-sub">{hm(e.t)} · {ago(e.t)}</span>
                  </div>
                  {(e.lines as string[]).map((l, j) => <p key={j} className="mt-0.5 text-[13.5px] leading-relaxed text-sub">{l}</p>)}
                </li>))}
            </ol>)}
        </Card>
        <div className="space-y-4">
          {/* 4) AI 그림자 성적표 */}
          <Card title="AI 그림자 성적표">
            <table className="w-full text-[13.5px] tabular-nums">
              <thead><tr className="text-left text-[12.5px] text-sub"><th className="pb-1 font-normal">코인</th><th className="pb-1 text-right font-normal">의견</th><th className="pb-1 text-right font-normal">방향 다름</th><th className="pb-1 text-right font-normal">규칙 메인</th><th className="pb-1 text-right font-normal">AI</th></tr></thead>
              <tbody>
                {coins.map((c) => { const x = d.score?.[c] ?? {}; return (
                  <tr key={c} className="border-t border-line"><td className="py-1.5 font-semibold">{COIN_KO[c] ?? c}</td><td className="text-right">{x.n ?? 0}번</td><td className="text-right">{x.differ ?? 0}번</td>
                    <td className={`text-right font-semibold ${tone(x.rule ?? 0)}`}>{x.scored ? `${x.rule >= 0 ? "+" : "−"}${Math.abs(x.rule * 100).toFixed(2)}%` : "-"}</td>
                    <td className={`text-right font-semibold ${tone(x.ai ?? 0)}`}>{x.scored ? `${x.ai >= 0 ? "+" : "−"}${Math.abs(x.ai * 100).toFixed(2)}%` : "-"}</td></tr>); })}
              </tbody>
            </table>
            <p className="mt-2 text-[12.5px] leading-relaxed text-sub">각자 의견대로 배수 없이(1배) 탔다고 치고, 다음 점검 때 가격으로 채점한 가상 손익이에요. 실제 주문과는 무관해요.
              {(sc?.scored ?? 0) < 60 && <b className="text-ink"> 지금은 {sc?.scored ?? 0}번 채점이라 판단하기 일러요. 8주(코인당 약 330번)쯤 쌓이면 비교할 수 있어요.</b>}</p>
          </Card>
          {/* 5) 뒤에서 도는 작업 */}
          <Card title="뒤에서 도는 작업">
            <ul className="space-y-2">
              {(d.jobs as Any[] ?? []).map((j) => { const st: St = !j.ok ? "bad" : j.late && secAgo(j.at) > j.late ? (j.at ? "late" : "none") : "ok"; return (
                <li key={j.name} className="flex items-start gap-2 text-[13.5px]">
                  <span className="mt-[7px]"><Dot st={st} /></span>
                  <span className="min-w-0 flex-1"><span className="font-semibold">{j.name}</span><span className="block text-[12.5px] text-sub">{j.what}</span></span>
                  <span className="shrink-0 text-right text-[12.5px] tabular-nums text-sub">{j.at ? ago(j.at) : st === "bad" ? "꺼져 있음" : j.late ? "기록 없음" : "실행 중"}</span>
                </li>); })}
            </ul>
            <p className="mt-2 text-[12.5px] leading-relaxed text-sub">투표단은 지금 규칙 {d.signals?.n ?? "-"}개예요. 재선정 때 전부 새 데이터로 다시 재서 합격한 것만 남겨요.</p>
          </Card>
        </div>
      </div>
    </main>);
}
