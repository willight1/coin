"""하이퍼리퀴드 상위 지갑 BTC 포지션 일일 스냅샷 (매매에 관여하지 않음).

매일 08:45 launchd(com.juno.hlsnapshot)가 실행한다. 지갑 선택은 수익 여부와 무관하게 넓게 잡고
그날의 손익값을 같이 저장한다 — 나중에 '그날 기준 고수'를 다시 고를 수 있어야 순환 오류가 없다.

  state/hl_snapshots/YYYY-MM-DD.json.gz  지갑별 원자료
  state/hl_snapshots/summary.csv          하루 한 줄 요약
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import time
from datetime import datetime

import requests

from bot.paths import PROJECT_ROOT

OUT_DIR = os.path.join(PROJECT_ROOT, "state", "hl_snapshots")
LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
INFO = "https://api.hyperliquid.xyz/info"


def _get(fn, tries=6):
    for k in range(tries):
        try:
            return fn()
        except Exception as e:  # 아침 DNS 끊김 대비
            print(f"재시도 {k + 1}/{tries}: {e}", flush=True)
            time.sleep(min(60, 5 * 2**k))
    raise RuntimeError("하이퍼리퀴드 응답 없음")


def _post(body):
    def f():
        r = requests.post(INFO, json=body, timeout=30)
        if r.status_code == 429:
            raise RuntimeError("429")
        r.raise_for_status()
        return r.json()
    return _get(f)


def select(rows):
    """계좌 10만$ 이상, 누적 거래량 5천만$ 이상, 월 회전율 100배 미만(마켓메이커 제외). 손익 부호는 보지 않는다."""
    out = []
    for r in rows:
        p = dict(r["windowPerformances"])
        av = float(r["accountValue"])
        if av < 1e5 or float(p["allTime"]["vlm"]) < 5e7 or float(p["month"]["vlm"]) / av >= 100:
            continue
        out.append({
            "addr": r["ethAddress"], "name": r.get("displayName"), "av": av,
            **{f"pnl_{k}": float(p[k]["pnl"]) for k in ("week", "month", "allTime")},
            "vlm_month": float(p["month"]["vlm"]),
        })
    return out


def is_smart(w):
    return w["pnl_week"] > 0 and w["pnl_month"] > 0 and w["pnl_allTime"] >= 1e6


def summarize(ws):
    sm = [w for w in ws if is_smart(w) and w["btc_ntl"]]
    long_ = sum(w["btc_ntl"] for w in sm if w["btc_ntl"] > 0)
    short = -sum(w["btc_ntl"] for w in sm if w["btc_ntl"] < 0)
    gross = long_ + short
    return {
        "wallets": len(ws), "smart_btc": len(sm),
        "smart_long_cnt": sum(w["btc_ntl"] > 0 for w in sm),
        "smart_long_usd": round(long_), "smart_short_usd": round(short),
        "smart_net_share": round((long_ - short) / gross, 4) if gross else "",
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(OUT_DIR, f"{today}.json.gz")
    if os.path.exists(path):
        print("오늘 스냅샷 있음", path)
        return

    rows = _get(lambda: requests.get(LEADERBOARD, timeout=180).json()["leaderboardRows"])
    ws = select(rows)
    btc_px = float(_post({"type": "allMids"})["BTC"])
    failed = 0
    for i, w in enumerate(ws):
        try:
            st = _post({"type": "clearinghouseState", "user": w["addr"]})
        except RuntimeError:  # 지갑 하나 때문에 하루치를 통째로 잃지 않게. 요약에선 빠진다(btc_ntl=None)
            failed += 1
            w.update(btc_szi=None, btc_ntl=None, btc_entry=None, btc_lev=None)
            if failed > len(ws) // 10:
                raise
            continue
        pos = next((a["position"] for a in st.get("assetPositions", []) if a["position"]["coin"] == "BTC"), None)
        szi = float(pos["szi"]) if pos else 0.0
        w.update(btc_szi=szi, btc_ntl=round(szi * btc_px, 2),
                 btc_entry=float(pos["entryPx"]) if pos else None,
                 btc_lev=pos["leverage"]["value"] if pos else None)
        time.sleep(0.15)  # 가중치 2 × 400회/분 ≈ 800 < 한도 1,200
        if i % 500 == 0:
            print(i, len(ws), flush=True)

    with gzip.open(path, "wt") as f:
        json.dump({"date": today, "time": datetime.now().isoformat(timespec="seconds"),
                   "btc_usd": btc_px, "wallets": ws}, f)

    s = {"date": today, "btc_usd": btc_px, **summarize(ws)}
    sp = os.path.join(OUT_DIR, "summary.csv")
    new = not os.path.exists(sp)
    with open(sp, "a", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(s))
        if new:
            wr.writeheader()
        wr.writerow(s)
    print("완료", s, "실패 지갑", failed, flush=True)


if __name__ == "__main__":
    main()
