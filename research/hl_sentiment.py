"""시장 심리 기록기 — 매시간 launchd(com.juno.hlsentiment)가 돌린다. 봇 판단에는 쓰지 않는다(참고·나중 측정용).

과거 기록을 받을 수 없는 데이터를 지금부터 쌓는다:
- 하이퍼리퀴드 수익 상위 지갑(계좌 50만 달러+)의 BTC·ETH 롱/숏 인원·금액 — 이번 달 상위 60, 누적 상위 30
- 바이낸스 롱 비중(상위 트레이더 금액·계정, 전체 계정 — API가 30일치만 줌), 미결제약정, 펀딩비
- 하이퍼리퀴드 미결제약정, 펀딩비
logs/hl_sentiment.csv 에 코인당 한 줄씩 붙인다. 한 출처가 실패하면 그 칸만 비운다.

고래 지갑별 기록(2026-11-15): 위 그룹 + 실적 상위(이번 달 30 ∪ 누적 30) + 실적 약한 큰 지갑(계좌 100만+, 누적 손실, 계좌 큰 순 30)
- logs/hl_whales.jsonl : 매시 한 줄 = 지갑별 BTC·ETH 포지션(금액·진입가·청산가·배수)과 누적·이번 달 수익. 나중에 측정용.
- logs/hl_whales_latest.json : 대시보드 '고래 청산 지도'용 요약.
- 텔레그램(참고): 실적 상위 순포지션이 1시간에 크게 변하거나 뒤집힐 때, 1천만 달러+ 포지션이 새로 열리거나 정리될 때.
  지갑은 '지금' 순위로 고르므로 이 기록으로 과거를 재면 안 된다(쌓인 뒤 그 시점 순위로만).
"""

from __future__ import annotations

import csv
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import json

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "logs", "hl_sentiment.csv")
HL = "https://api.hyperliquid.xyz/info"
BN = "https://fapi.binance.com"
COINS = ("BTC", "ETH")
MIN_ACCOUNT = 500_000
GROUPS = {"m60": ("month", 60), "all30": ("allTime", 30)}
KST = timezone(timedelta(hours=9))
WHALES = os.path.join(ROOT, "logs", "hl_whales.jsonl")
LATEST = os.path.join(ROOT, "logs", "hl_whales_latest.json")
ALERTS = os.path.join(ROOT, "logs", "hl_whale_alerts.jsonl")      # 텔레그램으로 보낸 알림 (대시보드에 표시)
SMART_N, WEAK_N, WEAK_MIN = 30, 30, 1_000_000
NET_PCT, NET_USD, BIG_USD = 0.20, 20e6, 10e6      # 알림: 순포지션 변화(직전 총액의 20% 이상 & 2천만$+), 단일 포지션 1천만$+
MAP_PCT = 15                                       # 청산 지도: 현재가 ±15%, 1% 칸
FIELDS = ["time", "coin", "price", "hl_oi_usd", "hl_funding_1h", "bn_oi_usd", "bn_funding_8h",
          "bn_top_pos_long", "bn_top_acc_long", "bn_global_long"] + \
         [f"{g}_{k}" for g in GROUPS for k in ("long_n", "short_n", "long_usd", "short_usd")]


def get(url, **params):
    r = requests.get(url, params=params, timeout=20); r.raise_for_status(); return r.json()


def hl(body):
    for k in range(4):
        r = requests.post(HL, json=body, timeout=15)
        if r.status_code != 429:
            r.raise_for_status(); return r.json()
        time.sleep(2 + k)
    r.raise_for_status()


