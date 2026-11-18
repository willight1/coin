# '매집·분산' 측정 (2026-11-16, 사용자 질문: 거래량·지표로 "매집하고 있네"가 보이지 않나). 규칙은 데이터 보기 전에 고정.
# 입력: 바이낸스 현물 일봉의 시장가 매수 거래대금(takerBuyQuote). 순매수 = 시장가 매수 − 시장가 매도 = 2×매수 − 전체.
# 흐름20 = 최근 20일 순매수 합 ÷ 거래대금 합. z = 흐름20 의 180일 z.
# 매집 = 가격은 20일 전보다 안 올랐는데(≤0) 흐름 z > 1.  분산 = 가격은 안 내렸는데(≥0) 흐름 z < −1.
# bt·합격 기준은 sweep.py 와 같다. '이평50 +' 변형은 같은 기간 이평50 보다 OOS 가 낮지 않을 것.
import os, sys, time
import numpy as np, pandas as pd, requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sweep import D, bt, load
from bot.hl_rules import sign
from vol_whale import show


def taker(sym):
    p = f"{D}/bn_{sym}_taker.pkl"
    if not os.path.exists(p):
        rows, start = [], int(pd.Timestamp("2017-08-01", tz="UTC").timestamp() * 1000)
        while True:
            r = requests.get("https://api.binance.com/api/v3/klines", params={"symbol": sym, "interval": "1d", "startTime": start, "limit": 1000}, timeout=20).json()
            if not r:
                break
            rows += r; start = r[-1][0] + 86400_000
            if len(r) < 1000:
                break
            time.sleep(0.3)
        df = pd.DataFrame({"tot": [float(x[7]) for x in rows], "buy": [float(x[10]) for x in rows]},
                          index=pd.to_datetime([x[0] for x in rows], unit="ms", utc=True))
        df[~df.index.duplicated()].iloc[:-1].to_pickle(p)                # 아직 안 끝난 오늘 봉은 뺀다
    return pd.read_pickle(p)


def rules(df, t):
    c = df.close; base = sign(c - c.rolling(50).mean()); t = t.reindex(df.index)
    net = 2 * t.buy - t.tot
    flow = net.rolling(20).sum() / t.tot.rolling(20).sum()
    z = (flow - flow.rolling(180).mean()) / flow.rolling(180).std()
    dp = c / c.shift(20) - 1
    acc, dist = (dp <= 0) & (z > 1), (dp >= 0) & (z < -1)
    ev = pd.Series(np.where(acc, 1.0, np.where(dist, -1.0, np.nan)), index=df.index)
    return {"순매수 흐름 20일 부호": sign(flow),
            "순매수 흐름 z 순방향(>1 롱/<-1 숏)": (z > 1) * 1.0 - (z < -1) * 1.0,
            "이평50 + 매집이면 숏 절반·분산이면 롱 절반": base.where(~(((base < 0) & acc) | ((base > 0) & dist)), base * 0.5),
            "이평50 + 매집이면 숏 정리·분산이면 롱 정리": base.where(~(((base < 0) & acc) | ((base > 0) & dist)), 0),
            "매집이면 롱·분산이면 숏, 아니면 이평50": ev.fillna(base),
            "매집이면 롱·분산이면 숏 (그 밖엔 현금)": ev.fillna(0)}, base, int(acc.sum()), int(dist.sum())


if __name__ == "__main__":
    res = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        df, fb, _ = load(sym, "1d"); t = taker(sym)
        R, base, na, nd = rules(df, t)
        t0 = t.index[0] + pd.Timedelta(days=200); cut = lambda s: s[s.index >= t0]
        print(f"{sym[:3]}: 입력 {t.index[0]:%Y-%m-%d}~{t.index[-1]:%Y-%m-%d}, 평가 {t0:%Y-%m-%d}~ · 매집 {na}일 · 분산 {nd}일")
        for name, sig in ({"(기준) 이평50": base} | R).items():
            res.setdefault(name, {})[sym] = bt(cut(df), cut(sig.fillna(0)), cut(fb))
    show("시장가 순매수로 본 매집·분산 (일봉)", res)
