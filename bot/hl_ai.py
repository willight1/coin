"""AI 운용역 그림자 — 매매에 쓰지 않는다 (2026-11-15).

재조정이 끝난 직후 별도 스레드에서, 봇이 방금 본 판(분석가 의견·규칙 표·지표·포지션·고래 요약)을 LLM 에 주고
'나라면 노출을 얼마로' 를 받아 logs/hl_ai_shadow.csv 와 state/hl_ai_shadow.json(대시보드용 마지막 값)에 적는다.
실패하면 건너뛴다. 실제 표로 넣으려면: 과거 재생(research/hl_ai_replay.py) 합격 + 그림자 8주 성적이 규칙 메인보다 나음 + 사용자 승인.
같은 판을 과거 재생도 쓰므로 날짜·절대 가격은 넣지 않는다(모델이 과거 시세를 알아보지 못하게).
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone

from . import dt_snapshot
from .paths import PROJECT_ROOT

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
LOG = os.path.join(PROJECT_ROOT, "logs", "hl_ai_shadow.csv")
LATEST = os.path.join(PROJECT_ROOT, "state", "hl_ai_shadow.json")
WHALES = os.path.join(PROJECT_ROOT, "logs", "hl_whales_latest.json")
MODEL = os.getenv("HL_AI_MODEL", "claude-sonnet-5-5").strip() or "claude-sonnet-5-5"
HEADER = ["time", "coin", "price", "rule_exposure", "ai_exposure", "reason", "model", "cost_usd"]


def _ind(df) -> dict:
    if "volume" not in df:                                   # 과거 재생(바이낸스 봉)은 거래대금만 있다
        df = df.assign(volume=df["value"] / df["close"])
    x = dt_snapshot.indicators(df)
    last = float(df["close"].iloc[-1])
    x.pop("close", None)
    for k in ("macd_hist", "macd_hist_prev"):                # 절대 가격 단위 → 종가 대비 %
        if x.get(k) is not None:
            x[k + "_pct"] = round(x.pop(k) / last * 100, 3)
    return x


def board(coin: str, d, h, votes: dict, desk: dict, exposure: float, lev: float, pos: dict | None,
          mode: str, whales: dict | None = None) -> dict:
    """LLM 에 주는 코인 한 개의 판. d·h = 확정 일봉·4시간봉. 봇과 과거 재생이 같은 함수를 쓴다."""
    b = {"coin": coin, "분석가 의견": {k: round(v, 2) for k, v in desk.items()}, "규칙 표": votes,
         "규칙 메인 노출": round(exposure, 2), "배수": round(lev, 1),
         "지표": {"4시간봉": _ind(h), "일봉": _ind(d)}, "모드": mode,
         "포지션": None if not pos else {"방향": "롱" if pos["size"] > 0 else "숏",
                                       "진입가 대비 %": round((float(h["close"].iloc[-1]) / pos["entry"] - 1) * 100, 2)}}
    if whales:
        b["고래(달러)"] = whales
    return b


def ask(boards: list[dict], model: str = MODEL, prompt: str = "hl_main.md") -> tuple[dict, dict]:
    """prompt: hl_main.md = 그림자(기록만), hl_trader.md = AI 연습 계좌(--mode hl-ai, 이 답대로 가상 매매)."""
    from .dt_agent import call_api                       # anthropic 을 여기서만 불러온다
    text, meta = call_api(None, prompt, "", json.dumps(boards, ensure_ascii=False), model)
    try:
        out = json.loads(text)
    except ValueError as e:                              # 답이 중간에 잘림 등. 비용은 이미 나갔으니 부른 쪽이 셀 수 있게 meta 를 붙인다
        err = RuntimeError(f"답을 JSON 으로 읽지 못함 (입력 {meta.get('in')}·출력 {meta.get('out')} 토큰, {len(text)}자): {text[:60]!r}")
        err.meta = meta
        raise err from e
    return {c["coin"]: {"exposure": max(-1.0, min(1.0, float(c["exposure"]))), "reason": str(c["reason"]).replace("\x00", "").strip()[:500]}
            for c in out["calls"]} | {"easy": str(out.get("easy", ""))[:300]}, meta


# 대화에서 AI 가 제안할 수 있는 명령. /hl_yes 는 없다 — 확인은 사람만 한다
CHAT_CMD = re.compile(r"/hl_(status|half|wait|stop|go)|/hl_(long|short) [a-z]{2,10} \d+(\.\d+)?x?( \d+(\.\d+)?x)?|/hl_(close|auto|hold) [a-z]{2,10}|/hl_at [a-z]{2,10} -?\d+(\.\d+)?%? (close|(long|short) \d+(\.\d+)?x?( \d+(\.\d+)?x)?)|/hl_list|/hl_cancel (\d+|all)|/hl_scalp (on|off)")


def chat(question: str, ctx: dict, history: list, model: str = MODEL) -> tuple[str, str, dict]:
    """텔레그램 대화: 지금 판과 질문 → (답, 제안 명령 또는 "", meta). 명령은 여기서 실행하지 않는다."""
    from .dt_agent import call_api
    text, meta = call_api(None, "hl_chat.md", "", json.dumps({"지금 상황": ctx, "앞선 대화": history, "질문": question}, ensure_ascii=False), model)
    out = json.loads(text)
    cmd = " ".join(str(out.get("command") or "").lower().split())
    return str(out["reply"]).replace("\x00", "").strip()[:1500], cmd if CHAT_CMD.fullmatch(cmd) else "", meta


def whales_now() -> dict:
    try:
        with open(WHALES) as f:
            return {c: v["groups"] for c, v in json.load(f)["coins"].items()}
    except (OSError, ValueError, KeyError):
        return {}


def _run(boards: list[dict], prices: dict, rule: dict) -> None:
    try:
        out, meta = ask(boards)
        now = datetime.now(KST).isoformat(timespec="seconds")
        new = not os.path.exists(LOG)
        with open(LOG, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(HEADER)
            for coin in prices:
                if coin in out:
                    w.writerow([now, coin, prices[coin], round(rule[coin], 3), out[coin]["exposure"], out[coin]["reason"],
                                meta.get("model"), round((meta.get("cost_usd") or 0) / len(prices), 5)])
        tmp = LATEST + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"time": now, "model": meta.get("model"), "easy": out.get("easy", ""),
                       "coins": {c: {**out[c], "rule": round(rule[c], 3)} for c in prices if c in out}}, f, ensure_ascii=False)
        os.replace(tmp, LATEST)
        logger.info("AI 그림자: " + " · ".join(f"{c} {out[c]['exposure']:+.2f} (규칙 {rule[c]:+.2f})" for c in prices if c in out))
    except Exception as e:
        logger.warning(f"AI 그림자 기록 실패(매매와 무관): {e}")


def shadow_async(boards: list[dict], prices: dict, rule: dict) -> None:
    threading.Thread(target=_run, args=(boards, prices, rule), daemon=True).start()