def whales(rows: dict) -> None:
    board = [w for w in get("https://stats-data.hyperliquid.xyz/Mainnet/leaderboard")["leaderboardRows"]
             if float(w["accountValue"]) >= MIN_ACCOUNT]
    pnl = lambda w, win: float(dict(w["windowPerformances"])[win]["pnl"])
    top = lambda win, n: [w["ethAddress"] for w in sorted(board, key=lambda w: -pnl(w, win))[:n]]
    groups = {g: top(win, n) for g, (win, n) in GROUPS.items()}
    groups["smart"] = list(dict.fromkeys(top("month", SMART_N) + top("allTime", SMART_N)))
    groups["weak"] = [w["ethAddress"] for w in sorted((w for w in board if float(w["accountValue"]) >= WEAK_MIN and pnl(w, "allTime") < 0),
                                                       key=lambda w: -float(w["accountValue"]))[:WEAK_N]]
    info = {w["ethAddress"]: w for w in board}
    pos, wallets = {}, {}                                      # pos: 지갑 → {코인: 평가금액(롱+, 숏−)}
    for addr in set().union(*groups.values()):
        st = hl({"type": "clearinghouseState", "user": addr})
        pos[addr], det = {}, {}
        for p in st["assetPositions"]:
            q = p["position"]; v = float(q["positionValue"]) * (1 if float(q["szi"]) > 0 else -1)
            pos[addr][q["coin"]] = v
            if q["coin"] in COINS:
                det[q["coin"]] = {"usd": round(v), "entry": float(q["entryPx"]), "liq": float(q["liquidationPx"]) if q.get("liquidationPx") else None,
                                  "lev": q["leverage"]["value"]}
        w = info[addr]
        wallets[addr] = {"name": w.get("displayName"), "acct": round(float(w["accountValue"])), "pnl_all": round(pnl(w, "allTime")),
                         "pnl_month": round(pnl(w, "month")), "g": [g for g, a in groups.items() if addr in a], "pos": det}
        time.sleep(0.1)
    for coin, row in rows.items():
        for g, (win, n) in GROUPS.items():
            v = [pos[a].get(coin, 0.0) for a in groups[g]]
            row.update({f"{g}_long_n": sum(x > 0 for x in v), f"{g}_short_n": sum(x < 0 for x in v),
                        f"{g}_long_usd": round(sum(x for x in v if x > 0)), f"{g}_short_usd": round(-sum(x for x in v if x < 0))})
    cur = {"time": rows[COINS[0]]["time"], "price": {c: r.get("price") for c, r in rows.items()}, "wallets": wallets}
    prev = last_snapshot()
    with open(WHALES, "a", encoding="utf-8") as f:
        f.write(json.dumps(cur, ensure_ascii=False) + "\n")
    with open(LATEST, "w", encoding="utf-8") as f:
        json.dump(summary(cur), f, ensure_ascii=False)
    msgs = changes(prev, cur) if prev else []
    if msgs:
        with open(ALERTS, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": cur["time"], "msgs": msgs}, ensure_ascii=False) + "\n")
        from bot import notifier
        notifier.notify("[고래 · 참고용]\n" + "\n\n".join(msgs))
        print("알림:", *msgs, sep="\n")


def last_snapshot() -> dict | None:
    if not os.path.exists(WHALES):
        return None
    with open(WHALES, "rb") as f:
        f.seek(0, 2); n = f.tell(); f.seek(max(0, n - 2_000_000))
        lines = f.read().decode("utf-8", "ignore").strip().split("\n")
    try:
        return json.loads(lines[-1])
    except Exception:
        return None


def who(a: str, w: dict) -> str:
    tag = "실적 상위" if "smart" in w["g"] else "실적 약함" if w["pnl_all"] < 0 else "상위"
    return f"{w['name'] or a[:8]}({tag}, 누적 {usd(w['pnl_all'], sign=True)})"


def usd(x: float, sign: bool = False) -> str:
    s = "+" if sign and x > 0 else "-" if x < 0 else ""
    a = abs(x)
    return f"{s}${a / 1e8:.2f}억" if a >= 1e8 else f"{s}${a / 1e4:,.0f}만"


def side(x: float) -> str:
    return "롱" if x > 0 else "숏" if x < 0 else "없음"


def changes(prev: dict, cur: dict) -> list[str]:
    """직전 시간과 비교한 알림 문구. 두 스냅샷에 모두 있는 지갑만 비교한다."""
    out, P, C = [], prev["wallets"], cur["wallets"]
    both = [a for a in C if a in P]
    for coin in COINS:
        smart = [a for a in both if "smart" in C[a]["g"]]
        g = lambda W, a: W[a]["pos"].get(coin, {}).get("usd", 0)
        n0, n1 = sum(g(P, a) for a in smart), sum(g(C, a) for a in smart)
        gross0 = sum(abs(g(P, a)) for a in smart)
        flip = n0 and n1 and (n0 > 0) != (n1 > 0)
        if flip or abs(n1 - n0) >= max(NET_USD, NET_PCT * gross0):
            movers = sorted(smart, key=lambda a: -abs(g(C, a) - g(P, a)))[:3]
            lines = [f"🐋 {coin} 실적 상위 {len(smart)}곳 순포지션 {side(n0)} {usd(abs(n0))} → {side(n1)} {usd(abs(n1))}" + (" (방향 바뀜)" if flip else "")]
            lines += [f"· {who(a, C[a])}: {side(g(P, a))} {usd(abs(g(P, a)))} → {side(g(C, a))} {usd(abs(g(C, a)))}" for a in movers if g(C, a) != g(P, a)]
            out.append("\n".join(lines))
        for a in both:
            p0, p1 = g(P, a), g(C, a)
            q = C[a]["pos"].get(coin, {})
            if abs(p1) >= BIG_USD and (p0 == 0 or (p0 > 0) != (p1 > 0)):
                liq = f", 청산 ${q['liq']:,.0f}" if q.get("liq") else ""
                out.append(f"🆕 {coin} {side(p1)} {usd(abs(p1))} 새로 열림 @ ${q['entry']:,.1f} {q['lev']}배{liq}\n· {who(a, C[a])}")
            elif abs(p0) >= BIG_USD and (p1 == 0 or (p0 > 0) != (p1 > 0)):
                out.append(f"✅ {coin} {side(p0)} {usd(abs(p0))} 정리" + (f" 후 {side(p1)} {usd(abs(p1))}" if p1 else "") + f"\n· {who(a, C[a])}")
    return out


def summary(cur: dict) -> dict:
    """대시보드용: 그룹별 롱·숏 합, 현재가 ±15% 청산 지도(1% 칸), 큰 포지션 목록."""
    res = {"time": cur["time"], "coins": {}}
    W = cur["wallets"]
    for coin in COINS:
        px = cur["price"].get(coin)
        ps = [(a, w, w["pos"][coin]) for a, w in W.items() if coin in w["pos"]]
        grp = {g: {"long": sum(q["usd"] for _, w, q in ps if g in w["g"] and q["usd"] > 0),
                   "short": -sum(q["usd"] for _, w, q in ps if g in w["g"] and q["usd"] < 0),
                   "n": sum(1 for _, w, _ in ps if g in w["g"])} for g in ("smart", "weak")}
        bins = {}
        if px:
            for _, w, q in ps:
                if q["liq"]:
                    k = int((q["liq"] / px - 1) * 100 // 1)
                    if -MAP_PCT <= k < MAP_PCT:
                        b = bins.setdefault(k, {"pct": k, "long": 0, "short": 0})
                        b["long" if q["usd"] > 0 else "short"] += abs(q["usd"])
        big = sorted(ps, key=lambda x: -abs(x[2]["usd"]))[:10]
        res["coins"][coin] = {"price": px, "groups": grp, "bins": sorted(bins.values(), key=lambda b: b["pct"]),
                              "big": [{"who": w["name"] or a[:8], "tag": "smart" if "smart" in w["g"] else "weak" if w["pnl_all"] < 0 else "",
                                       "pnl_all": w["pnl_all"], **q} for a, w, q in big]}
    return res


def hl_ctx(rows: dict) -> None:
    meta, ctxs = hl({"type": "metaAndAssetCtxs"})
    for a, c in zip(meta["universe"], ctxs):
        if a["name"] in rows:
            px = float(c["markPx"])
            rows[a["name"]].update(price=px, hl_oi_usd=round(float(c["openInterest"]) * px), hl_funding_1h=c["funding"])


def binance(rows: dict) -> None:
    for coin, row in rows.items():
        s = coin + "USDT"
        last = lambda path: get(BN + path, symbol=s, period="1h", limit=1)[-1]
        row["bn_top_pos_long"] = last("/futures/data/topLongShortPositionRatio")["longAccount"]
        row["bn_top_acc_long"] = last("/futures/data/topLongShortAccountRatio")["longAccount"]
        row["bn_global_long"] = last("/futures/data/globalLongShortAccountRatio")["longAccount"]
        row["bn_oi_usd"] = round(float(last("/futures/data/openInterestHist")["sumOpenInterestValue"]))
        row["bn_funding_8h"] = get(BN + "/fapi/v1/premiumIndex", symbol=s)["lastFundingRate"]


def main() -> None:
    now = datetime.now(KST).isoformat(timespec="seconds")
    rows = {c: {"time": now, "coin": c} for c in COINS}
    for step in (hl_ctx, binance, whales):
        try:
            step(rows)
        except Exception as e:                                 # 한 출처 실패 → 그 칸만 비움
            print(f"{now} {step.__name__} 실패: {e}", file=sys.stderr)
    new = not os.path.exists(OUT)
    with open(OUT, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows.values())
    for r in rows.values():
        print(r)


def selftest() -> None:
    w = lambda usd, g=("smart",): {"name": "A", "acct": 1, "pnl_all": 2e8, "pnl_month": 0, "g": list(g),
                                   "pos": {"BTC": {"usd": usd, "entry": 6e4, "liq": 7e4, "lev": 10}} if usd else {}}
    prev = {"wallets": {"a": w(-50e6), "b": w(-30e6), "c": w(0, ("weak",))}}
    assert changes(prev, prev) == []
    cur = {"wallets": {"a": w(-20e6), "b": w(-30e6), "c": w(15e6, ("weak",))}}
    m = changes(prev, cur)
    assert any("순포지션" in x for x in m) and any("새로 열림" in x for x in m), m
    assert any("새로 열림" in x for x in changes({"wallets": {"a": w(-50e6)}}, {"wallets": {"a": w(40e6)}}))
    s = summary({"time": "t", "price": {"BTC": 65000, "ETH": None}, "wallets": cur["wallets"]})
    assert s["coins"]["BTC"]["bins"] and s["coins"]["BTC"]["groups"]["smart"]["short"] == 50e6
    print("selftest OK")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
