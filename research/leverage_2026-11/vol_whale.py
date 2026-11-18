# 거래량 정체·매집 규칙과 '상위 지갑 진입 패턴' 규칙 측정 (2026-11-16, 사용자 제안). 규칙은 데이터 보기 전에 고정.
# bt·합격 기준은 sweep.py 와 같다(1배, 편도 0.05%, 실제 펀딩, 신호 봉 마감 → 다음 봉 시가).
# 합격: OOS>0·PF>1.2·3구간 중 2개+·30회+ BTC·ETH 모두. '이평50 +' 변형은 같은 기간 이평50 보다 OOS 가 낮지 않을 것.
#
# [거래량] 거래량 터짐 = 거래대금 20봉 z > 2. 고점 정체 = 터짐 + 종가가 20봉 고가의 3% 안 + 몸통(종가−시가) ≤ 0.25 ATR14.
#          저점 버팀 = 터짐 + 종가가 20봉 저가의 3% 안 + 몸통 ≥ −0.25 ATR14. 사건 뒤 10봉 동안 적용.
# [지갑]   하루 단위 복원 자료의 패턴: 추세 방향으로만, 반등이 꺾인 자리(30일 고점에서 −3~−8%)에서 진입, 역행 때 나눠 추가, 수익 구간에서 나눠 정리,
#          롱은 투매 뒤(RSI 30 아래·30일 −20%)에만. 자료 기간이 −44% 하락장이라 여기서는 롱·숏 대칭으로 적었다.
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sweep import bt, load
from bot.hl_rules import atr, sign

HOLD = 10


def after(ev, n=HOLD):
    """사건 뒤 n봉(사건 봉 포함) 동안 True."""
    return ev.astype(float).rolling(n, min_periods=1).max() > 0


def rsi(c, n=14):
    d = c.diff(); up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(); dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def vol_rules(df, z=2.0, band=0.03, body=0.25):
    c, a = df.close, atr(df)
    base = sign(c - c.rolling(50).mean())
    v = df.value; burst = (v - v.rolling(20).mean()) / v.rolling(20).std() > z
    top = burst & (c >= df.high.rolling(20).max() * (1 - band)) & (c - df.open <= body * a)
    bot = burst & (c <= df.low.rolling(20).min() * (1 + band)) & (c - df.open >= -body * a)
    T, B = after(top), after(bot)
    def cut(k):                                               # 고점 정체 뒤엔 롱을, 저점 버팀 뒤엔 숏을 k 배로
        return base.where(~(((base > 0) & T) | ((base < 0) & B)), base * k)
    ev = pd.Series(np.where(bot, 1.0, np.where(top, -1.0, np.nan)), index=df.index)
    return {"이평50 + 고점 정체면 롱 절반·저점 버팀이면 숏 절반": cut(0.5),
            "이평50 + 고점 정체면 롱 정리·저점 버팀이면 숏 정리": cut(0.0),
            "저점 버팀 뒤 10봉 롱·고점 정체 뒤 10봉 숏": B * 1.0 - T * 1.0,
            "저점 버팀이면 롱·고점 정체면 숏 (반대 사건까지 보유)": ev.ffill().fillna(0)}, base, int(top.sum()), int(bot.sum())


def whale_rules(df):
    c = df.close; ma = c.rolling(50).mean(); base = sign(c - ma)
    hh, ll = df.high.rolling(30).max(), df.low.rolling(30).min()
    dn_zone = (c < ma) & (c / hh - 1 <= -0.03) & (c / hh - 1 >= -0.08)        # 반등이 꺾인 자리
    up_zone = (c > ma) & (c / ll - 1 >= 0.03) & (c / ll - 1 <= 0.08)
    cv, mv, dz, uz = c.values, ma.values, dn_zone.values, up_zone.values

    def run(add=False, trim=False):
        out = np.zeros(len(c)); side = 0; entry = 0.0; size = 0.0
        for i in range(len(c)):
            if side and (cv[i] - mv[i]) * side < 0:                           # 추세 끝(이평50 반대편)
                side, size = 0, 0.0
            if not side and (dz[i] or uz[i]):
                side, entry, size = (-1 if dz[i] else 1), cv[i], (0.5 if add else 1.0)
            elif side:
                fav = side * (cv[i] / entry - 1)
                if add and fav <= -0.10: size = max(size, 1.0)                # 역행 −5%·−10% 에서 0.25 씩 추가(물타기)
                elif add and fav <= -0.05: size = max(size, 0.75)
                if trim and fav >= 0.10: size = min(size, 0.5)                # +10% 에서 절반 정리
            out[i] = side * size
        return pd.Series(out, index=c.index)

    r = rsi(c); panic = (r < 30) & (c / c.shift(30) - 1 <= -0.20)
    longs = pd.Series(np.where(panic, 1.0, np.where(r > 50, 0.0, np.nan)), index=c.index).ffill().fillna(0)
    return {"추세 방향 + 되돌림 자리에서만 진입 (이평50 이탈까지 보유)": run(),
            "위 + 절반으로 시작해 역행 때 나눠 추가": run(add=True),
            "위 + 수익 +10% 에서 절반 정리": run(trim=True),
            "투매 뒤 롱 (RSI<30·30일 −20% → RSI 50 회복까지)": longs,
            "이평50 + 투매 뒤 롱 (숏 대신)": base.where(longs == 0, 1.0)}, base


def show(title, res):
    print(f"\n===== {title}")
    b = res.pop("(기준) 이평50")
    line = lambda r: " | ".join(f"{s[:3]} 연{x['cagr']*100:+4.0f}% MDD{x['mdd']*100:3.0f}% OOS{x['oos']*100:+5.0f}% PF{x['pf']:4.2f} {x['n']:3d}회 [{'/'.join(f'{v*100:+.0f}' for v in x['seg'])}]{'✓' if x['ok'] else ' '}" for s, x in r.items())
    print("  (기준) 이평50".ljust(44) + line(b))
    for name, r in res.items():
        ok = all(x["ok"] for x in r.values()) and (not name.startswith("이평50") or all(r[s]["oos"] >= b[s]["oos"] for s in r))
        print(f"{'★' if ok else ' '} {name:40} | " + line(r), flush=True)


if __name__ == "__main__":
    # 일봉은 처음 정의로 사건이 거의 없어(저점 버팀 0번) 느슨한 정의(z>1.5·5% 안·몸통 0.5 ATR)를 한 번 더 잰다. 이게 마지막 — 더 고치지 않는다
    for tf, kw in (("1d", {}), ("4h", {}), ("1d", dict(z=1.5, band=0.05, body=0.5))):
        res = {}
        for sym in ("BTCUSDT", "ETHUSDT"):
            df, fb, _ = load(sym, tf)
            R, base, nt, nb = vol_rules(df, **kw)
            print(f"{sym[:3]} {tf}{' 느슨한 정의' if kw else ''}: {df.index[0]:%Y-%m-%d}~{df.index[-1]:%Y-%m-%d} {len(df)}봉 · 고점 정체 {nt}번 · 저점 버팀 {nb}번")
            for name, sig in ({"(기준) 이평50": base} | R).items():
                res.setdefault(name, {})[sym] = bt(df, sig.fillna(0), fb)
        show(f"거래량 정체·매집 ({tf}{', 느슨한 정의' if kw else ''})", res)
    res = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        df, fb, _ = load(sym, "1d")
        R, base = whale_rules(df)
        for name, sig in ({"(기준) 이평50": base} | R).items():
            res.setdefault(name, {})[sym] = bt(df, sig.fillna(0), fb)
    show("상위 지갑 진입 패턴 (일봉)", res)
