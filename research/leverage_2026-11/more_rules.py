# 하이퍼리퀴드 봇 규칙 후보 2차 측정 (2026-11-15). 규칙은 미리 고정, 합격 기준·비용·체결은 sweep.bt 그대로.
# 1 주봉(rules 33개)  2 거시(FRED 나스닥100·달러지수)  3 상대강도(BTC↔ETH)  4 재조정 방식(완충·데드존)  5 코인 추가
# 데이터: 이 폴더 pkl. 알트는 fetch_bn.klines/funding 으로 미리 받아 둠(bn_{SYM}_{d,h,f}.pkl). FRED 는 fred_*.pkl 캐시.
import io, os, sys
import numpy as np, pandas as pd, requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sweep import D, bt, load, load_ext
from stops import expo_series, chosen, COST, MM
from bot.hl_rules import rules, sign

SYMS = ("BTCUSDT", "ETHUSDT")
ALTS = ("SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT")
HL_MAX = {"BTCUSDT": 40, "ETHUSDT": 25, "SOLUSDT": 20, "XRPUSDT": 20}          # 나머지 10배


def utc(t):
    t = pd.Timestamp(t)
    return t if t.tz else t.tz_localize("UTC")


def cell(x):
    return (f"연{x['cagr']*100:+5.0f}% MDD{x['mdd']*100:3.0f}% OOS{x['oos']*100:+6.0f}% PF{x['pf']:4.2f} {x['n']:4d}회 "
            f"[{'/'.join(f'{v*100:+.0f}' for v in x['seg'])}]{'✓' if x['ok'] else ' '}")


def report(title, res):
    print(f"\n===== {title}")
    for name, r in res.items():
        ok = all(x["ok"] for x in r.values())
        print(f"{'★' if ok else ' '} {name:34} | " + " | ".join(f"{s[:3]} {cell(x)}" for s, x in r.items()))


# ---------------------------------------------------------------- 1 주봉
def weekly():
    res = {}
    for sym in SYMS:
        d, _, _ = load(sym, "1d"); f = pd.read_pickle(f"{D}/bn_{sym}_f.pkl")
        kw = dict(label="left", closed="left")                 # 월요일 00:00 UTC 시작 주봉 (바이낸스 주봉과 같은 구간)
        w = d.resample("W-MON", **kw).agg(dict(open="first", high="max", low="min", close="last", value="sum")).dropna()
        fw = f.resample("W-MON", **kw).sum().reindex(w.index).fillna(0)
        for name, sig in rules(w, fw).items():
            res.setdefault(f"1w {name}", {})[sym] = bt(w, sig, fw)
    report("1 주봉 (rules 33개, 주간 펀딩)", res)


# ---------------------------------------------------------------- 2 거시
def fred(sid):
    p = f"{D}/fred_{sid}.pkl"
    if not os.path.exists(p):
        r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=30); r.raise_for_status()
        s = pd.read_csv(io.StringIO(r.text), index_col=0, parse_dates=True, na_values=".").iloc[:, 0].dropna()
        s.index = s.index.tz_localize("UTC"); s.to_pickle(p)
    return pd.read_pickle(p)


def lagged(s, days, idx):
    """발표 지연만큼 늦춰서(미래정보 방지) 일봉 index 에 맞추고 주말·휴일은 직전 값."""
    s = s.copy(); s.index = s.index + pd.Timedelta(days=days)
    return s.reindex(s.index.union(idx)).ffill().reindex(idx)


def macro():
    ndx, dx = fred("NASDAQ100"), fred("DTWEXBGS")
    nq_raw = sign(ndx - ndx.rolling(50).mean()); dx_raw = -sign(dx - dx.rolling(50).mean())
    res = {}
    for sym in SYMS:
        d, fb, _ = load(sym, "1d"); c = d.close
        base = sign(c - c.rolling(50).mean())
        nq, dxs = lagged(nq_raw, 1, d.index), lagged(dx_raw, 7, d.index)
        R = {"나스닥 50일선 위 롱/아래 숏": nq,
             "이평50 + 나스닥 같은 방향일 때만": base.where(nq == base, 0),
             "달러지수 50일선 아래 롱/위 숏": dxs}
        for name, sig in R.items():
            res.setdefault(f"1d {name}", {})[sym] = bt(d, sig.fillna(0), fb)
    report(f"2 거시 (나스닥100 1일·달러지수 7일 늦춤, FRED {ndx.index[0].date()}~ / {dx.index[0].date()}~)", res)


