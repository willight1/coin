# 거래소(바이낸스·OKX) 카피트레이딩 상위권 자료의 진입 패턴을 규칙으로 바꿔 측정 (2026-11-16, 사용자 제공 자료). 규칙은 데이터 보기 전에 고정.
# 자료: 롱은 1시간 RSI 35~50 눌림, 숏은 1시간 RSI 50~65 반등에서 진입한 거래가 성적이 좋았고, 과매도(RSI<35) 롱은 손해, 큰 수익은 3일 이상 보유에서.
# 여기서는 롱·숏 대칭으로: 추세(전날까지 확정된 일봉 종가 vs 50일 이평) 방향일 때만, 1시간봉 RSI14 가 그 구간이면 다음 봉 시가에 진입.
# bt·합격 기준은 sweep.py 와 같다(1배, 편도 0.05%, 실제 펀딩). 1시간봉 2017-08~.
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sweep import bt, load
from bot.hl_rules import sign
from vol_whale import rsi, show


def rules(h, d, lo=(35, 50), hi=(50, 65)):
    c = h.close; r = rsi(c)
    trend = sign(d.close - d.close.rolling(50).mean()).shift(1).reindex(h.index, method="ffill").fillna(0)   # 전날 마감까지의 추세
    tv, rv, cv = trend.values, r.values, c.values
    zone = ((tv > 0) & (rv >= lo[0]) & (rv < lo[1])) | ((tv < 0) & (rv >= hi[0]) & (rv < hi[1]))

    def run(hold=None, stop=None):
        """hold = 보유 봉 수(None = 추세 끝까지), stop = 진입가 대비 역행 %(종가 기준, 다음 봉 시가 정리)."""
        out = np.zeros(len(c)); side = 0; entry = 0.0; age = 0
        for i in range(len(c)):
            if side:
                age += 1
                if tv[i] != side or (hold and age >= hold) or (stop and side * (cv[i] / entry - 1) <= -stop):
                    side = 0
            if not side and zone[i]:
                side, entry, age = int(tv[i]), cv[i], 0
            out[i] = side
        return pd.Series(out, index=c.index)

    knife = pd.Series(np.where(rv < 35, 1.0, 0.0), index=c.index).rolling(24, min_periods=1).max()      # 과매도면 24시간 롱 (추세 무시)
    return {"추세 + 1시간 RSI 눌림·반등 진입, 24시간 보유": run(24),
            "추세 + 1시간 RSI 눌림·반등 진입, 72시간 보유": run(72),
            "위(72시간) + 역행 −2% 면 정리": run(72, 0.02),
            "추세 + 1시간 RSI 눌림·반등 진입, 추세 끝까지 보유": run(),
            "과매도(RSI<35) 롱 24시간 (추세 무시)": knife}, trend


if __name__ == "__main__":
    res = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        h, fb, _ = load(sym, "1h"); d, _, _ = load(sym, "1d")
        R, base = rules(h, d)
        print(f"{sym[:3]} 1h: {h.index[0]:%Y-%m-%d}~{h.index[-1]:%Y-%m-%d} {len(h)}봉")
        for name, sig in ({"(기준) 이평50": base} | R).items():
            res.setdefault(name, {})[sym] = bt(h, sig.fillna(0), fb)
    show("거래소 카피 상위권 진입 패턴 (1시간봉, 기준 = 일봉 50일 이평 방향 그대로 보유)", res)

    # 참고(합격 판정과 무관): '추세 끝까지 보유' 변형이 구간·봉을 조금 바꿔도 같은 모습인지. 이평50 보다 나은지는 OOS 까지 같이 본다
    res, key = {}, "추세 + 1시간 RSI 눌림·반등 진입, 추세 끝까지 보유"
    for tf, lo, hi in (("1h", (35, 50), (50, 65)), ("1h", (30, 50), (50, 70)), ("1h", (40, 55), (45, 60)), ("1h", (0, 50), (50, 100)), ("4h", (35, 50), (50, 65))):
        for sym in ("BTCUSDT", "ETHUSDT"):
            h, fb, _ = load(sym, tf); d, _, _ = load(sym, "1d")
            R, base = rules(h, d, lo, hi)
            res.setdefault(f"(기준) 이평50" if (tf, lo) == ("1h", (35, 50)) else f"(기준 {tf})", {})[sym] = bt(h, base, fb)
            res.setdefault(f"{tf} 롱 {lo[0]}~{lo[1]} / 숏 {hi[0]}~{hi[1]}", {})[sym] = bt(h, R[key].fillna(0), fb)
    res.pop("(기준 1h)", None)
    show("구간·봉을 바꿔 본 것 (추세 끝까지 보유)", res)


