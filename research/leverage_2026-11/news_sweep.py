# 코인니스 속보 규칙 측정 (2026-11-15). 규칙·단어 목록은 데이터 보기 전에 고정. bt·합격 기준은 sweep.py 와 같다(일봉, 1배).
# 일봉 d(UTC 00시 시작)의 신호 = 그날 UTC 하루 동안 나온 속보만 → bt 가 다음 날 시가에 체결.
# 단어 점수는 분류 2(시세 자동 속보: "BTC $64,000 하회", "5분간 3% 상승", 청산 집계)를 뺀 제목에만. 시세 속보를 넣으면 그냥 가격 추세가 된다.
# 독자 투표(bull/bear)는 지금 누적값이라 미래 정보가 섞인다 → 합격해도 그대로 못 씀(참고).
import json, os, re, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sweep import bt, load
from bot.hl_rules import sign

SRC = "/Users/juno/orca/coin/logs/coinness.jsonl"
GOOD = ["상승", "상회", "매수", "유입", "승인", "돌파", "최고", "반등", "급등", "신고가", "채택", "매집"]
BAD = ["하락", "하회", "매도", "유출", "해킹", "소송", "규제", "급락", "파산", "기소", "거부", "폭락", "제재"]
LIQ = re.compile(r"24시간 기준으로는 ([\d.]+)억 달러")


def daily():
    rows = {}
    with open(SRC, encoding="utf-8") as f:
        for line in f:
            x = json.loads(line); rows[x["id"]] = x
    n = pd.DataFrame(rows.values())
    n["t"] = pd.to_datetime(n["t"], utc=True); n["d"] = n["t"].dt.floor("D")
    news = n[n.cat != 2]
    sc = news.title.apply(lambda s: int(any(w in s for w in GOOD)) - int(any(w in s for w in BAD)))
    liq = n.content.fillna("").str.extract(LIQ)[0].astype(float)
    g = pd.DataFrame({"score": sc.groupby(news.d).sum(), "cnt": news.groupby("d").size(),
                      "bull": n.groupby("d").bull.sum(), "bear": n.groupby("d").bear.sum(),
                      "liq24": liq.groupby(n.d).max()})
    print(f"속보 {len(n)}개, {n.t.min():%Y-%m-%d} ~ {n.t.max():%Y-%m-%d}, 청산 집계 시작 {n.t[liq.notna()].min():%Y-%m-%d}")
    return g


def news_rules(df, g):
    c = df.close; e = g.reindex(df.index)
    base = sign(c - c.rolling(50).mean())
    s = e.score.fillna(0); s7, s90 = s.rolling(7).mean(), s.rolling(90).mean()
    br = e.bull.rolling(7).sum() / (e.bull.rolling(7).sum() + e.bear.rolling(7).sum())
    brz = br - br.rolling(90).mean()
    lq = e.liq24.ffill(limit=1); z = (lq - lq.rolling(365, min_periods=90).mean()) / lq.rolling(365, min_periods=90).std()
    r1 = c / c.shift() - 1
    spike = pd.Series(np.where(z > 2, -np.sign(r1), np.nan), index=df.index).ffill(limit=2)   # 급증 뒤 3일 역방향
    return {"뉴스 점수 7일 합 부호": sign(s.rolling(7).sum()),
            "뉴스 점수 7일 평균이 90일 평균 위/아래": sign(s7 - s90),
            "이평50 + 뉴스 점수 7일 같은 방향일 때만": base.where(sign(s.rolling(7).sum()) == base, 0),
            "(미래정보) 독자 호재 비율 7일 vs 90일 순방향": sign(brz),
            "(미래정보) 독자 호재 비율 7일 vs 90일 역방향": -sign(brz),
            "청산 급증(z>2) 뒤 3일 역방향, 아니면 이평50": spike.where(spike.notna(), base)}


if __name__ == "__main__":
    g = daily()
    for sym in ("BTCUSDT", "ETHUSDT"):
        df, fb, _ = load(sym, "1d")
        first = g.index.min()
        df, fb = df[df.index >= first], fb[fb.index >= first]
        R = news_rules(df, g) | {"(기준) 이평50": sign(df.close - df.close.rolling(50).mean())}
        print(f"\n== {sym} {df.index[0]:%Y-%m-%d}~")
        for name, sig in R.items():
            x = bt(df, sig.fillna(0), fb)
            print(f"{'★' if x['ok'] else ' '} {name:34} 연{x['cagr']*100:+5.0f}% MDD{x['mdd']*100:3.0f}% OOS{x['oos']*100:+6.0f}% "
                  f"PF{x['pf']:5.2f} {x['n']:4d}회 [{'/'.join(f'{v*100:+.0f}' for v in x['seg'])}]")
