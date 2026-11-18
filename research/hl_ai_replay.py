# AI 운용역 과거 재생 (2026-11-15, 기준은 돌리기 전에 고정). 봇 그림자(bot/hl_ai.py)와 같은 판·같은 프롬프트(hl_main.md)를
# 2022-06-01~ 매일 UTC 00시(일봉·4h 봉이 막 마감된 시점)에 주고 코인별 노출(−1~+1)을 받는다. 모델은 Haiku(비용), 실제 그림자는 Sonnet.
# 판에는 날짜·절대 가격이 없다. 그래도 모델이 과거 시세 흐름을 기억할 수 있어 낙관적으로 나올 수 있다 → 참고용.
# 채점: sweep.bt (일봉 1배, 다음 날 시가 체결, 편도 0.05%, 실제 펀딩). 같은 날 규칙 메인(분석가 평균) 노출을 같은 방식으로 채점해 비교.
# 합격: 고정 기준(OOS>0·PF>1.2·3구간 중 2개+·30회+) BTC·ETH 모두 + OOS 가 규칙 메인 이상.
import json, os, sys, threading
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "research", "leverage_2026-11"))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(ROOT, ".env"))
from bot import hl_ai, hl_signals as sig  # noqa: E402
from bot.hl_rules import rules  # noqa: E402
from sweep import bt, load, load_ext  # noqa: E402

OUT = os.path.join(ROOT, "research", "hl_ai_replay.jsonl")
MODEL, START = "claude-haiku-5-5", pd.Timestamp("2022-06-01", tz="UTC")
SYMS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
chosen = json.load(open(os.path.join(ROOT, "state", "hl_signals.json")))["chosen"]
_lk = threading.Lock()


def prep():
    X = {}
    for coin, sym in SYMS.items():
        d, fb, fd = load(sym, "1d"); h4, _, _ = load(sym, "4h")
        X[coin] = dict(d=d, fb=fb, h4=h4, Rd=rules(d, fd, load_ext(sym)), R4=rules(h4))
    return X


def boards(X, day):
    """day(UTC 00시)에 봇이 보는 판 두 개와 규칙 메인 노출."""
    out, rule = [], {}
    for coin, x in X.items():
        d = x["d"][x["d"].index < day]; h = x["h4"][x["h4"].index < day]          # 시작 시각이 day 전인 봉 = 마감된 봉
        votes = {f"일봉 {n}": float(x["Rd"][n].loc[d.index[-1]]) for n in chosen["1d"]} | {f"4h {n}": float(x["R4"][n].loc[h.index[-1]]) for n in chosen["4h"]}
        e = sig.combine(votes); rule[coin] = e
        out.append(hl_ai.board(coin, d.tail(400), h.tail(500), votes, sig.desk(votes), e, sig.leverage(h.tail(500), 0.6, 40, 5), None, "정상"))
    return out, rule


def run():
    X = prep()
    have = {json.loads(l)["day"] for l in open(OUT)} if os.path.exists(OUT) else set()
    days = [t for t in pd.date_range(START, X["BTC"]["d"].index[-1]) if t.strftime("%Y-%m-%d") not in have]
    print(f"{len(have)}일 있음, {len(days)}일 새로", flush=True)
    cost = [0.0]

    def one(day):
        b, rule = boards(X, day)
        for k in range(3):
            try:
                out, meta = hl_ai.ask(b, MODEL); break
            except Exception as e:
                if k == 2:
                    print("실패", day.date(), e, flush=True); return
        with _lk, open(OUT, "a") as f:
            cost[0] += meta.get("cost_usd") or 0
            f.write(json.dumps({"day": day.strftime("%Y-%m-%d"), "rule": rule, "ai": {c: out[c]["exposure"] for c in SYMS if c in out}}, ensure_ascii=False) + "\n")
    with ThreadPoolExecutor(6) as ex:
        for i, _ in enumerate(ex.map(one, days)):
            if i % 200 == 0:
                print(f"  {i}/{len(days)} 비용 ${cost[0]:.2f}", flush=True)
    print(f"끝, 이번 비용 ${cost[0]:.2f}", flush=True)
    return X


def score(X=None):
    X = X or prep()
    r = pd.DataFrame([json.loads(l) for l in open(OUT)]).drop_duplicates("day").set_index("day").sort_index()
    r.index = pd.to_datetime(r.index, utc=True) - pd.Timedelta(days=1)          # day 의 판단 = 전날 일봉 마감 신호 (bt 가 다음 날 시가에 체결)
    print(f"\n판단 {len(r)}일 ({r.index[0].date()} ~ {r.index[-1].date()})")
    res = {}
    for coin in SYMS:
        d, fb = X[coin]["d"], X[coin]["fb"]
        d, fb = d[d.index >= r.index[0]], fb[fb.index >= r.index[0]]
        for who in ("rule", "ai"):
            s = r[who].apply(lambda v: v.get(coin)).reindex(d.index).fillna(0)
            res[(coin, who)] = x = bt(d, s, fb)
            print(f"  {coin} {'규칙 메인' if who == 'rule' else 'AI 운용역'}: 연 {x['cagr']*100:+4.0f}% MDD {x['mdd']*100:3.0f}% OOS {x['oos']*100:+5.0f}% PF {x['pf']:.2f} {x['n']}회 [{'/'.join(f'{v*100:+.0f}' for v in x['seg'])}]{' ✓' if x['ok'] else ''}")
        a, b = r["ai"].apply(lambda v: v.get(coin)), r["rule"].apply(lambda v: v.get(coin))
        print(f"  {coin} AI 와 규칙의 방향이 다른 날 {(a * b < 0).mean() * 100:.1f}% · AI 평균 크기 {a.abs().mean():.2f} (규칙 {b.abs().mean():.2f}) · 상관 {a.corr(b):.2f}")
    ok = all(res[(c, 'ai')]["ok"] and res[(c, 'ai')]["oos"] >= res[(c, 'rule')]["oos"] for c in SYMS)
    print(f"합격: {'통과' if ok else '탈락'}")


if __name__ == "__main__":
    score(run() if "score" not in sys.argv else None)
