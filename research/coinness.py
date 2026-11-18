"""코인니스 속보 수집기 — launchd(com.juno.coinness)가 10분마다 돌린다. 봇 판단에는 쓰지 않는다(참고·측정용).

api.coinness.com/feed/v3/breaking-news (키 없음, 한 번에 20개, endAt 으로 과거로 넘김, 2018년부터 있음).
logs/coinness.jsonl 에 한 줄 = 속보 하나. 같은 id 는 다시 안 쓴다(두 프로세스가 겹치면 중복 줄이 생길 수 있어 읽을 때 id 로 거른다).
  python research/coinness.py              # 최신 → 저장된 마지막 id 까지
  python research/coinness.py --backfill   # 저장된 가장 오래된 것 → 2018-01-01 까지 (처음 한 번, 1~2시간)
주의: bull/bear(독자 호재·악재 투표)는 지금 시점 누적값이라, 과거 속보는 나중에 찍힌 표가 섞여 있다(측정 시 미래 정보).
"""

from __future__ import annotations

import json
import os
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "logs", "coinness.jsonl")
URL = "https://api.coinness.com/feed/v3/breaking-news"
OLDEST = "2018-01-01T00:00:00.000Z"


def page(end_at=None):
    p = {"languageCode": "ko", "limit": 20} | ({"endAt": end_at} if end_at else {})
    for i in range(5):
        try:
            r = requests.get(URL, params=p, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"코인니스 조회 실패 endAt={end_at}")


def row(x):
    return {"id": x["id"], "t": x["publishAt"], "cat": x["categoryId"], "imp": x["isImportant"],
            "title": x["title"], "content": x["content"], "bull": x["bullCount"], "bear": x["bearCount"],
            "codes": x["originCodes"]}


def stored():
    if not os.path.exists(OUT):
        return set(), None
    ids, oldest = set(), None
    with open(OUT, encoding="utf-8") as f:
        for line in f:
            x = json.loads(line); ids.add(x["id"])
            oldest = x["t"] if oldest is None or x["t"] < oldest else oldest
    return ids, oldest


def walk(end_at, stop):
    """end_at 부터 과거로 넘기며 저장. stop(rows) 가 참이면 멈춤."""
    ids, _ = stored()
    n = 0
    with open(OUT, "a", encoding="utf-8") as f:
        while True:
            d = page(end_at)
            if not d:
                break
            new = [row(x) for x in d if x["id"] not in ids]
            for r in new:
                f.write(json.dumps(r, ensure_ascii=False) + "\n"); ids.add(r["id"])
            n += len(new); f.flush()
            if stop(d, new) or d[-1]["publishAt"] == end_at:
                break
            end_at = d[-1]["publishAt"]
            time.sleep(0.3)
    return n


if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    if "--backfill" in sys.argv:
        _, oldest = stored()
        n = walk(oldest, lambda d, new: d[-1]["publishAt"] < OLDEST)
    else:
        n = walk(None, lambda d, new: len(new) < len(d) or d[-1]["publishAt"] < OLDEST)   # 이미 가진 것에 닿으면 끝
    print(time.strftime("%F %T"), f"새 속보 {n}개")