def rule_a(h, d, k_atr=4.0, hold=120, risk=None, any_entry=False):
    """다른 대화의 '실전용 규칙 카드 A': 전날 일봉 EMA50 방향으로만. 롱 = 1시간 RSI 가 45 아래로 내려올 때(35 이상), 숏 = 55 위로 뚫을 때(65 이하).
    손절 = 진입가에서 1시간 ATR14 × 4 (봉 고가·저가가 닿으면 다음 봉 시가 정리), 안 걸리면 120시간 뒤 정리. 익절 없음.
    risk=None 이면 1배(합격 기준용), risk=0.01 이면 손절 때 계좌 1% 를 잃는 크기(원문 방식, 최대 1배)."""
    from bot.hl_rules import atr
    c = h.close; r = rsi(c); a = atr(h)
    trend = sign(d.close - d.close.ewm(span=50, adjust=False).mean()).shift(1).reindex(h.index, method="ffill").fillna(0)
    tv, rv, cv, av, hi, lo = trend.values, r.values, c.values, a.values, h.high.values, h.low.values
    out = np.zeros(len(c)); side = 0; stop = 0.0; age = 0; size = 0.0
    for i in range(1, len(c)):
        if side:
            age += 1
            if age >= hold or (side > 0 and lo[i] <= stop) or (side < 0 and hi[i] >= stop):
                side = 0
        if not side and av[i] == av[i]:
            if any_entry:                                      # 비교용: RSI 조건 없이 추세 방향이면 바로 진입
                side = int(tv[i])
            elif tv[i] > 0 and rv[i - 1] >= 45 > rv[i] >= 35:
                side = 1
            elif tv[i] < 0 and rv[i - 1] <= 55 < rv[i] <= 65:
                side = -1
            if side:
                dist = k_atr * av[i]; stop = cv[i] - side * dist; age = 0
                size = 1.0 if risk is None else min(1.0, risk / (dist / cv[i]))
        out[i] = side * size
    return pd.Series(out, index=c.index), trend


if __name__ == "__main__":
    one = lambda x: f"연{x['cagr']*100:+4.0f}% MDD{x['mdd']*100:3.0f}% OOS{x['oos']*100:+5.0f}% PF{x['pf']:4.2f} {x['n']:4d}회 [{'/'.join(f'{v*100:+.0f}' for v in x['seg'])}]{'✓' if x['ok'] else ' '}"
    print("\n===== 규칙 카드 A (손절 4 ATR·120시간 보유) — 1시간봉, 펀딩 포함")
    for sym in ("BTCUSDT", "ETHUSDT"):
        h, fb, _ = load(sym, "1h"); d, _, _ = load(sym, "1d")
        for label, hh in (("2017-08~", h), ("2021-07~", h[h.index >= "2021-07-01"])):
            ff = fb.reindex(hh.index)
            s1, tr = rule_a(hh, d); sr, _ = rule_a(hh, d, risk=0.01)
            print(f"{sym[:3]} {label}  A 1배          {one(bt(hh, s1, ff))}  (시장에 있는 시간 {float((s1 != 0).mean()):.0%})")
            print(f"{sym[:3]} {label}  A 손절당 1%    {one(bt(hh, sr, ff))}  (평균 크기 {float(sr.abs()[sr != 0].mean()):.2f}배)")
            print(f"{sym[:3]} {label}  EMA50 그대로   {one(bt(hh, tr, ff))}")
            sa, _ = rule_a(hh, d, risk=0.01, any_entry=True)
            print(f"{sym[:3]} {label}  RSI 조건 빼고 손절당 1%  {one(bt(hh, sa, ff))}  (평균 크기 {float(sa.abs()[sa != 0].mean()):.2f}배)")
