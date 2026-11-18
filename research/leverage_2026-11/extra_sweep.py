# 새 데이터 6종 측정 (2026-11-14). 규칙은 미리 고정, 합격 기준·비용·체결은 sweep.py 와 같다(bt 재사용).
# 1 매수 체결 비중(바이낸스 테이커 매수 거래대금) 1d·4h  3 공포탐욕지수  4 코인베이스 프리미엄
# 5 선물 프리미엄 지수  6 스테이블코인 시가총액   (2 펀딩 규칙은 sweep.py 에서 이미 잼)
# 데이터는 이 폴더 bn_{sym}_xb_{d,h}.pkl(매수 비중) · ext_daily.pkl(나머지)에 저장. --refresh 로 다시 받기.
import os, sys, time
import pandas as pd, requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sweep import D, bt, load
from fetch_bn import _get
from bot.hl_rules import sign, state

SYMS = ("BTCUSDT", "ETHUSDT")


def klines_buy(sym, interval):
    rows, t = [], 0
    while True:
        r = _get("https://api.binance.com/api/v3/klines", {"symbol": sym, "interval": interval, "startTime": t, "limit": 1000})
        if not r:
            break
        rows += r; t = r[-1][0] + 1; time.sleep(0.12)
        if len(r) < 1000:
            break
    df = pd.DataFrame(rows, columns="t o h l c v ct qv n tb tq i".split()).drop_duplicates("t")
    df.index = pd.to_datetime(df["t"], unit="ms", utc=True)
    return df[["qv", "tq"]].astype(float)


def coinbase(product):
    out, end = [], pd.Timestamp.now(tz="UTC").floor("D")
    start = pd.Timestamp("2017-08-01", tz="UTC")
    while start < end:
        stop = min(start + pd.Timedelta(days=299), end)
        r = requests.get(f"https://api.exchange.coinbase.com/products/{product}/candles",
                         params=dict(granularity=86400, start=start.isoformat(), end=stop.isoformat()), timeout=20)
        r.raise_for_status(); out += r.json(); start = stop; time.sleep(0.4)
    s = pd.DataFrame(out, columns="t low high open close vol".split()).drop_duplicates("t")
    return pd.Series(s.close.values, index=pd.to_datetime(s.t, unit="s", utc=True)).sort_index()


def premium(sym):
    rows, t = [], int(pd.Timestamp("2019-09-01", tz="UTC").timestamp() * 1000)
    while True:
        r = _get("https://fapi.binance.com/fapi/v1/premiumIndexKlines", {"symbol": sym, "interval": "1d", "startTime": t, "limit": 1500})
        if not r:
            break
        rows += r; t = r[-1][0] + 1; time.sleep(0.2)
        if len(r) < 1500:
            break
    return pd.Series([float(x[4]) for x in rows], index=pd.to_datetime([x[0] for x in rows], unit="ms", utc=True))


def fetch(refresh=False):
    p = f"{D}/ext_daily.pkl"
    if refresh or not os.path.exists(p):
        fng = requests.get("https://api.alternative.me/fng/", params=dict(limit=0, format="json"), timeout=20).json()["data"]
        st = requests.get("https://stablecoins.llama.fi/stablecoincharts/all", timeout=30).json()
        ext = pd.DataFrame({
            "fng": pd.Series({pd.Timestamp(int(x["timestamp"]), unit="s", tz="UTC"): float(x["value"]) for x in fng}),
            "stable": pd.Series({pd.Timestamp(int(x["date"]), unit="s", tz="UTC"): float(x["totalCirculatingUSD"].get("peggedUSD", 0)) for x in st}),
            "cb_BTCUSDT": coinbase("BTC-USD"), "cb_ETHUSDT": coinbase("ETH-USD"),
            "prem_BTCUSDT": premium("BTCUSDT"), "prem_ETHUSDT": premium("ETHUSDT")}).sort_index()
        ext.to_pickle(p)
    for s in SYMS:
        for k, iv in (("d", "1d"), ("h", "1h")):
            q = f"{D}/bn_{s}_xb_{k}.pkl"
            if refresh or not os.path.exists(q):
                klines_buy(s, iv).to_pickle(q)
    return pd.read_pickle(p)


