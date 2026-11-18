"""시간대별 방향판 — 참고용, 봇 매매에는 쓰지 않는다 (1시간 이하 봉은 측정에서 전부 탈락).

지금 투표단 규칙 중 가격만으로 계산하는 것(펀딩·공포탐욕·스테이블 제외)을 1분~1시간 봉에 그대로 적용해 롱/숏 수를 센다.
대시보드 /api/hl/tfboard 가 `./venv/bin/python -m bot.hl_tfboard BTC ETH` 로 부른다(60초 캐시).
"""

from __future__ import annotations

import json
import sys

from . import hl_signals as sig
from .hl_client import HlClient
from .hl_rules import NEEDS_COIN, NEEDS_EXTRA, NEEDS_FUNDING, rules

TFS = ("1m", "5m", "15m", "30m", "1h")


def price_rules() -> list[str]:
    """지금 투표단 규칙 중 가격만으로 계산하는 것."""
    return sorted({n for v in sig.chosen()[0].values() for n in v} - NEEDS_EXTRA - NEEDS_FUNDING - set(NEEDS_COIN))


def vote_share(df) -> tuple[float, float]:
    """확정봉 df 의 마지막 봉에서 (롱 표 비율, 숏 표 비율). 분봉 단타(hl_live.scalp)가 쓴다 — 측정 research/leverage_2026-11/vote_1m.py 와 같은 계산."""
    R = rules(df)
    v = [float(R[n].iloc[-1]) for n in price_rules() if n in R]
    return (sum(x > 0 for x in v) / len(v), sum(x < 0 for x in v) / len(v)) if v else (0.0, 0.0)


def board(coins: list[str]) -> dict:
    names = price_rules()
    c = HlClient()
    out: dict = {"rules": names, "coins": {}}
    for coin in coins:
        rows = {}
        for tf in TFS:
            R = rules(c.candles(coin, tf, 300))
            votes = {n: float(R[n].iloc[-1]) for n in names if n in R}
            v = list(votes.values())
            rows[tf] = {"long": sum(x > 0 for x in v), "short": sum(x < 0 for x in v), "n": len(v),
                        "exposure": round(sum(v) / len(v), 3) if v else 0.0, "votes": votes}
        out["coins"][coin] = rows
    return out


if __name__ == "__main__":
    print(json.dumps(board(sys.argv[1:] or ["BTC", "ETH"]), ensure_ascii=False))
