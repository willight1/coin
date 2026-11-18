# 정리 방식 비교 (2026-11-15). 15분 해상도로 같은 시뮬레이터에서:
#   기준 = 지금 봇(20표 투표 × 변동성 배수, 4h 마감 재조정, 완충 30%)
#   A 익절 X% (진입가 대비, 4h 종가 판정) → 전부 정리, 다음 날 09시(UTC 00) 투표 방향대로 재진입
#   B 여러 시간대 다수결: 15분·30분·1시간·4시간·일봉 각각 가격 규칙 14개 평균의 부호, 3개 이상이 포지션과 반대면 0 (15분 종가 판정)
#   T 바닥(최고 이익)에서 5% 되돌림 → 75% 정리 (4h 종가 판정, profit_guard.py 와 같은 규칙)
# 체결은 판정 다음 15분봉 시가, 편도 0.05%, 바이낸스 실제 펀딩, 유지증거금 1.25%, 장중 고가·저가로 청산 판정.
import json, sys
import numpy as np, pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "/Users/juno/orca/coin")
from stops import expo_series, COST, MM
from bot.hl_rules import rules, NEEDS_EXTRA, NEEDS_FUNDING

BAND = 0.30
chosen = json.load(open("/Users/juno/orca/coin/state/hl_signals.json"))["chosen"]
PRICE_RULES = sorted({n for v in chosen.values() for n in v} - NEEDS_EXTRA - NEEDS_FUNDING)
AGG = dict(open="first", high="max", low="min", close="last", value="sum")


def mtf_signs(m15):
    """시간대별 (가격 규칙 평균의 부호), 그 봉이 닫힌 뒤부터 15분 축에 붙인다."""
    out = {}
    for tf, rule in (("15m", None), ("30m", "30min"), ("1h", "1h"), ("4h", "4h"), ("1d", "1D")):
        df = m15 if rule is None else m15.resample(rule).agg(AGG).dropna()
        R = rules(df)
        s = np.sign(pd.concat([R[n] for n in PRICE_RULES if n in R], axis=1).mean(axis=1))
        out[tf] = s.shift(1).reindex(m15.index, method="ffill").fillna(0)     # 닫힌 봉의 값만
    return pd.DataFrame(out)


def sim15(m15, f15, tgt, mtf=None, tp=None, trail=None, start=None):
    o, h, l, c = (m15[k].values for k in ("open", "high", "low", "close"))
    t = m15.index
    end4h = ((t + pd.Timedelta(minutes=15)).hour % 4 == 0) & ((t + pd.Timedelta(minutes=15)).minute == 0)
    day0 = (t.hour == 0) & (t.minute == 0)
    opp = None
    if mtf is not None:
        sg = mtf.values
    i0 = 0 if start is None else int(t.searchsorted(pd.Timestamp(start, tz="UTC")))
    E, held, turn, dead, eq = 1.0, 0.0, 0.0, None, []
    side, entry, best, cut, wait_day = 0, 0.0, 0.0, 1.0, False
    T, F = tgt.values, f15.values
    for i in range(i0, len(t) - 1):
        p = T[i]; d = int(np.sign(p))
        if d != side:
            side, entry, best, cut, wait_day = d, o[i], 0.0, 1.0, False
        if wait_day and day0[i]:                                   # A: 다음 날 09시 → 새 진입가로 재시작
            wait_day, entry, best = False, o[i], 0.0
        if wait_day:
            p = 0.0
        p *= cut
        if mtf is not None and d and (sg[i] == -d).sum() >= 3:     # B: 5개 중 3개 이상 반대 → 비움
            p = 0.0
        if held and p and np.sign(p) == np.sign(held) and abs(p - held) < BAND * abs(p):
            p = held
        turn += abs(p - held); E *= 1 - abs(p - held) * COST
        if p:
            adverse = (l[i] / o[i] - 1) if p > 0 else -(h[i] / o[i] - 1)
            if adverse <= -(1 - MM * abs(p)) / abs(p):
                E = 0.0; dead = t[i]; eq.append(E); break
            r = o[i + 1] / o[i] - 1
            E *= 1 + p * r - p * F[i]
            held = p * (1 + r) / (1 + p * r)
        else:
            held = 0.0
        eq.append(E)
        if side and end4h[i] and not wait_day:                     # 4h 종가 판정
            fav = side * (c[i] / entry - 1); best = max(best, fav)
            if tp is not None and fav >= tp:
                wait_day = True
            if trail is not None and cut > 0.25 and (1 + best) / (1 + fav) - 1 >= trail:
                cut = 0.25
    s = pd.Series(eq, index=t[i0:i0 + len(eq)])
    yrs = (s.index[-1] - s.index[0]).days / 365.25; k = int(len(s) * 0.7); Ef = s.iloc[-1]
    return dict(cagr=(Ef ** (1 / yrs) - 1) if Ef > 0 else -1.0, mdd=float((1 - s / s.cummax()).max()),
                oos=(Ef / s.iloc[k] - 1) if Ef > 0 and s.iloc[k] > 0 else -1.0, dead=dead, turn=turn / yrs)


if __name__ == "__main__":
    for sym in ("BTCUSDT", "ETHUSDT"):
        m15 = pd.read_pickle(f"bn_{sym}_15m.pkl")
        f15 = pd.read_pickle(f"bn_{sym}_f.pkl").resample("15min").sum().reindex(m15.index).fillna(0)
        h4, fb4, e = expo_series(sym)
        rv = (np.log(h4.open / h4.open.shift()).rolling(120).std() * np.sqrt(6 * 365))
        mtf = mtf_signs(m15)
        for lo, start in ((1, None), (5, "2020-04-01"), (5, "2022-06-01"), (5, "2024-01-01")):
            lev = np.clip((0.6 / rv).fillna(1.0), lo, 50)
            tgt = (e.shift(1).fillna(0) * lev).reindex(m15.index, method="ffill").fillna(0)
            cases = [("지금", {}), ("A 익절 3%", dict(tp=.03)), ("A 익절 5%", dict(tp=.05)), ("A 익절 10%", dict(tp=.10)),
                     ("B 다수결 정리", dict(mtf=mtf)), ("T 되돌림5%→75%", dict(trail=.05))]
            print(f"\n== {sym} 하한 {lo}배 {start or '2017'}~")
            for name, kw in cases:
                r = sim15(m15, f15, tgt, start=start, **kw)
                print(f"  {name:16} 연 {r['cagr']*100:+6.0f}%  MDD {r['mdd']*100:3.0f}%  OOS {r['oos']*100:+7.0f}%  회전 연 {r['turn']:5.0f}배"
                      + (f"  ← {r['dead']:%Y-%m-%d} 전멸" if r['dead'] is not None else ""), flush=True)