def zscore(x):
    x7 = x.rolling(7).mean()
    return (x7 - x7.rolling(365, min_periods=90).mean()) / x7.rolling(365, min_periods=90).std()


def buy_rules(c, qv, tq):
    br = tq.rolling(20).sum() / qv.rolling(20).sum()
    cvd = (2 * tq - qv).cumsum()
    base = sign(c - c.rolling(50).mean())
    b = sign(br - 0.5)
    return {"매수 체결 비중 20봉 50% 위/아래": b,
            "누적 순매수(CVD) 20봉 기울기": sign(cvd - cvd.shift(20)),
            "이평50 + 매수 비중 같은 방향일 때만": base.where(b == base, 0)}


def daily_rules(d, ext, sym):
    c = d["close"]; e = ext.reindex(d.index).ffill()
    base = sign(c - c.rolling(50).mean())
    fng = e["fng"]
    cbp = e[f"cb_{sym}"] / c - 1
    z = zscore(e[f"prem_{sym}"])
    st = e["stable"]
    return {"공포탐욕 역방향(20미만 롱/80초과 숏)": state(fng < 20, fng > 50, fng > 80, fng < 50),
            "이평50 + 탐욕 80초과면 롱 절반": base.where(~((base > 0) & (fng > 80)), 0.5),
            "코인베이스 프리미엄 20일 평균 부호": sign(cbp.rolling(20).mean()),
            "선물 프리미엄 과열 역방향(z>1 숏/z<-1 롱)": state(z < -1, z > 0, z > 1, z < 0),
            "선물 프리미엄 순방향(z>1 롱/z<-1 숏)": state(z > 1, z < 0, z < -1, z > 0),
            "스테이블코인 시총 30일 증가 부호": sign(st / st.shift(30) - 1)}


def main():
    ext = fetch("--refresh" in sys.argv)
    res = {}
    for tf in ("1d", "4h"):
        for sym in SYMS:
            df, fb, _ = load(sym, tf)
            xb = pd.read_pickle(f"{D}/bn_{sym}_xb_{'d' if tf == '1d' else 'h'}.pkl")
            if tf == "4h":
                xb = xb.resample("4h").sum()
            xb = xb.reindex(df.index)
            R = {f"{tf} {k}": v for k, v in buy_rules(df["close"], xb.qv, xb.tq).items()}
            if tf == "1d":
                R |= {f"1d {k}": v for k, v in daily_rules(df, ext, sym).items()}
            for name, sig in R.items():
                res.setdefault(name, {})[sym] = bt(df, sig.fillna(0), fb)
    for name, r in res.items():
        ok = all(x["ok"] for x in r.values())
        cells = " | ".join(f"{s[:3]} 연{x['cagr']*100:+5.0f}% MDD{x['mdd']*100:3.0f}% OOS{x['oos']*100:+6.0f}% PF{x['pf']:4.2f} {x['n']:4d}회 "
                           f"[{'/'.join(f'{v*100:+.0f}' for v in x['seg'])}]{'✓' if x['ok'] else ' '}" for s, x in r.items())
        print(f"{'★' if ok else ' '} {name:34} | {cells}")
    for sym in SYMS:                                               # 기준: 이평50 (같은 bt)
        df, fb, _ = load(sym, "1d"); c = df.close
        x = bt(df, sign(c - c.rolling(50).mean()), fb)
        print(f"  (기준) 1d 이동평균50 {sym[:3]}: 연{x['cagr']*100:+.0f}% MDD{x['mdd']*100:.0f}% OOS{x['oos']*100:+.0f}% PF{x['pf']:.2f}")


if __name__ == "__main__":
    main()
