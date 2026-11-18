# AI 단타 판단 과거 재생 채점 (2026-11-15). 기준은 돌리기 전에 고정.
# 2025-01-01 ~ 2026-11-14 KRW-BTC 에서 무작위 정각(KST) N개. 그 시각까지 확정된 봉만으로 AI 봇과 같은 지표(dt_snapshot.indicators)를 만들고,
# 날짜·절대 가격을 지운다(가격은 마지막 종가=10000 지수, 거래대금은 20봉 평균 대비 배수) — 학습 때 본 과거 가격을 알아보지 못하게.
# AI 답: 지금 사서 1시간 또는 4시간 들고 있을지(롱만, 업비트 현물). 채점: 다음 60분봉 시가 매수 → 1h/4h 뒤 종가 매도, 왕복 비용 0.2%.
# 합격(전부): ① 매수 30회+ ② 매수 평균 순수익 > 0 ③ 같은 보유시간으로 '모든 시점에 산 것'(무작위 매수)보다 높음 ④ 기간 3등분 중 2개+ 플러스
# 결과는 research/dt_ai_replay.jsonl 에 쌓아 다시 돌려도 이미 물은 시점은 다시 부르지 않는다.
import json, os, random, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot import dt_snapshot as S  # noqa: E402
from bot.upbit_client import UpbitClient  # noqa: E402
from bot.dt_agent import PRICES  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dt_ai_replay.jsonl")
COST = 0.002
START, END = datetime(2025, 1, 1, tzinfo=S.KST), datetime(2026, 11, 14, tzinfo=S.KST)
SYSTEM = """너는 업비트 비트코인 현물 단타 트레이더다. 롱만 가능하고 공매도는 없다. 사고파는 왕복 비용은 0.2%다.
주어진 시장 스냅샷(1분봉~주봉 확정봉 지표와 최근 봉)만 보고, 지금 사서 1시간 또는 4시간 뒤에 팔지, 아니면 사지 않을지 정한다.
가격은 마지막 종가를 10000으로 맞춘 지수이고, 날짜는 지워져 있다. 확실한 우위가 없으면 사지 않는다(skip).
reason 은 한국어 한두 문장."""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["action", "horizon_h", "confidence", "reason"],
          "properties": {"action": {"type": "string", "enum": ["buy", "skip"]},
                         "horizon_h": {"type": "integer", "enum": [1, 4]},
                         "confidence": {"type": "number"}, "reason": {"type": "string"}}}
_lock = threading.Lock()
_last = [0.0]


def req(c, unit, count, to):
    with _lock:  # 업비트 시세 조회 초당 10회 제한
        w = 0.2 - (time.time() - _last[0])
        if w > 0:
            time.sleep(w)
        _last[0] = time.time()
    for k in range(4):
        try:
            return S.fetch_candles(c, unit, count=count, to=to)
        except Exception:
            time.sleep(1 + k)
    raise RuntimeError(f"캔들 조회 실패 {unit} {to}")


def snapshot(c, t: datetime) -> dict:
    to = t.isoformat()
    out = {"time_of_day_kst": t.strftime("%H:%M"), "weekday": "월화수목금토일"[t.weekday()]}
    for u in S.UNITS:
        df, _ = S.split_closed(req(c, u, 200, to), u, t)  # 진행 중이던 봉(과거 조회엔 최종값이 들어 있음)은 버린다
        ind = S.indicators(df)
        last = float(df["close"].iloc[-1])
        ind.pop("close", None)
        if ind.get("macd_hist") is not None:
            ind["macd_hist_pct"] = round(ind.pop("macd_hist") / last * 100, 3)
            ind["macd_hist_prev_pct"] = round(ind.pop("macd_hist_prev") / last * 100, 3)
        sec = {"ind": ind}
        if u in S.RAW_UNITS:
            v = df["value"].tail(21).iloc[:-1].mean()
            sec["raw[시각,시,고,저,종,거래대금배수]"] = [
                [r.candle_date_time_kst[11:16] if u != 1440 else "", *(round(x / last * 10000, 1) for x in (r.open, r.high, r.low, r.close)),
                 round(r.value / v, 2)] for r in df.tail(8).itertuples()]
        out["1w" if u == "week" else "1d" if u == 1440 else f"{u}m"] = sec
    return out


def forward(c, t: datetime) -> dict:
    df = req(c, 60, 4, (t + timedelta(hours=4)).isoformat())
    df = df[pd.to_datetime(df["candle_date_time_kst"]) >= pd.Timestamp(t.replace(tzinfo=None))]
    if len(df) != 4 or df["candle_date_time_kst"].iloc[0][11:13] != t.strftime("%H"):
        return None  # 거래가 빈 시간(점검 등)
    o = float(df["open"].iloc[0])
    return {"r1": float(df["close"].iloc[0]) / o - 1, "r4": float(df["close"].iloc[3]) / o - 1}