# ---------------------------------------------------------------- 3 상대강도
def relative():
    res = {}
    for sym, oth in (("BTCUSDT", "ETHUSDT"), ("ETHUSDT", "BTCUSDT")):
        d, fb, _ = load(sym, "1d"); c = d.close
        oc = load(oth, "1d")[0].close.reindex(d.index).ffill()
        base = sign(c - c.rolling(50).mean())
        rel = (c / c.shift(20)) / (oc / oc.shift(20)) - 1
        ratio = c / oc
        R = {"이평50 + 상대강도 20일 같은 방향일 때만": base.where(sign(rel) == base, 0),
             "내코인/다른코인 비율 50일선 위 롱/아래 숏": sign(ratio - ratio.rolling(50).mean())}
        for name, sig in R.items():
            res.setdefault(f"1d {name}", {})[sym] = bt(d, sig.fillna(0), fb)
    report("3 상대강도 (BTC↔ETH, 대칭)", res)


# ---------------------------------------------------------------- 4 재조정 방식 (실제 봇처럼 수량을 들고 있어 가격에 따라 배수가 흘러감)
def sim2(h4, fb4, expo, lo, hi, tv=0.6, band=0.0, dead=0.0, start=None):
    """완충 band: 같은 방향이고 목표와 차이 < band×|목표| 면 안 맞춤. 데드존 dead: |노출|<dead → 0.
    반환: 평가금액 Series(봉 시작 기준), 연 회전율(자산 대비 배), 전멸 시각"""
    o, hi_, lo_ = h4.open.values, h4.high.values, h4.low.values
    rv = (np.log(h4.open / h4.open.shift()).rolling(120).std() * np.sqrt(6 * 365)).values
    lev = np.clip(np.nan_to_num(tv / rv, nan=1.0), lo, hi)
    e = expo.shift(1).fillna(0).values
    e = np.where(np.abs(e) < dead, 0.0, e)
    tgt, f = e * lev, fb4.values
    i0 = 0 if start is None else int(h4.index.searchsorted(utc(start)))
    E, held, turn, eq, dead_at = 1.0, 0.0, 0.0, [], None
    for i in range(i0, len(h4) - 1):
        p = tgt[i]
        if band and held and p and np.sign(p) == np.sign(held) and abs(p - held) < band * abs(p):
            p = held
        turn += abs(p - held); E *= 1 - abs(p - held) * COST
        if p:
            adverse = (lo_[i] / o[i] - 1) if p > 0 else -(hi_[i] / o[i] - 1)
            if adverse <= -(1 - MM * abs(p)) / abs(p):
                E = 0.0; dead_at = h4.index[i]; eq.append(E); break
            r = o[i + 1] / o[i] - 1
            E *= 1 + p * r - p * f[i]
            held = p * (1 + r) / (1 + p * r) if E > 0 else 0.0      # 수량은 그대로 → 배수가 흘러감
        else:
            held = 0.0
        eq.append(E)
    s = pd.Series(eq, index=h4.index[i0:i0 + len(eq)])
    yrs = max((s.index[-1] - s.index[0]).days / 365.25, 1e-9)
    return s, turn / yrs, dead_at


def stats(s, dead_at=None):
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    E = s.iloc[-1]; k = int(len(s) * 0.7)
    return dict(cagr=(E ** (1 / yrs) - 1) if E > 0 else -1.0, mdd=float((1 - s / s.cummax()).max()),
                oos=(E / s.iloc[k] - 1) if E > 0 and s.iloc[k] > 0 else -1.0, dead=dead_at)


def fmt(x, extra=""):
    return (f"연 {x['cagr']*100:+6.0f}%  MDD {x['mdd']*100:3.0f}%  OOS {x['oos']*100:+7.0f}%{extra}"
            + (f"  ← {x['dead']:%Y-%m-%d} 전멸" if x["dead"] is not None else ""))


