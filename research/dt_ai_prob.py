# AI 확률 판단 채점 (2026-11-15, dt_ai_replay.py 후속). 기준은 돌리기 전에 고정.
# dt_ai_replay 가 'skip'만 내서 채점이 안 됐다 → 매번 '4시간 뒤 오를 확률(0~100)'을 답하게 하고 순위로 채점한다.
#  A: 지표 스냅샷만 (dt_ai_replay 와 같은 스냅샷)
#  B: A + 1분~1시간봉 투표판(지금 투표단 가격 규칙 17개를 업비트 봉에 적용한 롱·숏 수, 대시보드 방향판과 같은 계산)
# 같은 시점 598곳(dt_ai_replay 와 같은 seed), 미래 수익은 dt_ai_replay.jsonl 의 r4(다음 60분봉 시가 → 4시간 뒤 종가)를 다시 쓴다.
# 합격(전부): ① 확률 상위 20% 시점의 평균 4h 수익 − 0.2% > 0 ② 상위 20% 평균이 전체 평균보다 높음
#            ③ 기간 3등분 각각의 상위 20% 중 2개+ 에서 ①이 성립 ④ 확률과 수익의 순위 상관 > 0
import json, os, sys, threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dt_ai_replay as R  # noqa: E402
from bot import hl_signals as sig  # noqa: E402
from bot.hl_rules import NEEDS_EXTRA, NEEDS_FUNDING, rules  # noqa: E402

D = os.path.dirname(os.path.abspath(__file__))
CACHE, OUT = f"{D}/dt_ai_snaps.jsonl", f"{D}/dt_ai_prob.jsonl"
TFS = {1: "1m", 5: "5m", 15: "15m", 30: "30m", 60: "1h"}
ch, _ = sig.chosen()
NAMES = sorted({n for v in ch.values() for n in v} - NEEDS_EXTRA - NEEDS_FUNDING)
MODEL = "claude-haiku-5-5"
SYSTEM = """너는 업비트 비트코인 현물 단타 트레이더다. 주어진 시장 스냅샷만 보고 '4시간 뒤 가격이 지금보다 높을 확률'을 0~100 정수로 답한다.
50은 모르겠다는 뜻이다. 근거가 있으면 50에서 벗어나게 답한다. 가격은 마지막 종가를 10000으로 맞춘 지수이고, 날짜는 지워져 있다.
reason 은 한국어 한 문장."""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["p_up", "reason"],
          "properties": {"p_up": {"type": "integer"}, "reason": {"type": "string"}}}
_lk = threading.Lock()


def votes(c, t):
    out = {}
    for u, k in TFS.items():
        a = R.req(c, u, 200, t.isoformat())
        b = R.req(c, u, 200, pd.Timestamp(a["candle_date_time_kst"].iloc[0]).tz_localize(R.S.KST).isoformat())
        df = pd.concat([b, a]).drop_duplicates("candle_date_time_kst").sort_values("candle_date_time_kst")
        df, _ = R.S.split_closed(df.reset_index(drop=True), u, t)
        df.index = pd.to_datetime(df["candle_date_time_kst"])
        rr = rules(df)
        v = [float(rr[n].iloc[-1]) for n in NAMES if n in rr]
        out[k] = {"롱": sum(x > 0 for x in v), "숏": sum(x < 0 for x in v), "규칙수": len(v)}
    return out


def build():
    have = {json.loads(x)["t"] for x in open(CACHE)} if os.path.exists(CACHE) else set()
    fw = {r["t"]: r for r in map(json.loads, open(R.OUT)) if r["model"] == MODEL}
    todo = [t for t in R.times(600) if t.isoformat() in fw and t.isoformat() not in have]
    print(f"스냅샷 {len(have)}개 있음, {len(todo)}개 새로", flush=True)
    c = R.UpbitClient()

    def one(t):
        s, v = R.snapshot(c, t), votes(c, t)
        with _lk, open(CACHE, "a") as f:
            f.write(json.dumps({"t": t.isoformat(), "r4": fw[t.isoformat()]["r4"], "snap": s, "votes": v}, ensure_ascii=False) + "\n")
    with ThreadPoolExecutor(2) as ex:
        for i, _ in enumerate(ex.map(one, todo)):
            if i % 50 == 0:
                print(f"  스냅샷 {i}/{len(todo)}", flush=True)


