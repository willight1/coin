# 추세 중간에 켰을 때 바로 탈까, 새 추세(투표 부호 바뀜)부터 탈까 (2026-11-15). 규칙은 돌리기 전에 고정.
# 2020-01~2025-06 매주 월요일 00 UTC 를 '봇을 켠 날'로 두고:
#   A 지금 방식: 켠 봉부터 투표대로
#   B 새 추세 대기: 켠 봉의 투표 부호가 바뀔 때(0 포함)까지 비우고, 바뀐 봉부터 투표대로
# 공통: 지금 봇과 같게 5배 하한·변동성 배수(목표 60%, 상한 50), 완충 30%, 이익 보호(최고 이익에서 4h 종가 5% 되돌림 → 25%만),
#       4h 종가 판정·다음 봉 시가 체결, 편도 0.05%, 바이낸스 실제 펀딩, 장중 고가·저가로 청산 판정. 90일·180일 뒤 평가금액 비교.
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "/Users/juno/orca/coin")
from stops import expo_series, COST, MM

BAND, TRAIL, KEEP = 0.30, 0.05, 0.25


def run(h4, fb4, expo, i0, n, wait, lo=5, hi=50, tv=0.6):
    o, hh, ll, c = h4.open.values, h4.high.values, h4.low.values, h4.close.values
    rv = (np.log(h4.open / h4.open.shift()).rolling(120).std() * np.sqrt(6 * 365)).values
    lev = np.clip(np.nan_to_num(tv / rv, nan=1.0), lo, hi)
    tgt, f = expo.shift(1).fillna(0).values * lev, fb4.values
    E, held = 1.0, 0.0
    side, entry, best, cut = 0, 0.0, 0.0, 1.0
    first = int(np.sign(tgt[i0])); waiting = wait and first != 0
    for i in range(i0, min(i0 + n, len(h4) - 1)):
        d = int(np.sign(tgt[i]))
        if waiting and d != first:
            waiting = False
        if d != side:
            side, entry, best, cut = d, o[i], 0.0, 1.0
        p = 0.0 if waiting else tgt[i] * cut
        if held and p and np.sign(p) == np.sign(held) and abs(p - held) < BAND * abs(p):
            p = held
        E *= 1 - abs(p - held) * COST
        if p:
            adverse = (ll[i] / o[i] - 1) if p > 0 else -(hh[i] / o[i] - 1)
            if adverse <= -(1 - MM * abs(p)) / abs(p):
                return 0.0
            r = o[i + 1] / o[i] - 1
            E *= 1 + p * r - p * f[i]
            held = p * (1 + r) / (1 + p * r)
        else:
            held = 0.0
        if side and cut > KEEP and not waiting:
            fav = side * (c[i] / entry - 1); best = max(best, fav)
            if (1 + best) / (1 + fav) - 1 >= TRAIL:
                cut = KEEP
    return E


if __name__ == "__main__":
    for sym in ("BTCUSDT", "ETHUSDT"):
        h4, fb4, e = expo_series(sym)
        starts = [h4.index.searchsorted(t) for t in pd.date_range("2020-01-06", "2025-06-30", freq="W-MON", tz="UTC")]
        for days in (90, 180):
            n = days * 6
            A = np.array([run(h4, fb4, e, i, n, False) for i in starts])
            B = np.array([run(h4, fb4, e, i, n, True) for i in starts])
            q = lambda x: f"중앙 {np.median(x) * 100 - 100:+5.0f}% · 평균 {x.mean() * 100 - 100:+6.0f}% · 전멸 {np.mean(x == 0) * 100:3.0f}%"
            print(f"{sym[:3]} {days:3d}일 ({len(starts)}개 시작일)  A 바로 탐: {q(A)}  |  B 새 추세 대기: {q(B)}  |  B가 나은 시작일 {np.mean(B > A) * 100:.0f}%", flush=True)