def rebalance():
    print(f"\n===== 4 재조정 방식 (지금 투표단 {sum(len(v) for v in chosen.values())}표, 목표변동성 60%, 수량 보유·배수 흐름 반영)")
    cases = [("지금 (매 4h 목표로 맞춤)", {}), ("완충 10%", dict(band=0.1)), ("완충 20%", dict(band=0.2)),
             ("데드존 |노출|<0.3", dict(dead=0.3)), ("완충 20% + 데드존 0.3", dict(band=0.2, dead=0.3))]
    for sym in SYMS:
        h4, fb4, ex = expo_series(sym)
        for lo, start, label in ((1, None, "하한1배 전체"), (5, "2020-01-01", "하한5배 2020~")):
            print(f"--- {sym[:3]} {label}")
            for name, kw in cases:
                s, turn, dead_at = sim2(h4, fb4, ex, lo, 50, start=start, **kw)
                fee_t, fee_m = turn * 0.00045, turn * 0.00015
                print(f"  {name:22} {fmt(stats(s, dead_at))}  회전 연 {turn:6.1f}배 (테이커 수수료 연 {fee_t*100:4.1f}% → 지정가 {fee_m*100:4.1f}%)")


# ---------------------------------------------------------------- 5 코인 추가
def coins():
    print(f"\n===== 5 코인 추가 (지금 {sum(len(v) for v in chosen.values())}표 앙상블, 1배 bt 로 합격 판정)")
    eqs, ok = {}, {}
    for sym in SYMS + ALTS:
        if not os.path.exists(f"{D}/bn_{sym}_h.pkl"):
            print(f"  {sym} 데이터 없음 — 건너뜀"); continue
        h4, fb4, ex = expo_series(sym)
        x = bt(h4, ex, fb4); ok[sym] = x["ok"]
        hi = HL_MAX.get(sym, 10)
        eqs[sym] = {lo: sim2(h4, fb4, ex, lo, hi, start=None if lo == 1 else "2020-01-01") for lo in (1, 5)}
        s5 = stats(eqs[sym][5][0], eqs[sym][5][2])
        print(f"{'★' if x['ok'] else ' '} {sym[:-4]:5} {h4.index[0]:%Y-%m}~ 최대{hi:2d}배 | 1배: {cell(x)} | 하한5배 2020~: {fmt(s5)}")
    return eqs, ok


def portfolio(eqs, members, lo, start):
    """동일 비중, 4h 마다 비중 재조정(코인별 따로 계좌라 가정 — 실제 봇은 교차증거금)."""
    eq = pd.concat({m: eqs[m][lo][0] for m in members}, axis=1)
    eq = eq[eq.index >= utc(start)].ffill()     # 전멸한 코인은 0 으로 남음(그 몫은 현금 취급)
    rets = eq.pct_change().fillna(0).iloc[1:]
    s = (1 + rets.mean(axis=1)).cumprod()
    return stats(s)


if __name__ == "__main__":
    d, fb, fd = load("BTCUSDT", "1d")
    chk = bt(d, rules(d, fd, load_ext())["이동평균50 위/아래"], fb)
    print(f"[측정기 확인] 1d 이동평균50 BTC OOS {chk['oos']*100:+.0f}% (기존 +125%)")
    weekly(); macro(); relative(); rebalance()
    eqs, ok = coins()
    passed = [s for s in ALTS if ok.get(s)]
    common = max(eqs[s][1][0].index[0] for s in eqs) + pd.Timedelta(days=30)
    print(f"\n===== 포트폴리오 (공통 구간 {common:%Y-%m-%d}~ 에서 코인별로 다시 시작, 합격 알트: {', '.join(p[:-4] for p in passed) or '없음'})")
    for lo in (1, 5):
        sims = {s: {lo: sim2(*expo_series(s), lo, HL_MAX.get(s, 10), start=common)} for s in eqs}
        for label, mem in (("BTC+ETH", list(SYMS)), ("BTC+ETH+합격 알트", list(SYMS) + passed), ("전부 9개", list(eqs))):
            print(f"  하한{lo}배 {label:16} {fmt(portfolio(sims, mem, lo, common))}")