def ask_all(variant):
    import anthropic
    from dotenv import load_dotenv
    load_dotenv(f"{D}/../.env")
    ws = os.getenv("ANTHROPIC_WORKSPACE_ID", "").strip()
    api = anthropic.Anthropic(max_retries=3, timeout=120, default_headers={"anthropic-workspace-id": ws} if ws else None)
    done = {(r["v"], r["t"]) for r in map(json.loads, open(OUT))} if os.path.exists(OUT) else set()
    rows = [r for r in map(json.loads, open(CACHE)) if (variant, r["t"]) not in done]
    cost = [0.0]

    def one(r):
        snap = dict(r["snap"])
        if variant == "B":
            snap["투표판(가격 규칙 17개, 1분~1시간봉)"] = r["votes"]
        m = api.messages.create(model=MODEL, max_tokens=3000, system=SYSTEM,
                                messages=[{"role": "user", "content": json.dumps(snap, ensure_ascii=False)}],
                                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}})
        d = json.loads("".join(b.text for b in m.content if b.type == "text"))
        p = R.PRICES[MODEL]
        cst = (m.usage.input_tokens * p["in"] + m.usage.output_tokens * p["out"]) / 1e6
        with _lk, open(OUT, "a") as f:
            cost[0] += cst
            f.write(json.dumps({"v": variant, "t": r["t"], "r4": r["r4"], "p": d["p_up"], "reason": d["reason"], "cost": cst}, ensure_ascii=False) + "\n")
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(one, rows))
    print(f"{variant}: {len(rows)}개 물음, 비용 ${cost[0]:.2f}", flush=True)


def score():
    d = pd.DataFrame(map(json.loads, open(OUT)))
    base = d.groupby("v").r4.mean()
    for v, g in d.groupby("v"):
        g = g.sort_values("t").reset_index(drop=True)
        g["third"] = np.arange(len(g)) * 3 // len(g)
        top = g[g.p >= g.p.quantile(0.8)]
        net = top.r4.mean() - R.COST
        th = [(x.r4[x.p >= x.p.quantile(0.8)].mean() - R.COST) for _, x in g.groupby("third")]
        rho = g.p.rank().corr(g.r4.rank())
        bot = g[g.p <= g.p.quantile(0.2)]
        ok = [net > 0, top.r4.mean() > g.r4.mean(), sum(x > 0 for x in th) >= 2, rho > 0]
        print(f"\n[{'A 지표만' if v == 'A' else 'B 지표+투표판'}] {len(g)}곳, 확률 분포 {g.p.min()}~{g.p.max()} (중앙 {g.p.median():.0f}), 비용 ${g.cost.sum():.2f}")
        print(f"  전체 평균 4h 수익(비용 전) {g.r4.mean() * 100:+.3f}% · 순위상관 {rho:+.3f}")
        print(f"  확률 상위 20% ({len(top)}곳, p≥{g.p.quantile(0.8):.0f}): 비용 전 {top.r4.mean() * 100:+.3f}% → 비용 후 {net * 100:+.3f}%, 오른 비율 {(top.r4 > 0).mean() * 100:.0f}%")
        print(f"  확률 하위 20% ({len(bot)}곳): 비용 전 {bot.r4.mean() * 100:+.3f}%")
        print(f"  3등분별 상위 20% 비용 후: " + " / ".join(f"{x * 100:+.3f}%" for x in th))
        print(f"  합격: {'통과' if all(ok) else '탈락'} (비용 후>0 {ok[0]}, 전체보다 나음 {ok[1]}, 3구간 2+ {ok[2]}, 상관>0 {ok[3]})")


if __name__ == "__main__":
    if "score" not in sys.argv:
        build()
        for v in ("A", "B"):
            ask_all(v)
    score()