def ask(api, model, snap) -> tuple[dict, float]:
    r = api.messages.create(model=model, max_tokens=4000, system=SYSTEM,
                            messages=[{"role": "user", "content": json.dumps(snap, ensure_ascii=False)}],
                            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}})
    p, u = PRICES[model], r.usage
    return json.loads("".join(b.text for b in r.content if b.type == "text")), \
        (u.input_tokens * p["in"] + u.output_tokens * p["out"]) / 1e6


def times(n, seed=7):
    rnd = random.Random(seed)
    hours = int((END - START).total_seconds() // 3600)
    return sorted(START + timedelta(hours=h) for h in rnd.sample(range(hours), n))


def done(model):
    if not os.path.exists(OUT):
        return {}
    rows = [json.loads(x) for x in open(OUT)]
    return {r["t"]: r for r in rows if r["model"] == model}


def run(model, n, budget):
    import anthropic
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(OUT), "..", ".env"))
    ws = os.getenv("ANTHROPIC_WORKSPACE_ID", "").strip()
    api = anthropic.Anthropic(max_retries=2, timeout=120, default_headers={"anthropic-workspace-id": ws} if ws else None)
    c, have = UpbitClient(), done(model)
    todo = [t for t in times(n) if t.isoformat() not in have]
    random.Random(1).shuffle(todo)  # 예산에서 멈춰도 표본이 앞 기간에 몰리지 않게
    spent = [0.0]
    print(f"{model}: {len(have)}개 있음, {len(todo)}개 새로", flush=True)

    def one(t):
        if spent[0] >= budget:
            return
        fw = forward(c, t)
        if fw is None:
            return
        snap = snapshot(c, t)
        d, cost = ask(api, model, snap)
        with _lock:
            spent[0] += cost
            with open(OUT, "a") as f:
                f.write(json.dumps({"model": model, "t": t.isoformat(), **fw, **d, "cost": cost}, ensure_ascii=False) + "\n")
    with ThreadPoolExecutor(4) as ex:
        for i, _ in enumerate(ex.map(one, todo)):
            if i % 50 == 0:
                print(f"  {i}/{len(todo)} 비용 ${spent[0]:.2f}", flush=True)
    print(f"  끝, 이번 비용 ${spent[0]:.2f}", flush=True)


def score(model):
    d = pd.DataFrame(done(model).values())
    if d.empty:
        return
    d = d.sort_values("t").reset_index(drop=True)
    d["third"] = np.arange(len(d)) * 3 // len(d)
    b = d[d.action == "buy"].copy()
    b["r"] = np.where(b.horizon_h == 1, b.r1, b.r4) - COST
    base = {h: d[f"r{h}"].mean() - COST for h in (1, 4)}
    b["base"] = b.horizon_h.map(base)
    thirds = [b[b.third == k].r.mean() for k in range(3)]
    ok = [len(b) >= 30, b.r.mean() > 0 if len(b) else False, (b.r - b.base).mean() > 0 if len(b) else False,
          sum(x > 0 for x in thirds if x == x) >= 2]
    print(f"\n[{model}] 시점 {len(d)}개, 매수 {len(b)}번 (1h {sum(b.horizon_h == 1)} · 4h {sum(b.horizon_h == 4)}), 비용 ${d.cost.sum():.2f}")
    print(f"  무작위 매수 평균 순수익: 1h {base[1] * 100:+.3f}% · 4h {base[4] * 100:+.3f}%")
    if len(b):
        print(f"  AI 매수 평균 순수익 {b.r.mean() * 100:+.3f}% (무작위 대비 {(b.r - b.base).mean() * 100:+.3f}%p), 이긴 비율 {(b.r > 0).mean() * 100:.0f}%, 합계 {b.r.sum() * 100:+.1f}%")
        print(f"  기간 3등분 평균: " + " / ".join(f"{x * 100:+.3f}%" if x == x else "거래없음" for x in thirds))
        hi = b[b.confidence >= b.confidence.median()]
        print(f"  자신감 상위 절반({len(hi)}번) 평균 {hi.r.mean() * 100:+.3f}%")
        # 사지 않은 시점의 결과 — AI 가 '피한' 게 실제로 나빴는지
        s = d[d.action == "skip"]
        print(f"  안 산 시점({len(s)}개)의 1h/4h 평균(비용 전): {s.r1.mean() * 100:+.3f}% / {s.r4.mean() * 100:+.3f}%, 산 시점(비용 전): {b.r1.mean() * 100:+.3f}% / {b.r4.mean() * 100:+.3f}%")
    print(f"  합격: {'통과' if all(ok) else '탈락'}  (30회+ {ok[0]}, 순수익>0 {ok[1]}, 무작위보다 나음 {ok[2]}, 3구간 중 2+ {ok[3]})")


if __name__ == "__main__":
    model, n, budget = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
    if n:
        run(model, n, budget)
    score(model)
