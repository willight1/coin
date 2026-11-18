# 바이낸스 공개 데이터(키 없음): BTCUSDT·ETHUSDT 일봉(2017-08~)·1시간봉(2017-08~)·펀딩(2019-09~) → 이 폴더에 pkl 저장
import os, sys, time
import pandas as pd, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from bot.hl_signals import coin_daily, external_daily  # noqa: E402  봇과 같은 함수로 받는다

OUT = os.getenv("SWEEP_DATA", os.path.dirname(os.path.abspath(__file__)))


def _get(url, params, tries=5):
    for k in range(tries):
        try:
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(1 + k)
    raise RuntimeError(f"바이낸스 조회 실패: {url} {params}")


def klines(sym, interval, start_ms=0):
    rows, t = [], start_ms
    while True:
        r = _get("https://api.binance.com/api/v3/klines", {"symbol": sym, "interval": interval, "startTime": t, "limit": 1000})
        if not r:
            break
        rows += r; t = r[-1][0] + 1; time.sleep(0.15)
        if len(r) < 1000:
            break
    df = pd.DataFrame(rows, columns="t o h l c v ct qv n tb tq i".split()).drop_duplicates("t")
    df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    return df.set_index("t")[["o", "h", "l", "c", "qv"]].astype(float).rename(
        columns=dict(o="open", h="high", l="low", c="close", qv="value"))


def funding(sym):
    rows, t, end = [], int(pd.Timestamp("2019-09-01", tz="UTC").timestamp() * 1000), int(time.time() * 1000)
    while t < end:
        r = _get("https://fapi.binance.com/fapi/v1/fundingRate", {"symbol": sym, "startTime": t, "endTime": end, "limit": 1000})
        if not r:
            break
        rows += r; t = r[-1]["fundingTime"] + 1; time.sleep(0.2)
    f = pd.DataFrame(rows).drop_duplicates("fundingTime"); f["t"] = pd.to_datetime(f["fundingTime"], unit="ms", utc=True)
    return f.set_index("t")["fundingRate"].astype(float)


def fetch_all(syms=("BTCUSDT", "ETHUSDT"), incremental=True):
    """저장된 pkl 이 있으면 마지막 시점부터만 받아 이어 붙인다."""
    for s in syms:
        for kind, interval in (("d", "1d"), ("h", "1h")):
            p = f"{OUT}/bn_{s}_{kind}.pkl"
            old = pd.read_pickle(p) if incremental and os.path.exists(p) else None
            start = int(old.index[-1].timestamp() * 1000) if old is not None and len(old) else 0
            new = klines(s, interval, start)
            df = pd.concat([old, new]) if old is not None else new
            df = df[~df.index.duplicated(keep="last")].sort_index()
            df.to_pickle(p)
            print(s, interval, len(df), df.index[0].date(), "~", df.index[-1], flush=True)
        funding(s).to_pickle(f"{OUT}/bn_{s}_f.pkl")
        coin_daily(s[:-4]).to_pickle(f"{OUT}/bn_{s}_x.pkl")   # 코인별 일봉 입력: 미결제약정·계정 롱숏 비율·옵션 변동성 (봇과 같은 캐시 state/hl_metrics_*.pkl)
    external_daily().to_pickle(f"{OUT}/bn_ext.pkl")       # 공포탐욕·스테이블코인 시총 (일봉 규칙용)


if __name__ == "__main__":
    fetch_all(incremental="--full" not in sys.argv)
