# 급반등 대비 비상선 측정. 투표단(state/hl_signals.json) × 변동성 맞춤 배수. 4h 봉 고가·저가로 장중 판정.
# A 포지션 비상선: 진입가 대비 반대 s% → 그 봉에서 정리(비상선 가격 + 미끄러짐 0.2%), 다음 봉 투표대로 재진입
# B 자산 비상선: 봉 시작 자산 대비 k% 손실 → 정리. 편도 0.05%, 바이낸스 실제 펀딩, 유지증거금 1.25%
import json, sys
import numpy as np, pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "/Users/juno/orca/coin")
from sweep import load, load_ext
from bot.hl_rules import analyst, rules

COST, SLIP, MM = 0.0005, 0.002, 0.0125
chosen = json.load(open("/Users/juno/orca/coin/state/hl_signals.json"))["chosen"]


def expo_series(sym):
    d, _, fd = load(sym, "1d"); h4, fb4, _ = load(sym, "4h")
    Rd, R4 = rules(d, fd, load_ext(sym)), rules(h4)
    cols = {f"일봉 {n}": Rd[n].shift(1).reindex(h4.index, method="ffill") for n in chosen["1d"]} | {f"4h {n}": R4[n] for n in chosen["4h"]}
    S = pd.DataFrame(cols).fillna(0)
    # 봇(hl_signals.combine)과 같게: 분석가별 평균의 평균 (2026-11-15 전에는 전체 평균이었다 — 그 전 기록의 5배 수치는 전체 평균 기준)
    return h4, fb4, S.T.groupby(S.columns.map(analyst)).mean().T.mean(axis=1)


def sim(h4, fb4, expo, lo, hi, tv=0.6, pos_stop=None, eq_stop=None):
    o, hi_, lo_ = h4.open.values, h4.high.values, h4.low.values
    rv = (np.log(h4.open / h4.open.shift()).rolling(120).std() * np.sqrt(6 * 365)).values
    lev = np.clip(np.nan_to_num(tv / rv, nan=1.0), lo, hi)
    pos = expo.shift(1).fillna(0).values * lev; f = fb4.values
    E, prev, entry, stops, eq, peak, mdd, dead = 1.0, 0.0, None, 0, [], 1.0, 0.0, None
    for i in range(len(h4) - 1):
        p = pos[i]
        if p and (not prev or (p > 0) != (prev > 0)):
            entry = o[i]                                           # 새 방향 진입가
        E *= 1 - abs(p - prev) * COST
        if p:
            side = 1 if p > 0 else -1
            adverse = (lo_[i] / o[i] - 1) if side > 0 else -(hi_[i] / o[i] - 1)   # 이 봉 최악 수익률(가격 기준, 음수)
            # 비상선 가격 → 이 봉 시가 대비 수익률로
            lines = []
            if pos_stop:
                lines.append(side * (entry * (1 - side * pos_stop) / o[i] - 1))
            if eq_stop:
                lines.append(-eq_stop / abs(p))
            liq = -(1 - MM * abs(p)) / abs(p)
            trig = max(lines) if lines else -9                      # 가장 먼저 닿는 선(덜 음수)
            if adverse <= liq and (not lines or liq >= trig):       # 비상선보다 청산이 먼저
                E = 0.0; dead = h4.index[i]; break
            if lines and adverse <= trig:
                E *= 1 + abs(p) * (trig - SLIP) - abs(p) * COST; stops += 1
                p = 0.0                                             # 남은 봉은 현금, 다음 봉에 투표대로 재진입
            else:
                E *= 1 + p * (o[i + 1] / o[i] - 1) - p * f[i]
        eq.append(E); peak = max(peak, E); mdd = max(mdd, 1 - E / peak); prev = p
    yrs = (h4.index[-1] - h4.index[0]).days / 365.25
    s = pd.Series(eq); k = int(len(s) * 0.7)
    oos = s.iloc[-1] / s.iloc[k] - 1 if E > 0 and s.iloc[k] > 0 else -1
    y = pd.Series(eq, index=h4.index[:len(eq)]).resample("YE").last(); y = y / y.shift(1).fillna(1) - 1
    return dict(cagr=(E ** (1 / yrs) - 1) if E > 0 else -1, mdd=mdd, oos=oos, stops=stops, dead=dead,
                years=" ".join(f"{i.year%100}:{v*100:+.0f}" for i, v in y.items()))


if __name__ == "__main__":
    for sym in ("BTCUSDT", "ETHUSDT"):
        h4, fb4, e = expo_series(sym)
        print(f"\n===== {sym}  (투표단 {sum(len(v) for v in chosen.values())}개, 목표변동성 60%)")
        for lo, hi in ((5, 50), (1, 50), (0, 3)):
            print(f"--- 배수 하한 {lo} · 상한 {hi}")
            cases = [("비상선 없음", {})] + [(f"A 포지션 −{s}%", dict(pos_stop=s / 100)) for s in (3, 4, 5, 6, 8)] \
                + [(f"B 자산 −{k}%", dict(eq_stop=k / 100)) for k in (10, 15, 20, 25)]
            for name, kw in cases:
                r = sim(h4, fb4, e, lo, hi, **kw)
                print(f"{name:14} 연 {r['cagr']*100:+6.0f}%  MDD {r['mdd']*100:4.0f}%  OOS {r['oos']*100:+6.0f}%  비상 발동 {r['stops']:4d}회"
                      + (f"  ← {r['dead']:%Y-%m-%d} 전멸" if r['dead'] is not None else "") + f" | {r['years']}")
