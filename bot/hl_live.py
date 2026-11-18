"""하이퍼리퀴드 롱·숏 추세 봇 (--mode hl-agent).

4시간봉이 닫힐 때마다 코인별로: 노출 = 투표단을 분석가(추세·파생·거시·심리)로 묶어 평균한 의견(-1~+1), 배수 = 목표변동성 ÷ 실현변동성(상한),
목표 명목 = (자산 ÷ 코인 수) × 노출 × 배수 → 지금 포지션과의 차이만큼 맞춘다(HL_MAKER_WAIT_SEC 면 지정가 먼저, 남은 건 시장가).
HL_LIVE_CONFIRM=yes + HL_PRIVATE_KEY 없으면 가상 체결(중간가, 테이커 수수료, 시간당 펀딩 반영).
정지: state/HL_STOP 이 있으면 전량 청산 후 새 포지션을 잡지 않는다. 킬: 시작 자산 대비 HL_KILL_DRAWDOWN_PCT 하락.
기록: state/hl_live.json, logs/hl_trades.csv, logs/hl_equity.csv
"""

from __future__ import annotations

import collections
import csv
import json
import math
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone, timedelta

from . import hl_ai
from . import hl_signals as sig
from . import hl_tfboard
from . import notifier
from .config import HlConfig
from .hl_client import HlClient, TAKER_FEE
from .logger import get_logger
from .paths import PROJECT_ROOT

logger = get_logger(__name__)
KST = timezone(timedelta(hours=9))
TAG = os.getenv("HL_INSTANCE", "").strip().lower()             # 연습용 쌍둥이(--mode hl-paper → "paper"): 상태·기록 파일을 따로 쓰고 주문은 항상 가상
_N = f"hl_{TAG}" if TAG else "hl"
PAPER_STATE = os.path.join(PROJECT_ROOT, "state", "hl_paper_live.json")     # 실계좌 봇이 상태 문구·대화에 참고로 붙인다 (매매 판단에는 안 씀)
AI_STATE = os.path.join(PROJECT_ROOT, "state", "hl_aipaper_live.json")
AI_MODE = TAG == "aipaper"                                    # AI 연습 계좌(--mode hl-ai): 노출을 규칙 투표 대신 AI 가 15분마다 정한다. 가상 돈
AI_DAILY_USD = float(os.getenv("HL_AIP_DAILY_USD", "3") or 3)  # 하루 API 비용이 이만큼 넘으면 그날은 지난 판단을 그대로 쓴다
STATE = os.path.join(PROJECT_ROOT, "state", f"{_N}_live.json")
STOP_FILE = os.path.join(PROJECT_ROOT, "state", f"{_N.upper()}_STOP")
HALF_FILE = os.path.join(PROJECT_ROOT, "state", f"{_N.upper()}_HALF")   # 만들면: 지금 포지션을 절반으로 줄이고 투표 방향이 바뀌면(새 추세) 전액으로 (split_entry.py 'C', 사용자 요청 2026-11-15)
WAIT_FILE = os.path.join(PROJECT_ROOT, "state", f"{_N.upper()}_WAIT")   # 만들면: 지금 포지션을 전부 정리하고 투표 방향이 바뀌면(새 추세) 전액으로 (fresh_start.py 'B')
INBOX = os.path.join(PROJECT_ROOT, "state", f"{_N}_commands.txt")   # (= notifier.HL_COMMAND_INBOX, 연습용 = notifier.HL_PAPER_INBOX) 텔레그램 /hl_* (50일선 봇이 넘김)·대시보드 버튼. 연습용은 /hlp_* 를 50일선 봇이 /hl_* 로 바꿔 넣는다
CONFIRM_SEC = 60
MANUAL = ("/hl_long", "/hl_short", "/hl_close", "/hl_auto", "/hl_hold", "/hl_at")   # 직접 주문. 손댄 코인은 state.manual 에 적고 /hl_auto 전까지 봇이 안 건드린다
TRADES = os.path.join(PROJECT_ROOT, "logs", f"{_N}_trades.csv")
EQUITY = os.path.join(PROJECT_ROOT, "logs", f"{_N}_equity.csv")
VOTES = os.path.join(PROJECT_ROOT, "logs", f"{_N}_votes.csv")
DESK = os.path.join(PROJECT_ROOT, "logs", f"{_N}_desk.jsonl")      # 점검마다 한 줄: 분석가 의견 → 메인 → 안전장치 → 결과 (대시보드 '에이전트 상황실')
CHAT = os.path.join(PROJECT_ROOT, "logs", f"{_N}_chat.jsonl")      # 봇이 보낸 알림 전부. 대시보드 '주문 대화창'이 읽고, 거기서 보낸 말은 대시보드(/api/command)가 같은 파일에 적는다
BAR_SEC = 900 if AI_MODE else 4 * 3600                        # 점검 주기: 규칙 봇은 4시간봉 마감마다, AI 연습 계좌는 15분마다
TRAIL_PCT, TRAIL_KEEP = 0.05, 0.25   # 이익 보호: 이 거래의 최고 이익(4h 종가)에서 5% 되돌리면 75% 정리, 25% 남김 (profit_guard.py·mtf_exit.py 측정)
REBAL_BAND = 0.30                # 같은 방향이고 목표와 차이가 목표의 30% 미만이면 안 맞춤 (수수료 절감, research more_rules.sim2 로 측정)
LIQ_TIERS = (5, 10, 20)          # 청산가까지 남은 % 가 이 아래로 처음 내려가면 알림
MOVE_PCT, MOVE_SEC = 3.0, 3600   # 1시간에 ±3% 넘게 움직이면 알림 (코인별 1시간 쿨다운)
DAILY_AT = (9, 5)                # 매일 09:05 요약
REBAL_HOURS = (1, 5, 9, 13, 17, 21)
# 분봉 단타(/hl_scalp on, 2026-11-17 사용자 결정 "하이리스크 하이리턴"): 1분봉 투표가 SCALP_TH 넘는 쪽으로 계좌의 SCALP_LEV 배, 반대쪽이 넘으면 뒤집기.
# 측정(research/leverage_2026-11/vote_1m.py)에서는 32개 조합 전부 탈락, 20배는 9~25일 안에 99% 손실 — 결과를 보여 준 뒤 사용자가 실계좌로 골랐다.
SCALP_LEV = float(os.getenv("HL_SCALP_LEV", "20") or 20)
SCALP_TH = float(os.getenv("HL_SCALP_TH", "0.6") or 0.6)
SCALP_STOP_PCT = float(os.getenv("HL_SCALP_STOP_PCT", "2") or 2)   # 평균 진입가에서 이만큼 불리하면 거래소 손절 주문(시장가)이 전부 정리. 20배면 계좌 약 −40%. 청산(약 3.7%)보다 안쪽이어야 한다
SCALP_WAIT = 40                  # 단타 코인의 지정가 대기(초). 다음 1분 판단 전에 끝나게


def action(before: float, after: float) -> str:
    """주문 방향 대신 포지션이 어떻게 바뀌었는지 (숏을 되사서 줄인 걸 '롱'이라고 적지 않게)."""
    if not before:
        return f"{'롱' if after > 0 else '숏'} 진입"
    b, buy = ("롱", "팔기") if before > 0 else ("숏", "되사기")
    if not after:
        return f"{b} 청산(전부 {buy})"
    if (after > 0) != (before > 0):
        return f"{'롱' if after > 0 else '숏'}으로 뒤집기"
    return f"{b} 늘림" if abs(after) > abs(before) else f"{b} 줄임(일부 {buy})"


class HlBot:
    def __init__(self, cfg: HlConfig, client: HlClient):
        self.cfg, self.c = cfg, client
        self.s = self._load()
        self.marks: dict[str, float] = {}
        self.px_hist: dict[str, list[tuple[float, float]]] = {}      # 급변 알림용 (시각, 가격), 메모리에만
        self.move_at: dict[str, float] = {}
        self._chat: collections.deque = collections.deque(maxlen=6)  # 텔레그램 대화 앞선 문답 (메모리에만)

    # ---------------------------------------------------------------- 상태
    def _load(self) -> dict:
        if os.path.exists(STATE):
            with open(STATE) as f:
                return json.load(f)
        start = 0.0 if self.cfg.live else self.cfg.budget_usd          # 실거래는 첫 입금 때 정해진다
        return {"start_usd": start, "cash_usd": start, "pos": {},
                "last_bar": 0, "last_funding_hour": 0, "halt": "", "votes": {}, "lev": {}, "started": _iso(datetime.now(KST))}

    def size(self, coin: str) -> float:
        return self.s["pos"].get(coin, {}).get("size", 0.0)

    def stopped(self) -> bool:
        return os.path.exists(STOP_FILE) or bool(self.s["halt"])

    def cap(self, coin: str) -> float:
        """배수 상한: 설정값과 거래소 상한 중 작은 쪽."""
        return min(self.cfg.max_lev, self.c.max_leverage(coin))

    def scalping(self, coin: str) -> bool:
        return (self.s.get("scalp") or {}).get("coin") == coin

    def save(self) -> None:
        self.s["equity_usd"] = self.equity()
        self.s["marks"] = self.marks
        self.s["live"] = self.cfg.live
        self.s.update(target_vol=self.cfg.target_vol, max_lev=self.cfg.max_lev, min_lev=self.cfg.min_lev, kill_drawdown_pct=self.cfg.kill_drawdown_pct)
        self.s["signals_updated"] = sig.chosen()[1]
        self.s["inputs"] = sig.input_status()
        self.s["stop_file"] = os.path.exists(STOP_FILE)
        self.s["saved"] = _iso(datetime.now(KST))
        tmp = STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.s, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE)

    def equity(self) -> float:
        if self.cfg.live and self.s.get("account_usd") is not None:
            return self.s["account_usd"]
        return self.s["cash_usd"] + sum(p["size"] * (self.marks.get(c, p["entry"]) - p["entry"]) for c, p in self.s["pos"].items())

    def notify(self, text: str) -> None:
        try:                                                          # 대시보드 주문 대화창용 기록 (텔레그램이 실패해도 남는다)
            with open(CHAT, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": time.time(), "who": "bot", "text": text}, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning(f"대화 기록 쓰기 실패: {e}")
        if TAG == "paper":                                            # 연습용 쌍둥이는 텔레그램에서 /hlp_* 로 부른다
            text = text.replace("/hl_", "/hlp_")
        notifier.notify(f"[하이퍼리퀴드{'' if self.cfg.live else ' AI 연습' if AI_MODE else ' 연습'}] {text}")

    # ---------------------------------------------------------------- 체결
    def trade(self, coin: str, delta: float, px: float, why: str, urgent: bool = False, market: bool = False) -> None:
        """delta 코인 수량(부호 = 방향)만큼 체결. 가상이면 px 로 체결.
        실거래: HL_MAKER_WAIT_SEC > 0 이고 급하지 않으면 지정가(post-only, 호가 따라가기)로 먼저 걸고, 그 시간 뒤 남은 수량만 시장가.
        HL_MAKER_ONLY(기본): 시장가를 쓰지 않는다 — 급한 주문(명령·직접 주문)도 지정가로 걸고, 못 채운 것 중 포지션을 '늘리는' 몫은 버리고
        '줄이는' 몫은 state.pending 에 적어 틱마다 지정가로 다시 건다. market=True(킬 드로다운 전량 청산)만 바로 시장가."""
        before = self.size(coin)
        pnl = fee = None
        if self.cfg.live:
            t0 = int(time.time() * 1000) - 5000
            rest, dec = delta, self.c.sz_decimals(coin)
            only = self.cfg.maker_only and not market and self.cfg.maker_wait_sec > 0
            scalp = self.scalping(coin)
            want, failed = before + delta, False
            if self.cfg.maker_wait_sec > 0 and not market and (only or not urgent):
                try:
                    got = self.c.maker_order(coin, delta, SCALP_WAIT if scalp else self.cfg.maker_wait_sec, reduce_only=bool(before) and abs(want) < abs(before) and (want == 0 or (want > 0) == (before > 0)))
                    rest = round(delta - got, dec)
                    why += f" · 지정가 {got / delta:.0%}"
                except Exception as e:                                # 취소 미확인 등 → 시장가로 잇지 않는다(남은 주문이 뒤늦게 체결될 수 있음)
                    logger.error(f"{coin} 지정가 주문 오류, 이번 재조정은 여기서 멈춤: {e}")
                    self.notify(f"⚠ {coin} 지정가 주문에 문제가 있어 이번 주문을 멈췄어요. 다음 {'1분 판단' if scalp else '4시간 점검'}에서 다시 맞춰요.\n{e}")
                    rest, failed = 0.0, True
            if only and rest:                                         # 시장가를 쓰지 않는다(사용자 요청 2026-11-16)
                why += f" · 못 채운 {abs(rest):g}개는 시장가로 잇지 않음"
                rest = 0.0
            fill = {}
            if abs(rest) * px >= 10.0:                                # 거래소 최소 주문 10달러
                fill = self.c.market_order(coin, rest)
            acct, pos = self.c.account()
            self.s["account_usd"], self.s["pos"] = acct, pos
            now = pos.get(coin, {}).get("size", 0.0)
            pend = self.s.setdefault("pending", {})
            goal = want if (want == 0 or (want > 0) == (now > 0)) else 0.0      # 뒤집는 주문은 정리까지만 다시 시도, 새로 여는 몫은 다음 점검에
            if only and not failed and now and abs(goal) < abs(now) and abs(goal - now) * px >= 10.0:
                if coin not in pend:
                    self.notify(f"⏳ {coin} 지정가로 다 못 채웠어요. 남은 정리 수량 {abs(goal - now):g}개는 시장가 없이 30초마다 지정가로 다시 걸어요")
                pend[coin] = goal
            else:
                pend.pop(coin, None)                                  # 다 됐거나, 취소 미확인(다시 걸면 겹칠 수 있다)이면 지운다
            delta = round(now - before, dec)                          # 실제로 바뀐 수량으로 기록
            if not delta:
                return
            px = float(fill.get("avgPx", px))
            try:
                pnl, fee, avg = self.c.fills_pnl(coin, t0)
                px = avg or px
            except Exception as e:                                    # 기록용이라 실패해도 매매는 진행
                logger.warning(f"체결 손익 조회 실패: {e}")
        else:
            p = self.s["pos"].get(coin, {"size": 0.0, "entry": px})
            cur = p["size"]
            fee = abs(delta) * px * TAKER_FEE
            pnl = 0.0
            if cur and (cur > 0) != (delta > 0):                      # 줄이기·뒤집기: 겹치는 만큼 실현
                closed = min(abs(cur), abs(delta))
                pnl = closed * (px - p["entry"]) * (1 if cur > 0 else -1)
            new = cur + delta
            if abs(new) < 1e-12:
                self.s["pos"].pop(coin, None)
            elif cur and (cur > 0) == (new > 0):                      # 같은 방향 늘리기 → 평균 단가
                p["entry"] = (p["entry"] * abs(cur) + px * abs(delta)) / abs(new) if abs(new) > abs(cur) else p["entry"]
                p["size"] = new; self.s["pos"][coin] = p
            else:                                                     # 새로 열기·뒤집기
                self.s["pos"][coin] = {"size": new, "entry": px}
            self.s["cash_usd"] += pnl - fee
        side = action(before, self.size(coin))
        logger.info(f"{coin} {side} {abs(delta):g}개 (${abs(delta) * px:,.0f}) @ {px:,.1f} — {why}")
        if before and (not self.s["pos"].get(coin) or (self.s["pos"][coin]["size"] > 0) != (before > 0)) and not self.scalping(coin):
            self.reselect(f"{coin} 거래 종료")                      # 한 거래가 끝남 → 투표단 재선정
        _csv(TRADES, ["time", "coin", "side", "size", "price", "notional_usd", "equity_usd", "why", "pnl_usd", "fee_usd"],
             [_iso(datetime.now(KST)), coin, side, abs(delta), px, abs(delta) * px, self.equity(), why,
              "" if pnl is None else round(pnl, 4), "" if fee is None else round(fee, 4)])
        p = self.s["pos"].get(coin)
        now = (f"지금 {'롱' if p['size'] > 0 else '숏'} {abs(p['size']):g}개 (${abs(p['size']) * px:,.0f})" if p else "지금 현금")
        real = f" · 실현 {pnl:+,.2f}$" if pnl else ""
        self.notify(f"{coin} {side} {abs(delta):g}개 (${abs(delta) * px:,.0f}) 체결 @ {px:,.1f}{real}\n{why}\n{now} · 평가 ${self.equity():,.0f}")
        self.save()                                                   # 다음 코인의 지정가 대기(최대 5분) 동안에도 대시보드에 바로 보이게

    def reselect(self, reason: str) -> None:
        """research/hl_reselect.py 를 따로 띄운다(몇 분 걸림). 끝나면 state/hl_signals.json 이 바뀌고 다음 재조정부터 반영."""
        if TAG or (getattr(self, "_resel", None) and self._resel.poll() is None):     # 투표단은 실계좌 봇과 주간 작업만 바꾼다
            return
        script = os.path.join(PROJECT_ROOT, "research", "hl_reselect.py")
        if not os.path.exists(script):
            return
        self._resel = subprocess.Popen([sys.executable, script, reason], cwd=PROJECT_ROOT,
                                       stdout=open(os.path.join(PROJECT_ROOT, "logs", "hl_reselect.out"), "a"), stderr=subprocess.STDOUT)
        logger.info(f"투표단 재선정 시작 ({reason})")

    def flatten(self, why: str) -> None:
        for coin, p in list(self.s["pos"].items()):
            self.trade(coin, -p["size"], self.marks.get(coin, p["entry"]), why, urgent=True, market=True)   # 킬 드로다운: 여기만 시장가

    # ---------------------------------------------------------------- 펀딩비 기록
    def add_funding(self, ms: int, coin: str, usd: float) -> None:
        f = self.s.setdefault("funding", {"total": 0.0, "by_coin": {}, "recent": [], "last_ms": 0})
        f["total"] += usd
        f["by_coin"][coin] = f["by_coin"].get(coin, 0.0) + usd
        f["recent"] = [x for x in f["recent"] if x[0] > ms - 86_400_000] + [[ms, usd]]
        f["day"] = sum(x[1] for x in f["recent"])
        f["last_ms"] = max(f["last_ms"], ms)

    def sync_funding(self) -> None:
        """실거래: 거래소 정산 내역에서 마지막 기록 이후 것만 더한다 (매시 정각 정산)."""
        f = self.s.get("funding") or {}
        since = f.get("last_ms") or int(datetime.fromisoformat(self.s.get("started", _iso(datetime.now(KST)))).timestamp() * 1000)
        try:
            for ms, coin, usd in self.c.funding(since + 1):
                self.add_funding(ms, coin, usd)
        except Exception as e:
            logger.warning(f"펀딩비 내역 조회 실패: {e}")

    # ---------------------------------------------------------------- 상태 문구·명령
    def coin_lines(self) -> list[str]:
        out = []
        for coin in self.cfg.coins:
            p, v = self.s["pos"].get(coin), self.s.get("votes", {}).get(coin, {})
            vt = f"롱 {sum(x > 0 for x in v.values())}·숏 {sum(x < 0 for x in v.values())}"
            dk = " · ".join(f"{k} {x:+.2f}" for k, x in (self.s.get("desk", {}).get(coin) or {}).items())
            mode = ""
            if coin in self.s.get("half", {}):
                mode = " · 새 추세 대기 중" if self.s.get("half_k", 0.5) == 0 else " · 절반 모드"
            if coin in self.s.get("manual", []):
                mode = " · 직접 주문한 코인(봇 쉬는 중, /hl_auto 로 돌려줌)"
            if p:
                liq = f", 청산까지 {abs(p['liq'] / self.marks[coin] - 1) * 100:.0f}%" if p.get("liq") and self.marks.get(coin) else ""
                out.append(f"{coin} {'숏' if p['size'] < 0 else '롱'} {abs(p['size']):g}개 (투표 {vt}{liq}){mode}")
            else:
                out.append(f"{coin} 포지션 없음 (투표 {vt}){mode}")
            if dk:
                out.append(f"  분석가 의견: {dk}")
        return out

    def status_text(self) -> str:
        eq, start, f = self.equity(), self.s.get("start_usd") or 0, self.s.get("funding") or {}
        stop = " · ⛔ 정지 중" if self.stopped() else ""
        return "\n".join([f"평가 ${eq:,.2f}" + (f" (시작 대비 {(eq / start - 1) * 100:+.2f}%)" if start else "") + stop,
                          f"펀딩비 24시간 {f.get('day', 0):+.3f}$ · 누적 {f.get('total', 0):+.3f}$", *self.coin_lines(), *self.scalp_line(), *(f"예약 {r['id']}번: {r['text']}" for r in self.s.get("resv", [])), *self.paper_line(),
                          f"다음 점검 {_next_rebal()}시"])

    def scalp_line(self) -> list[str]:
        sc = self.s.get("scalp")
        if not sc:
            return []
        wait = f" · 손절 뒤 {'롱' if sc['block'] > 0 else '숏'} 쉬는 중" if sc.get("block") else ""
        return [f"⚡ 분봉 단타 {sc['coin']} {sc['lev']:g}배{' (끄는 중)' if sc.get('off') else ''}: 1분봉 투표 롱 {sc.get('long', 0):.0%}·숏 {sc.get('short', 0):.0%} (문턱 {sc['th']:.0%})"
                + (f" · 손절선 ${sc['sl']:,.1f}" if sc.get("sl") else "") + wait]

    def paper_line(self) -> list[str]:
        """실계좌 봇 상태 문구에 붙는 연습용 쌍둥이(--mode hl-paper) 한 줄. 같은 투표를 가상 돈으로 대기 없이 따른 결과 — 참고용."""
        out = []
        for label, path in (() if TAG else (("연습용(가상 돈, 대기 없이 투표대로)", PAPER_STATE), ("AI 연습 계좌(가상 돈, AI 가 15분마다 판단)", AI_STATE))):
            try:
                with open(path) as f:
                    p = json.load(f)
                off = "" if time.time() - os.path.getmtime(path) < 300 else " · 지금 꺼져 있음"
            except (OSError, ValueError):
                continue
            eq, start = p.get("equity_usd") or 0, p.get("start_usd") or 0
            pos = " · ".join(f"{c} {'롱' if x['size'] > 0 else '숏'} {abs(x['size']):g}개" for c, x in p.get("pos", {}).items()) or "포지션 없음"
            out.append(f"{label}: 평가 ${eq:,.2f}" + (f" (시작 대비 {(eq / start - 1) * 100:+.2f}%)" if start else "") + f" · {pos}{off}")
        return out

    def commands(self) -> None:
        for cmd in notifier._drain_inbox(INBOX):
            try:
                self.handle(cmd)
            except Exception as e:
                logger.error(f"명령 처리 오류 ({cmd}): {e}")
                self.notify(f"명령 {cmd} 처리 중 오류: {e}")

    def handle(self, cmd: str) -> None:
        ask = {"/hl_half": "지금 포지션을 절반으로 줄이고, 투표 방향이 바뀌면(새 추세) 전액으로 들어가요",
               "/hl_wait": "지금 포지션을 전부 정리하고, 투표 방향이 바뀔 때까지 기다려요",
               "/hl_stop": "지금 포지션을 전부 정리하고 멈춰요 (/hl_go 로 다시 켬)",
               "/hl_go": "멈춤·대기·절반을 풀어요. 다음 4시간 점검부터 투표대로 전액으로 들어가요",
               "/hl_scalp off": "분봉 단타를 꺼요. 단타 포지션을 지정가로 전부 정리하고 손절 주문을 취소해요"}
        sc = self.s.get("scalp")
        if cmd == "/hl_scalp on":
            coin = next((c for c in self.cfg.coins if c not in self.s.get("hold", [])), None)
            if sc or not coin or self.stopped():
                self.notify("분봉 단타가 이미 켜져 있어요. 끄려면 /hl_scalp off" if sc else "정지 중이거나 거래할 코인이 없어서 분봉 단타를 켤 수 없어요. 먼저 /hl_go 로 풀어 주세요")
                return
            lev = min(SCALP_LEV, self.c.max_leverage(coin))
            ask[cmd] = (f"{coin} 분봉 단타를 켜요. 1분마다 1분봉 투표를 보고 {SCALP_TH:.0%} 넘는 쪽으로 계좌의 {lev:g}배(약 ${self.equity() * lev:,.0f})를 지정가로 잡고, "
                        f"반대쪽이 {SCALP_TH:.0%}를 넘으면 뒤집어요. 평균 진입가에서 {SCALP_STOP_PCT:g}% 불리하면 거래소 손절 주문(시장가)이 전부 정리해요(계좌 약 −{SCALP_STOP_PCT * lev:.0f}%). "
                        f"이 코인의 4시간 재조정·이익 보호는 멈춰요.\n과거 측정에서는 이 방식이 {lev:g}배로 9~25일 안에 계좌의 99%를 잃었어요. 끄기: /hl_scalp off")
        elif cmd == "/hl_scalp off" and not sc:
            self.notify("분봉 단타가 켜져 있지 않아요")
            return
        head = cmd.split()[0]
        if cmd == "/hl_status":
            self.notify(self.status_text())
        elif head in MANUAL:
            plan = self.manual_plan(cmd)
            if isinstance(plan, str):
                self.notify(plan)
            else:
                self.s["confirm"] = [cmd, time.time()]
                self.notify(f"{plan[2]}\n{CONFIRM_SEC}초 안에 /hl_yes 를 보내면 실행해요")
        elif cmd == "/hl_list":
            rs = self.s.get("resv", [])
            self.notify("걸어 둔 예약 주문\n" + "\n".join(f"{r['id']}번 · {r['text']}" for r in rs) + "\n취소: /hl_cancel 번호 (전부: /hl_cancel all)" if rs
                        else "걸어 둔 예약 주문이 없어요. 예: /hl_at BTC 63000 long 100 · /hl_at ETH 2% close")
        elif head == "/hl_cancel":                                    # 포지션이 안 바뀌므로 확인 없이 바로
            arg, rs = (cmd.split() + [""])[1], self.s.get("resv", [])
            hit = [r for r in rs if arg == "all" or str(r["id"]) == arg]
            for r in hit:
                rs.remove(r)
            self.notify("예약을 취소했어요\n" + "\n".join(f"{r['id']}번 · {r['text']}" for r in hit) if hit else "그 번호의 예약이 없어요. /hl_list 로 번호를 확인해 주세요 (전부 취소: /hl_cancel all)")
        elif head == "/hl_ask":                                       # 텔레그램에 그냥 쓴 말(50일선 봇이 /hl_ask 를 붙여 넘김)
            q = cmd[len(head):].strip()
            if q:                                                     # LLM 은 별도 스레드: 답을 기다리는 동안에도 틱은 돈다
                threading.Thread(target=self.answer, args=(q, self.chat_ctx()), daemon=True).start()
        elif cmd in ask:
            self.s["confirm"] = [cmd, time.time()]
            self.notify(f"{cmd}: {ask[cmd]}\n{CONFIRM_SEC}초 안에 /hl_yes 를 보내면 실행해요")
        elif cmd == "/hl_yes":
            c, t = self.s.pop("confirm", None) or (None, 0)
            if not c or time.time() - t > CONFIRM_SEC:
                self.notify("확정할 명령이 없어요 (보낸 지 60초가 지났거나 보낸 적이 없어요)")
            elif c == "/hl_go":
                if self.s["halt"]:
                    self.notify(f"킬 드로다운으로 멈춘 상태라 /hl_go 로는 못 켜요 ({self.s['halt']}). state/HL_STOP 삭제 후 재시작이 필요해요")
                    return
                if os.path.exists(STOP_FILE):
                    os.remove(STOP_FILE)
                self.s["half"] = {}
                self.notify(f"▶ 멈춤·대기·절반을 풀었어요. 다음 점검({_next_rebal()}시)부터 투표대로 들어가요")
            elif c == "/hl_stop":
                open(STOP_FILE, "w").close()
                self.notify("⛔ 정지 — 포지션을 전부 정리하고 멈춰요. 다시 켜려면 /hl_go")
                self.rebalance(urgent=True)
            elif c.split()[0] in MANUAL:
                self.manual_run(c)
            elif c == "/hl_scalp on":
                self.scalp_on()
            elif c == "/hl_scalp off":
                self.scalp_off()
            else:
                open(HALF_FILE if c == "/hl_half" else WAIT_FILE, "w").close()      # 이번 틱에서 바로 처리된다
        else:
            self.notify(f"모르는 명령: {cmd}\n/hl_status /hl_half /hl_wait /hl_stop /hl_go /hl_long /hl_short /hl_close /hl_hold /hl_auto /hl_at /hl_list /hl_cancel /hl_scalp /hl_yes")

    # ---------------------------------------------------------------- 직접 주문
    def manual_plan(self, cmd: str):
        """직접 주문 명령 → (코인, 바꿀 수량, 설명 문구). 받을 수 없으면 이유(str). 확인을 물을 때와 실행할 때 두 번 부른다."""
        a = cmd.split()
        act, coin = a[0], a[1].upper() if len(a) > 1 else ""
        if act == "/hl_at":
            r = self.resv_plan(cmd)
            return r if isinstance(r, str) else (r["coin"], 0.0, f"예약 주문: {r['text']}.\n{r['note']}")
        if coin not in self.cfg.coins or len(a) not in ((3, 4) if act in ("/hl_long", "/hl_short") else (2,)):
            return (f"이렇게 보내 주세요 (코인: {'·'.join(self.cfg.coins)}, 금액은 달러)\n"
                    "/hl_long 코인 금액 [배수] · /hl_short 코인 금액 [배수] · /hl_close 코인 · /hl_hold 코인 · /hl_auto 코인\n예: /hl_short BTC 100 · /hl_long BTC 100 20x (100달러 × 20배)")
        if self.scalping(coin):
            return f"{coin} 은 분봉 단타가 맡고 있어요. 직접 주문하려면 먼저 /hl_scalp off 로 단타를 꺼 주세요"
        cur, px, eq = self.size(coin), self.marks[coin], self.equity()
        hold = lambda x: f"{'롱' if x > 0 else '숏'} {abs(x):g}개 (${abs(x) * px:,.0f})" if x else "포지션 없음"
        if act == "/hl_auto":
            if coin not in self.s.get("manual", []):
                return f"{coin} 은 지금도 봇이 맡고 있어요"
            return coin, 0.0, (f"{coin} 을 봇에게 돌려줘요. 다음 점검({_next_rebal()}시)에 봇이 투표와 모드(대기·절반)에 따라 "
                               f"포지션을 다시 맞춰요 — 대기 중이면 전부 정리해요. 지금 {hold(cur)}")
        if self.stopped():
            return "정지 중이라 주문을 받지 않아요. 먼저 /hl_go 로 풀어 주세요"
        tail = (f"이 코인은 /hl_auto 를 보낼 때까지 봇이 건드리지 않아요 (정지·킬 드로다운 때는 같이 정리). "
                f"이익 보호만 걸려요: 4시간 종가가 최고 이익에서 {TRAIL_PCT:.0%} 되돌리면(이익 없이 약 {TRAIL_PCT / (1 + TRAIL_PCT):.1%} 불리하게 가도) {1 - TRAIL_KEEP:.0%} 정리")
        if act == "/hl_hold":                                         # 주문 없이 그 코인만 멈춤(지금 포지션은 그대로)
            if coin in self.s.get("hold", []):
                return f"{coin} 은 이미 멈춰 있어요"
            return coin, 0.0, (f"{coin} 거래를 멈춰요. 주문은 내지 않고 지금 {hold(cur)} 그대로 둬요. "
                               f"이 코인 몫의 돈은 다음 점검({_next_rebal()}시)부터 나머지 코인이 써요(나머지 코인 포지션이 그만큼 커져요). {tail}")
        if act == "/hl_close":
            if not cur:
                return f"{coin} 포지션이 없어요"
            return coin, -cur, f"{coin} {hold(cur)} 을 {'지정가로(최우선 호가에 걸고 따라가요)' if self.cfg.maker_only else '시장가로'} 전부 정리해요. {tail}"
        amt = a[2].rstrip("x배")                                      # 100 = 100달러, 10x·10배 = 계좌 평가금액의 10배
        lev = 0.0                                                     # "/hl_long BTC 100 20x" = 넣는 돈 100달러 × 20배 → 포지션 2,000달러 (사용자 요청 2026-11-17 "배수 선택해서 얼마 롱쳐")
        try:
            usd = float(amt) * (eq if amt != a[2] else 1)
            if len(a) == 4:
                lev = float(a[3].rstrip("x배"))
                usd = float(a[2]) * lev                               # 금액 자리는 달러 숫자여야 한다("10x 20x" 는 여기서 오류)
        except ValueError:
            usd = 0.0
        if not 10 <= usd < 1e9 or eq <= 0 or (len(a) == 4 and not 1 <= lev <= self.c.max_leverage(coin)):   # nan·inf 도 여기서 걸린다. 거래소 최소 주문 10달러
            return (f"금액은 10달러 이상 숫자(달러)나 계좌의 몇 배(10x)로, 배수는 1~{self.c.max_leverage(coin):g}배로 적어 주세요. "
                    "예: /hl_short BTC 100 · /hl_short BTC 10x · /hl_long BTC 100 20x (100달러 × 20배 = 2,000달러)")
        dec = self.c.sz_decimals(coin)
        delta = round((1 if act == "/hl_long" else -1) * usd / px, dec)
        after = round(cur + delta, dec)
        cap = self.cap(coin)
        if abs(after) > abs(cur) and abs(after) * px > eq * cap:      # 줄이는 주문은 막지 않는다
            return f"주문 뒤 포지션이 계좌의 {abs(after) * px / eq:.1f}배가 돼서 받지 않아요 (상한 {cap:g}배)"
        size = f"넣는 돈 ${usd / lev:,.0f} × {lev:g}배 = ${usd:,.0f}" if lev else f"${usd:,.0f}"
        cross = "교차 마진이라 손실이 넣는 돈에서 멈추지 않고 계좌 전체로 버텨요(청산가는 아래). " if lev else ""
        return coin, delta, (f"{coin} {'롱' if delta > 0 else '숏'} {size} (약 {abs(delta):g}개)를 {'지정가로(최우선 호가에 걸고 따라가요. 5분 안에 못 채운 늘리는 몫은 버려요)' if self.cfg.maker_only else '시장가로'} 주문해요. "
                             f"주문 뒤 {hold(after)} — 계좌 ${eq:,.0f} 의 {abs(after) * px / eq:.2f}배. {cross}\n{self.liq_text(coin, after)}\n{tail}")

    def resv_plan(self, cmd: str):
        """/hl_at 코인 가격 close | long 금액 | short 금액 → 예약(dict) 또는 받을 수 없는 이유(str).
        가격 자리의 '2%' = 평균 진입가에서 2% 이익인 가격, '-3%' = 3% 손실인 가격(포지션이 있어야 한다)."""
        a = cmd.split()
        usage = ("이렇게 보내 주세요\n/hl_at 코인 가격 close — 그 가격에 닿으면 그 코인 전부 정리\n/hl_at 코인 가격 long 금액 · /hl_at 코인 가격 short 금액 — 닿으면 그만큼 주문 (금액: 100 = 100달러, 10x = 계좌의 10배, 100 20x = 100달러 × 20배)\n"
                 "가격 자리에 2% 를 쓰면 '평균 진입가에서 2% 이익', -3% 는 '3% 손실'이에요.\n예: /hl_at ETH 2% close · /hl_at BTC 63000 long 100")
        if len(a) < 4 or a[1].upper() not in self.cfg.coins or a[3].lower() not in ("close", "long", "short") or len(a) not in ((4,) if a[3].lower() == "close" else (5, 6)):
            return usage
        coin, act = a[1].upper(), a[3].lower()
        p, mark, how = self.s["pos"].get(coin), self.marks[coin], ""
        try:
            if a[2].endswith("%"):
                if not p:
                    return f"{coin} 포지션이 없어서 %로는 못 정해요. 가격을 달러로 적어 주세요"
                pct, side = float(a[2][:-1]), (1 if p["size"] > 0 else -1)
                px, up = p["entry"] * (1 + side * pct / 100), (side > 0) == (pct > 0)
                how = f"(평균 진입가 ${p['entry']:,.1f} 에서 {abs(pct):g}% {'이익' if pct > 0 else '손실'}) "
            else:
                px = float(a[2])
                up = px > mark
        except ValueError:
            return usage
        if not 0 < px < 1e9 or px == mark:                            # nan 도 여기서 걸린다
            return usage
        if act == "close":
            if not p:
                return f"{coin} 포지션이 없어요"
            order, what = f"/hl_close {coin.lower()}", "전부 정리"
        else:
            order = f"/hl_{act} {coin.lower()} {' '.join(a[4:]).lower()}"      # 금액 자리는 직접 주문과 같다: 100 · 10x(계좌의 10배) · 100 20x(100달러 × 20배)
            chk = self.manual_plan(order)                             # 금액·상한 검사는 직접 주문과 같은 코드로 (지금 가격·평가금액 기준)
            if isinstance(chk, str):
                return chk
            amt = f"${a[4]} × {a[5].rstrip('x배')}배" if len(a) == 6 else f"계좌의 {a[4].rstrip('x배')}배" if a[4][-1] in "x배" else "$" + a[4]
            what = f"{'롱' if act == 'long' else '숏'} {amt} 주문"
        done = mark >= px if up else mark <= px
        return {"coin": coin, "px": round(px, 2), "up": up, "cmd": order,
                "text": f"{coin} 가격이 ${px:,.1f} {how}{'이상으로 오르면' if up else '이하로 내리면'} {what}",
                "note": (f"지금 ${mark:,.1f} 라서 {'이미 그 가격을 지나 있어요. 확인하면 바로 실행돼요' if done else f'그 가격까지 {(px / mark - 1) * 100:+.1f}% 남았어요'}. "
                         "봇이 30초마다 가격을 보고, 닿으면 지정가로 주문해요(그 가격에 딱 맞게 체결되진 않을 수 있어요). "
                         "실행되면 이 코인은 직접 주문한 코인이 돼서 /hl_auto 를 보낼 때까지 봇이 다시 사고팔지 않아요(이익 보호만).")}

    def reservations(self) -> None:
        """예약 주문 점검(틱마다). 가격이 닿으면 직접 주문과 같은 경로(manual_run)로 실행하고 지운다. 거래소에 주문을 걸어 두지 않는다.
        ponytail: 30초 사이에 찍고 돌아온 가격은 놓치고, 닿은 뒤에 지정가를 넣어 그 가격에 정확히 체결되지 않는다. 정확한 체결이 필요해지면 거래소 트리거 주문으로."""
        for r in list(self.s.get("resv", [])):
            px = self.marks.get(r["coin"])
            if r["cmd"].startswith("/hl_close") and not self.s["pos"].get(r["coin"]):
                self.s["resv"].remove(r)
                self.notify(f"예약 {r['id']}번을 지웠어요 — {r['coin']} 포지션이 이미 없어요 ({r['text']})")
            elif px and (px >= r["px"] if r["up"] else px <= r["px"]):
                self.s["resv"].remove(r)
                self.notify(f"⏰ 예약 {r['id']}번 실행 — {r['coin']} 지금 ${px:,.1f} ({r['text']})")
                self.manual_run(r["cmd"])

    def liq_estimate(self, coin: str, size: float) -> float | None:
        """coin 을 size 개(부호 = 방향) 들고 있을 때의 예상 청산가. 교차 마진, 다른 코인 가격은 그대로라고 가정.
        하이퍼리퀴드 공식: 가격 − 방향 × 여유 증거금 ÷ 수량 ÷ (1 − l × 방향), l = 유지 증거금률 = 1 ÷ (최대 배수 × 2)."""
        if not size:
            return None
        side, px = (1 if size > 0 else -1), self.marks[coin]
        l = lambda c: 1 / (2 * self.c.max_leverage(c))
        maint = abs(size) * px * l(coin) + sum(abs(p["size"]) * self.marks.get(c, p["entry"]) * l(c) for c, p in self.s["pos"].items() if c != coin)
        liq = px - side * (self.equity() - maint) / abs(size) / (1 - l(coin) * side)
        return liq if liq > 0 else None                               # 롱인데 0 이하 = 가격이 0 이 돼도 청산 안 됨

    def liq_text(self, coin: str, size: float) -> str:
        liq = self.liq_estimate(coin, size)
        if not size:
            return "포지션이 없어 청산가도 없어요"
        if liq is None:
            return "예상 청산가 없음 (계좌 돈이 포지션보다 많아요)"
        return f"예상 청산가 ${liq:,.1f} (지금 가격에서 {(liq / self.marks[coin] - 1) * 100:+.1f}%, 다른 코인 가격이 그대로일 때)"

    def manual_run(self, cmd: str) -> None:
        plan = self.manual_plan(cmd)                                  # 확인을 기다리는 사이 상태가 바뀌었을 수 있다
        if isinstance(plan, str):
            self.notify(plan)
            return
        coin, delta, _ = plan
        if cmd.startswith("/hl_at"):
            r = self.resv_plan(cmd)
            if isinstance(r, str):
                self.notify(r)
                return
            r.pop("note")
            self.s["resv_n"] = r["id"] = self.s.get("resv_n", 0) + 1
            self.s.setdefault("resv", []).append(r)
            self.notify(f"⏰ 예약 {r['id']}번을 걸었어요 — {r['text']}\n목록 /hl_list · 취소 /hl_cancel {r['id']}")
            return
        man = self.s.setdefault("manual", [])
        if cmd.startswith("/hl_auto"):
            man.remove(coin)
            if coin in self.s.get("hold", []):
                self.s["hold"].remove(coin)
            self.s.get("guard", {}).pop(coin, None)                   # 이익 보호는 봇이 넘겨받는 시점부터 다시 센다
            self.notify(f"▶ {coin} 을 봇에게 돌려줬어요. 다음 점검({_next_rebal()}시)부터 봇이 맡아요")
            return
        if cmd.startswith("/hl_hold"):                                # 이익 보호 기록은 그대로 이어 간다(같은 포지션)
            if coin not in man:
                man.append(coin)
            self.s.setdefault("hold", []).append(coin)
            self.notify(f"⏸ {coin} 거래를 멈췄어요. 이제 봇이 이 코인을 사고팔지 않고(이익 보호·정지·킬 드로다운 때만 정리), 계좌 돈은 나머지 코인에 다 써요. 되돌리려면 /hl_auto {coin}")
            return
        was, before = coin in man, self.size(coin)
        if not was:
            man.append(coin)                                          # 주문보다 먼저: 중간에 오류가 나도 봇이 이 포지션을 정리하지 않게
        if self.cfg.live and abs(before + delta) > abs(before):       # 거래소 배수 설정이 낮으면 큰 주문이 거절된다. 교차 마진이라 청산가는 이 설정과 무관
            self.c.set_leverage(coin, int(self.cap(coin)))
        self.trade(coin, delta, self.marks[coin], f"직접 주문 {cmd}", urgent=True)
        if self.size(coin) == before:
            if not was:
                man.remove(coin)
            self.notify(f"{coin} 직접 주문이 체결되지 않았어요. 포지션은 그대로예요")
        else:
            self.s.get("guard", {}).pop(coin, None)                   # 이익 보호는 바뀐 포지션(평균 진입가) 기준으로 다시 센다

    # ---------------------------------------------------------------- 분봉 단타
    def scalp_on(self) -> None:
        coin = next(c for c in self.cfg.coins if c not in self.s.get("hold", []))
        if coin not in self.s.setdefault("manual", []):
            self.s["manual"].append(coin)                             # 4시간 재조정·절반·대기가 이 코인을 건드리지 않게 (정지·킬은 같이 정리)
        self.s.get("guard", {}).pop(coin, None)
        cur = self.size(coin)
        self.s["scalp"] = {"coin": coin, "lev": min(SCALP_LEV, self.c.max_leverage(coin)), "th": SCALP_TH, "stop": SCALP_STOP_PCT, "since": _iso(datetime.now(KST)),
                           "bar": 0, "dir": 0, "pos_dir": (cur > 0) - (cur < 0), "block": 0}
        self.notify(f"⚡ {coin} 분봉 단타를 켰어요. 다음 1분봉 마감부터 투표가 {SCALP_TH:.0%} 넘는 쪽으로 들어가요. 끄기: /hl_scalp off")

    def scalp_off(self) -> None:
        sc = self.s["scalp"]
        sc["off"] = True                                              # 포지션이 다 정리되고 손절 주문을 취소한 뒤에 scalp() 가 상태를 지운다
        coin = sc["coin"]
        cur = self.size(coin)
        if cur:
            self.trade(coin, -cur, self.marks[coin], "분봉 단타 끄기", urgent=True)

    def scalp(self, now: int) -> None:
        """분봉 투표 단타(방식 B): 1분봉이 닫힐 때마다 가격 규칙들의 롱·숏 표 비율을 보고, 문턱을 넘은 쪽으로 계좌의 lev 배를 잡는다.
        반대쪽이 문턱을 넘을 때까지 들고 가고, 넘으면 뒤집는다. 같은 방향이면 다시 맞추지 않는다(목표보다 30% 넘게 모자랄 때만 채움).
        손절은 거래소에 걸어 둔 시장가 트리거 주문(sync_stop). 손절·청산으로 포지션이 사라지면 투표가 반대쪽으로 넘어갈 때까지 그 방향은 쉰다."""
        sc = self.s["scalp"]
        coin = sc["coin"]
        px, dec = self.marks[coin], self.c.sz_decimals(coin)
        cur = self.size(coin)
        held = (cur > 0) - (cur < 0)
        if sc.get("off"):
            if self.cfg.live:
                self.sync_stop(coin)                                  # 포지션이 없으면 남은 손절 주문을 취소한다
            if not cur:
                del self.s["scalp"]
                self.notify(f"⏹ 분봉 단타를 껐어요. {coin} 은 직접 주문한 코인으로 남아 있어요 — 봇의 4시간 매매로 돌리려면 /hl_auto {coin}")
            return
        if self.cfg.live and not getattr(self, "_scalp_lev", False):  # 거래소 배수 설정이 낮으면 큰 주문이 거절된다(교차 마진이라 청산가와 무관). 프로세스당 한 번
            self.c.set_leverage(coin, int(self.c.max_leverage(coin)))
            self._scalp_lev = True
        stopped = self.stopped()
        if not stopped and now // 60 > sc["bar"] and now % 60 >= 2:    # 1분봉 마감 2초 뒤, 봉마다 한 번
            sc["bar"] = now // 60
            L, S = hl_tfboard.vote_share(self.c.candles(coin, "1m", 300))
            sc["long"], sc["short"] = round(L, 3), round(S, 3)
            d = 1 if L >= sc["th"] else -1 if S >= sc["th"] else sc["dir"]
            if sc["pos_dir"] and not held and sc["pos_dir"] == sc["dir"]:      # 들고 있어야 할 포지션이 우리 주문 없이 사라졌다 = 손절(또는 강제청산·직접 정리)
                sc["block"] = sc["pos_dir"]
                self.notify(f"🛑 {coin} 분봉 단타 포지션이 정리됐어요(손절 주문 체결로 보여요). 투표가 반대쪽으로 넘어갈 때까지 "
                            f"{'롱' if sc['block'] > 0 else '숏'}은 다시 잡지 않아요. 평가 ${self.equity():,.2f}")
            sc["pos_dir"], sc["dir"] = held, d
            if sc["block"] == -d:
                sc["block"] = 0
            if d and d != sc["block"]:
                target = round(d * sc["lev"] * self.equity() / px, dec)
                delta = round(target - cur, dec)
                if (held != d or abs(cur) < (1 - REBAL_BAND) * abs(target)) and abs(delta) * px >= 10.0:
                    self.s.get("pending", {}).pop(coin, None)         # 새 목표를 정하니 지난 '남은 정리' 재시도는 버린다
                    self.trade(coin, delta, px, f"분봉 단타: 1분봉 투표 롱 {L:.0%}·숏 {S:.0%} → {'롱' if d > 0 else '숏'} {sc['lev']:g}배")
                    cur = self.size(coin)
                    sc["pos_dir"] = (cur > 0) - (cur < 0)
        if self.cfg.live:
            self.sync_stop(coin)

    def sync_stop(self, coin: str) -> None:
        """거래소 손절 주문을 지금 포지션에 맞춘다: 수량 = 포지션 전부, 발동 가격 = 평균 진입가에서 stop% 불리한 곳.
        매 틱 거래소에 걸린 주문을 직접 읽어 견준다(상태 파일을 믿지 않는다). 포지션이 없으면 남은 주문을 취소한다."""
        sc, p, dec = self.s["scalp"], self.s["pos"].get(coin), self.c.sz_decimals(coin)
        want = None
        if p and p.get("size"):
            side = 1 if p["size"] > 0 else -1
            want = (round(-p["size"], dec), self.c.px_round(coin, p["entry"] * (1 - side * sc["stop"] / 100)))
        have = self.c.stops(coin)
        if want and len(have) == 1 and abs(have[0]["size"] - want[0]) < 10 ** -dec / 2 and abs(have[0]["px"] - want[1]) < 1e-9 * want[1]:
            sc["sl"] = want[1]
            return
        for o in have:
            self.c.cancel(coin, o["oid"])
        sc["sl"] = None
        if want:
            self.c.stop_order(coin, *want)
            sc["sl"] = want[1]
            logger.info(f"{coin} 손절 주문 {want[0]:+g}개 @ {want[1]:,.1f} (진입가 {p['entry']:,.1f}에서 {sc['stop']:g}%)")

    # ---------------------------------------------------------------- 텔레그램 대화
    def chat_ctx(self) -> dict:
        """대화용 '지금 판'. 메인 스레드에서 만들어 넘긴다(답하는 스레드가 상태를 직접 읽지 않게)."""
        def tail(path, n):
            try:
                with open(path, encoding="utf-8") as f:
                    return f.readlines()[-n:]
            except OSError:
                return []
        def js(text):
            try:
                return json.loads(text)
            except ValueError:
                return None
        return {"상태": self.status_text(), "지금 가격": dict(self.marks), "배수": dict(self.s.get("lev", {})),
                "설정": {"목표 변동성": self.cfg.target_vol, "배수 하한": self.cfg.min_lev, "배수 상한": self.cfg.max_lev, "실거래": self.cfg.live},
                "포지션": {c: {"방향": "롱" if p["size"] > 0 else "숏", "수량": abs(p["size"]), "평균 진입가": p["entry"], "지금 손익(달러)": round(p["size"] * (self.marks.get(c, p["entry"]) - p["entry"]), 2),
                               "청산가": p.get("liq")} for c, p in self.s["pos"].items()},
                "직접 주문한 코인(봇이 안 건드림)": list(self.s.get("manual", [])), "거래를 멈춘 코인(/hl_hold)": list(self.s.get("hold", [])), "분봉 단타(/hl_scalp)": self.scalp_line() or "꺼짐", "걸어 둔 예약 주문": [f"{r['id']}번 · {r['text']}" for r in self.s.get("resv", [])],
                "대기·절반 모드": {"코인별 기억한 방향": dict(self.s.get("half", {})), "남기는 비율": self.s.get("half_k")},
                "이익 보호": json.loads(json.dumps(self.s.get("guard", {}))), "규칙 표(+1 롱, -1 숏)": json.loads(json.dumps(self.s.get("votes", {}))),
                "마지막 점검": js("".join(tail(DESK, 1)) or "null"), "최근 체결": [x.strip() for x in tail(TRADES, 6)],
                "예상 청산가 표(이 코인만 계좌 평가금액의 N배로 새로 잡을 때)": {
                    c: {f"{n}배": {k: (round(v, 1) if v else None) for k, v in (("롱", self.liq_estimate(c, n * self.equity() / self.marks[c])),
                                                                              ("숏", self.liq_estimate(c, -n * self.equity() / self.marks[c])))}
                        for n in (1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 40) if n <= self.c.max_leverage(c)}
                    for c in self.cfg.coins if self.marks.get(c) and self.equity() > 0},
                "AI 그림자 의견": js("".join(tail(hl_ai.LATEST, 50)) or "null"), "고래(달러)": hl_ai.whales_now()}

    def answer(self, q: str, ctx: dict) -> None:
        try:                                                          # 차트 지표는 여기(답하는 스레드)서 받는다: 봉 조회 8번이 틱을 막지 않게
            ctx["차트 지표"] = {c: {ko: hl_ai._ind(self.c.candles(c, iv, 300)) for ko, iv in (("15분봉", "15m"), ("1시간봉", "1h"), ("4시간봉", "4h"), ("일봉", "1d"))}
                            for c in self.cfg.coins}
        except Exception as e:
            logger.warning(f"대화용 차트 지표 조회 실패: {e}")
        try:
            reply, cmd, meta = hl_ai.chat(q, ctx, list(self._chat))
        except Exception as e:
            logger.warning(f"대화 답변 실패: {e}")
            self.notify(f"지금은 답을 받지 못했어요. 잠시 뒤 다시 물어봐 주세요.\n({str(e)[:150]})")
            return
        self._chat.append({"질문": q, "답": reply})
        self.notify("💬 " + reply)
        if cmd:                                                       # 실행하지 않는다: 명령함에 넣어 다음 틱에 보통 명령처럼 처리 → /hl_yes 확인을 다시 받는다
            notifier.send_command(cmd, path=INBOX)
        logger.info(f"대화: {q[:80]} → 명령 {cmd or '없음'} (${meta.get('cost_usd') or 0:.4f})")

    # ---------------------------------------------------------------- 알림 (매매는 안 바뀜)
    def alerts(self, now: float) -> None:
        nxt = _next_rebal()
        tiers = self.s.setdefault("liq_tier", {})
        for coin, p in self.s["pos"].items():
            mark, liq = self.marks.get(coin), p.get("liq")
            if not mark or not liq:
                continue
            dist = abs(liq / mark - 1) * 100
            hit = next((t for t in LIQ_TIERS if dist < t), None)
            last = tiers.get(coin)
            if hit and (last is None or hit < last):
                tiers[coin] = hit
                self.notify(f"⚠ {coin} 청산가까지 {dist:.1f}% 남았어요 ({hit}% 선 안쪽). 지금 ${mark:,.1f}, 청산가 ${liq:,.1f}\n"
                            f"{'숏' if p['size'] < 0 else '롱'} {abs(p['size']):g}개 · 다음 재조정 {nxt}시")
            elif last and dist > last + 5:                              # 다시 멀어지면 단계 초기화 → 다음에 또 알림
                tiers[coin] = hit
        for coin, mark in self.marks.items():
            h = self.px_hist.setdefault(coin, [])
            h.append((now, mark))
            while len(h) > 1 and h[1][0] <= now - MOVE_SEC:            # 60분 전 값 하나만 남기고 버림
                h.pop(0)
            if now - h[0][0] < MOVE_SEC - 60 or now - self.move_at.get(coin, 0) < MOVE_SEC:
                continue
            chg = (mark / h[0][1] - 1) * 100
            if abs(chg) >= MOVE_PCT:
                self.move_at[coin] = now
                p = self.s["pos"].get(coin)
                mine = ""
                if p:
                    good = (chg > 0) == (p["size"] > 0)
                    liq = f", 청산까지 {abs(p['liq'] / mark - 1) * 100:.0f}%" if p.get("liq") else ""
                    mine = f"\n내 {'숏' if p['size'] < 0 else '롱'}에는 {'유리' if good else '불리'}{liq}"
                self.notify(f"{'📈' if chg > 0 else '📉'} {coin} 1시간 {chg:+.1f}% (${h[0][1]:,.1f} → ${mark:,.1f}){mine}\n다음 재조정 {nxt}시")
        kst = datetime.now(KST); today = kst.strftime("%Y-%m-%d")
        if (kst.hour, kst.minute) >= DAILY_AT and self.s.get("daily_sent") != today:
            eq, prev, start = self.equity(), self.s.get("daily_eq"), self.s.get("start_usd") or 0
            f = self.s.get("funding") or {}
            lines = [f"☀ 하이퍼리퀴드 아침 요약 {today}",
                     f"평가 ${eq:,.2f}" + (f" (어제 대비 {eq - prev:+,.2f}$)" if prev else "") + (f" · 시작 대비 {(eq / start - 1) * 100:+.2f}%" if start else ""),
                     f"펀딩비 24시간 {f.get('day', 0):+.3f}$ · 누적 {f.get('total', 0):+.3f}$"]
            self.notify("\n".join(lines + self.coin_lines()))
            self.s["daily_sent"], self.s["daily_eq"] = today, eq

    # ---------------------------------------------------------------- 이익 보호 (4h 종가 판정)
    def guard(self, coin: str, e: float, close: float, px: float, h, manual: bool = False) -> float:
        """노출에 곱할 비율(1 또는 TRAIL_KEEP). 투표 방향이 바뀌면 새 거래로 보고 다시 1부터.
        manual = 직접 주문 포지션: e 는 포지션 수량(부호만 쓴다), 줄이는 주문은 부른 쪽(_rebalance_coins)이 낸다."""
        side = (e > 0) - (e < 0)
        gs = self.s.setdefault("guard", {})
        g = gs.get(coin)
        if g is None or g["side"] != side:
            p = self.s["pos"].get(coin)
            if g is None and side and p and (p["size"] > 0) == (side > 0):     # 이미 들고 있는 거래(처음 켤 때): 진입가·진입 뒤 종가로 시작
                start = _trade_start(coin)
                closes = h["close"][h.index >= start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)] if start else h["close"].tail(1)
                best = max([0.0] + [side * (c / p["entry"] - 1) for c in closes])
                g = {"side": side, "entry": p["entry"], "best": best, "cut": 1.0}
            else:
                gs[coin] = {"side": side, "entry": px, "best": 0.0, "cut": 1.0, "line": None}
                return 1.0                                                     # 새 거래는 다음 마감부터 판정
        fav = side * (close / g["entry"] - 1)
        g["best"] = max(g["best"], fav)
        if side and g["cut"] > TRAIL_KEEP and (1 + g["best"]) / (1 + fav) - 1 >= TRAIL_PCT:
            g["cut"] = TRAIL_KEEP
            self.notify(f"🛡 {coin} 이익 보호 — 최고 이익에서 {TRAIL_PCT:.0%} 되돌림 (종가 ${close:,.1f}). "
                        f"{'숏' if side < 0 else '롱'}을 {1 - TRAIL_KEEP:.0%} 정리하고 {TRAIL_KEEP:.0%}만 남겨요. "
                        + ("남은 건 직접 정리(/hl_close)하거나 /hl_auto 로 봇에게 넘길 때까지 그대로 둬요" if manual else "투표 방향이 바뀔 때까지 다시 늘리지 않아요"))
        f = (1 + g["best"]) / (1 + TRAIL_PCT) - 1                             # 이 종가 이상(숏)·이하(롱)로 마감하면 발동
        g["line"] = round(g["entry"] * (1 + side * f), 2) if side and g["cut"] > TRAIL_KEEP else None
        gs[coin] = g
        return g["cut"]

    # ---------------------------------------------------------------- 4시간마다
    def rebalance(self, urgent: bool = False) -> None:
        """urgent = 사용자 명령(절반·대기·정지)으로 부른 재조정. HL_MAKER_ONLY=0 일 때만 지정가를 거치지 않고 바로 시장가(기본은 지정가로만)."""
        urgent = urgent or self.stopped()
        share = self.equity() / max(1, len(self.cfg.coins) - len(self.s.get("hold", [])))     # 멈춘 코인(/hl_hold) 몫의 돈은 나머지 코인이 쓴다
        boards, rule, recs = [], {}, {}
        whales = hl_ai.whales_now() if self.cfg.ai_shadow and not urgent else {}
        ai = self.ai_decide() if AI_MODE and not urgent else self.s.get("ai", {}).get("coins", {}) if AI_MODE else None
        try:
            self._rebalance_coins(share, urgent, boards, rule, recs, whales, ai)
        finally:                                                      # 중간에 오류가 나도 본 데까지는 남긴다
            if recs:
                with open(DESK, "a") as f:
                    f.write(json.dumps({"time": _iso(datetime.now(KST)), "urgent": urgent, "coins": recs}, ensure_ascii=False) + "\n")
        if boards:
            hl_ai.shadow_async(boards, {b["coin"]: self.marks[b["coin"]] for b in boards}, rule)

    def ai_decide(self) -> dict:
        """AI 연습 계좌: 코인별 판(15분~일봉 지표·규칙 표·고래·포지션·지난 판단)을 주고 {코인: {exposure, reason}} 를 받는다.
        실패하거나 하루 예산(HL_AIP_DAILY_USD)을 넘으면 지난 판단을 그대로 돌려준다(없으면 빈 dict → 그 코인은 그대로 둔다)."""
        last = (self.s.get("ai") or {}).get("coins", {})
        cost = self.s.setdefault("ai_cost", {"day": "", "usd": 0.0, "total": 0.0})
        today = datetime.now(KST).strftime("%Y-%m-%d")
        if cost["day"] != today:
            cost.update(day=today, usd=0.0)
        if cost["usd"] >= AI_DAILY_USD:
            logger.warning(f"AI 판단 하루 예산 ${AI_DAILY_USD:g} 초과 → 오늘은 지난 판단 유지")
            return last
        try:
            whales, boards, eq = hl_ai.whales_now(), [], self.equity()
            for coin in self.cfg.coins:
                d, h = self.c.candles(coin, "1d", 400), self.c.candles(coin, "4h", 500)
                e, votes = sig.vote(d, h, coin)
                lev = sig.leverage(h, self.cfg.target_vol, self.cap(coin), self.cfg.min_lev)
                p = self.s["pos"].get(coin)
                b = hl_ai.board(coin, d, h, votes, sig.desk(votes), e, lev, p, "AI 운용", whales.get(coin))
                b["지표"] = {"15분봉": hl_ai._ind(self.c.candles(coin, "15m", 300)), "1시간봉": hl_ai._ind(self.c.candles(coin, "1h", 300))} | b["지표"]
                b["지금 노출(계좌 대비 배)"] = round(p["size"] * self.marks[coin] / eq, 2) if p and eq > 0 else 0.0
                b["지난 판단"] = [{"몇 분 전": round((time.time() - x["t"]) / 60), "exposure": x["exposure"], "이유": x["reason"]}
                               for x in reversed(self.s.get("ai_hist", {}).get(coin, [])[-4:])]
                boards.append(b)
            out, meta = hl_ai.ask(boards, prompt="hl_trader.md")
        except Exception as ex:
            usd = (getattr(ex, "meta", None) or {}).get("cost_usd") or 0.0     # 답을 못 읽었어도 호출 비용은 센다
            cost["usd"] += usd; cost["total"] += usd
            logger.warning(f"AI 판단 실패 → 지난 판단 유지: {ex}")
            return last
        usd = meta.get("cost_usd") or 0.0
        cost["usd"] += usd; cost["total"] += usd
        calls = {c: out[c] for c in self.cfg.coins if c in out}
        self.s["ai"] = {"time": _iso(datetime.now(KST)), "model": meta.get("model"), "easy": out.get("easy", ""), "coins": calls}
        for c, x in calls.items():
            hist = self.s.setdefault("ai_hist", {}).setdefault(c, [])
            if not hist or hist[-1]["exposure"] != x["exposure"]:       # 판단이 바뀔 때만 남긴다(같은 말 반복으로 판이 길어지지 않게)
                hist.append({"t": time.time(), "exposure": x["exposure"], "reason": x["reason"]}); del hist[:-8]
        logger.info("AI 판단: " + " · ".join(f"{c} {x['exposure']:+.2f}" for c, x in calls.items()) + f" (${usd:.4f}, 오늘 ${cost['usd']:.2f})")
        return calls

    def _rebalance_coins(self, share: float, urgent: bool, boards: list, rule: dict, recs: dict, whales: dict, ai: dict | None = None) -> None:
        for coin in self.cfg.coins:
            d, h = self.c.candles(coin, "1d", 400), self.c.candles(coin, "4h", 500)
            self.s.get("pending", {}).pop(coin, None)                 # 새 목표를 정하니 지난 '남은 정리' 재시도는 버린다
            e, votes = sig.vote(d, h, coin)
            rule[coin] = e                                            # 대기·절반·정지·이익 보호를 곱하기 전, 규칙 메인의 노출
            lev = sig.leverage(h, self.cfg.target_vol, self.cap(coin), self.cfg.min_lev)
            self.s["votes"][coin], self.s["lev"][coin] = votes, round(lev, 2)
            self.s.setdefault("desk", {})[coin] = {k: round(v, 3) for k, v in sig.desk(votes).items()}
            self.s.setdefault("analyst", {}).update({k: sig.analyst(k) for k in votes})     # 대시보드: 규칙 → 분석가
            px = self.marks[coin]
            base = {"price": px, "desk": self.s["desk"][coin], "rule": round(rule[coin], 3), "lev": round(lev, 2)}   # 점검 기록(hl_desk.jsonl)의 공통 칸
            if AI_MODE:                                               # AI 연습 계좌: 규칙 메인(rule) 대신 AI 가 정한 노출로
                if coin not in (ai or {}):
                    recs[coin] = {**base, "mode": "AI 운용",
                                  "cut": 1.0, "exposure": 0.0, "target": 0.0, "cur": self.size(coin), "result": "매매 없음 (AI 판단을 아직 못 받음)"}
                    continue
                e = max(-1.0, min(1.0, float(ai[coin]["exposure"])))
            if self.cfg.ai_shadow and not urgent:
                try:                                                  # 기록용: 실패해도 매매는 그대로
                    mode = ("새 추세 대기" if self.s.get("half_k", 0.5) == 0 else "절반 모드") if coin in self.s.get("half", {}) else "정상"
                    boards.append(hl_ai.board(coin, d, h, votes, sig.desk(votes), e, lev, self.s["pos"].get(coin), mode, whales.get(coin)))
                except Exception as ex:
                    logger.warning(f"AI 그림자 판 만들기 실패: {ex}")
            half = self.s.setdefault("half", {})
            k = self.s.get("half_k", 0.5)
            if coin in half and (e > 0) - (e < 0) != half[coin]:
                del half[coin]
                self.notify(f"🔁 {coin} 투표 방향이 바뀌었어요(새 추세) → {'대기' if k == 0 else '절반 모드'} 끝, 이제 전액 기준으로 맞춰요")
            if coin in half:
                e *= k
            mode = "새 추세 대기" if coin in half and k == 0 else "절반 모드" if coin in half else "정상"
            if self.stopped():
                e, mode = 0.0, "정지"
            if coin in self.s.get("manual", []) and mode != "정지":    # 직접 주문한 코인: /hl_auto 전까지 봇이 안 건드린다 (정지·킬은 정리)
                cur = self.size(coin)
                recs[coin] = rec = {**base, "mode": "직접 주문",
                                    "cut": 1.0, "exposure": 0.0, "target": cur, "cur": cur, "result": "매매 없음 (직접 주문한 코인 — /hl_auto 로 봇에게 돌려줌)"}
                if self.scalping(coin):      # 분봉 단타가 맡은 코인: 1분마다 scalp() 가 판단하고 거래소 손절 주문이 지킨다
                    rec.update(mode="분봉 단타", result="매매 없음 (분봉 단타가 1분마다 판단)")
                elif cur:                                             # 이익 보호만 건다(사용자 요청 2026-11-16): 발동하면 75% 정리, 남은 25%는 그대로
                    rec["cut"] = self.guard(coin, cur, float(h["close"].iloc[-1]), px, h, manual=True)
                    g = self.s["guard"][coin]
                    if rec["cut"] < 1 and not g.get("trimmed"):       # 한 번만 줄인다. 체결이 안 됐으면 다음 점검에 다시
                        rec["result"] = "이익 보호 주문 중 오류"
                        self.trade(coin, round(-cur * (1 - TRAIL_KEEP), self.c.sz_decimals(coin)), px,
                                   f"직접 주문 포지션 이익 보호 (최고 이익에서 {TRAIL_PCT:.0%} 되돌림)", urgent)
                        after = self.size(coin)
                        g["trimmed"] = after != cur
                        rec["result"] = f"이익 보호 — {action(cur, after)} {abs(after - cur):g}개" if after != cur else "이익 보호 주문이 체결되지 않음 (다음 점검에 다시)"
                continue
            cut = 1.0 if AI_MODE else self.guard(coin, e, float(h["close"].iloc[-1]), px, h)     # AI 연습 계좌는 AI 판단만으로(이익 보호 없음)
            e *= cut
            target = round(share * e * lev / px, self.c.sz_decimals(coin))
            cur = self.size(coin)
            delta = round(target - cur, self.c.sz_decimals(coin))
            agree = sum(1 for v in votes.values() if v > 0), sum(1 for v in votes.values() if v < 0)
            why = f"투표 롱 {agree[0]}·숏 {agree[1]} → 노출 {e:+.2f} × 배수 {lev:.2f} (변동성 {sig.realized_vol(h):.0%})"
            if AI_MODE:
                why = f"AI 판단 {e:+.2f} × 배수 {lev:.2f} (규칙이라면 {rule[coin]:+.2f}) — {str(ai[coin].get('reason', ''))[:200]}"
            logger.info(f"{coin} {why} | 목표 {target:g}개, 지금 {cur:g}개")
            _csv(VOTES, ["time", "coin", "exposure", "lev", "long_n", "short_n", "price"],
                 [_iso(datetime.now(KST)), coin, round(e, 3), round(lev, 2), agree[0], agree[1], px])
            recs[coin] = rec = {**base, "mode": mode,
                                "cut": cut, "exposure": round(e, 3), "target": target, "cur": cur, "result": ""}
            if abs(delta) * px < max(10.0, share * 0.003):          # 10달러·자산의 0.3% 미만은 수수료만 나가니 건너뜀
                rec["result"] = f"매매 없음 ({mode if mode in ('정지', '새 추세 대기') and not cur else '목표와 거의 같음'})"
                continue
            if target and cur and (target > 0) == (cur > 0) and abs(delta) < REBAL_BAND * abs(target):
                logger.info(f"{coin} 목표와 차이 {abs(delta) / abs(target):.0%} < 완충 {REBAL_BAND:.0%} → 그대로 둠")
                rec["result"] = f"매매 없음 (목표와 차이 {abs(delta) / abs(target):.0%} < 완충 {REBAL_BAND:.0%})"
                continue
            if self.cfg.live and lev > 1:                             # 멈춘 코인이 증거금을 같이 쓰면 평소 설정으론 큰 주문이 거절된다 → 상한까지(교차 마진이라 청산가와 무관)
                self.c.set_leverage(coin, int(self.cap(coin)) if self.s.get("hold") else math.ceil(lev))
            rec["result"] = "주문 중 오류"
            self.trade(coin, delta, px, why, urgent)
            after = self.size(coin)
            rec["result"] = f"{action(cur, after)} {abs(after - cur):g}개" if after != cur else "주문했지만 체결 없음"

    # ---------------------------------------------------------------- 매 틱
    def tick(self) -> None:
        ctx = self.c.ctx()
        self.marks = {c: ctx[c]["mid"] for c in self.cfg.coins}
        now = int(time.time())
        hour = now // 3600
        if not self.cfg.live and hour > self.s["last_funding_hour"] and self.s["last_funding_hour"]:
            for coin, p in self.s["pos"].items():                   # 시간당 펀딩: 롱은 내고 숏은 받음 (양수일 때)
                usd = -p["size"] * self.marks[coin] * ctx[coin]["funding"]
                self.s["cash_usd"] += usd
                self.add_funding(now * 1000, coin, usd)
        if hour > self.s["last_funding_hour"]:
            self.s["last_funding_hour"] = hour
            if self.cfg.live:
                self.sync_funding()
        if self.cfg.live:
            self.s["account_usd"], self.s["pos"] = self.c.account()
            self.s["withdrawable_usd"] = self.c.withdrawable
        eq = self.equity()
        if self.cfg.live and not self.s["start_usd"] and eq > 0:
            self.s["start_usd"] = eq                                   # 실거래: 첫 입금 금액이 시작 자산
            self.notify(f"입금 확인 — 시작 자산 ${eq:,.2f}. 다음 4시간 점검부터 거래해요")
        if not self.cfg.live and eq <= 0 and self.s["pos"] and not self.s["halt"]:     # 가상 체결에는 강제청산이 없다 → 돈이 0 이하가 되면 끝
            self.s["halt"] = "가상 계좌 전액 손실 (정지)"
            open(STOP_FILE, "w").close()
            self.flatten(self.s["halt"])
            self.notify("⛔ 가상 계좌 돈이 0 이하가 돼서 전부 정리하고 멈췄어요 (실계좌였다면 그 전에 강제청산)")
        if self.cfg.kill_drawdown_pct > 0 and self.s["start_usd"] > 0 and eq <= self.s["start_usd"] * (1 - self.cfg.kill_drawdown_pct / 100) and not self.s["halt"]:
            self.s["halt"] = f"킬 드로다운 {self.cfg.kill_drawdown_pct:g}% (전량 청산 후 정지)"
            open(STOP_FILE, "w").close()
            self.flatten(self.s["halt"])
            self.notify(f"⛔ 시작 자산 대비 {self.cfg.kill_drawdown_pct:g}% 넘게 줄어 전부 팔고 멈췄어요. 평가 ${eq:,.0f}. 다시 켜려면 state/HL_STOP 삭제 후 재시작")
        if not TAG:                                                   # 연습용은 체결만 알린다 (급변·아침 요약이 두 번 오지 않게)
            self.alerts(now)
        self.commands()
        self.reservations()
        for path, k in ((HALF_FILE, 0.5), (WAIT_FILE, 0.0)):
            if os.path.exists(path):
                os.remove(path)
                sides = {c: (1 if p["size"] > 0 else -1) for c, p in self.s["pos"].items() if p.get("size") and c not in self.s.get("manual", [])}
                self.s["half"] = sides | {c: v for c, v in self.s.get("half", {}).items() if c not in sides}   # 이미 대기 중인 코인은 유지
                self.s["half_k"] = k
                self.notify(f"{'✂️ 절반 모드' if k else '⏸ 새 추세 대기'} — {', '.join(self.s['half']) or '포지션 없음'} 지금 "
                            f"{'절반으로 줄이고' if k else '전부 정리하고'}, 투표 방향이 바뀌면(새 추세) 전액으로 들어가요")
                self.rebalance(urgent=True)
        for coin, goal in list(self.s.get("pending", {}).items()):    # 지정가로 못 끝낸 정리 수량: 틱마다 다시 건다 (시장가 안 씀)
            cur = self.size(coin)
            left = round(goal - cur, self.c.sz_decimals(coin))
            if not cur or abs(goal) >= abs(cur) or abs(left) * self.marks[coin] < 10.0:
                self.s["pending"].pop(coin, None)
            else:
                self.trade(coin, left, self.marks[coin], "남은 정리 수량 지정가 다시 시도")
        if self.s.get("scalp"):
            try:
                self.scalp(now)
            except Exception as e:                                    # 단타 오류가 4시간 점검·저장을 막지 않게. 알림은 10분에 한 번
                logger.error(f"분봉 단타 오류: {e}")
                if now - self.s["scalp"].get("err_at", 0) > 600:
                    self.s["scalp"]["err_at"] = now
                    self.notify(f"⚠ 분봉 단타 처리 중 오류가 났어요. 포지션과 손절 주문을 확인해 주세요.\n{str(e)[:200]}")
        bar = (now - 20) // BAR_SEC                                   # 봉 마감 20초 뒤부터
        if bar > self.s["last_bar"]:
            self.rebalance()
            self.s["last_bar"] = bar
            _csv(EQUITY, ["time", "equity_usd", "cash_usd", "pos"], [_iso(datetime.now(KST)), round(eq, 2), round(self.s["cash_usd"], 2),
                                                                    json.dumps(self.s["pos"])])
        self.save()

    def run(self) -> None:
        if self.cfg.live:
            self.sync_funding()
        self.tick()
        if self.s["last_bar"] == 0:
            self.s["last_bar"] = (int(time.time()) - 20) // BAR_SEC - 1   # 처음 켜면 바로 한 번 맞춘다
            self.tick()
        self.notify(f"시작 — {', '.join(self.cfg.coins)} 롱·숏 추세, 목표변동성 {self.cfg.target_vol:.0%}, 배수 상한 {self.cfg.max_lev:g}배, "
                    f"평가 ${self.equity():,.0f}")
        logger.info("hl-agent 시작")
        try:
            ok = True
            while True:
                for _ in range(15):                                   # 30초 주기. 명령이 오면 다 기다리지 않고 바로 처리한다(대시보드 주문 대화창·텔레그램 응답이 빨라짐)
                    time.sleep(2)
                    sc = self.s.get("scalp")                          # 분봉 단타: 1분봉이 닫히면(2초 뒤) 바로 판단
                    if ok and (os.path.exists(INBOX) or (sc and not sc.get("off") and time.time() // 60 > sc["bar"] and time.time() % 60 >= 2)):   # 직전 틱이 실패했으면 30초를 다 기다린다 (2초마다 헛도는 것 방지)
                        break
                try:
                    self.tick()
                    ok = True
                except Exception as e:
                    ok = False
                    logger.error(f"틱 오류: {e}")
        except KeyboardInterrupt:
            self.save()
            self.notify(f"종료 (포지션 그대로) · 평가 ${self.equity():,.0f}")
            logger.info("hl-agent 종료")


def _trade_start(coin: str) -> datetime | None:
    """체결 기록에서 그 코인의 마지막 '진입'·'뒤집기' 시각."""
    try:
        with open(TRADES, newline="") as f:
            rows = [r for r in csv.DictReader(f) if r["coin"] == coin and (r["side"].endswith("진입") or "뒤집기" in r["side"])]
        return datetime.fromisoformat(rows[-1]["time"]) if rows else None
    except FileNotFoundError:
        return None


def _next_rebal() -> int:
    return next((h for h in REBAL_HOURS if h > datetime.now(KST).hour), REBAL_HOURS[0])


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def _csv(path: str, header: list[str], row: list) -> None:
    if os.path.exists(path):                                   # 칸이 늘었으면 기존 줄을 새 헤더로 한 번 옮겨 쓴다
        with open(path, newline="") as f:
            r = csv.reader(f); old = next(r, None)
            rest = list(r) if old and old != header else None      # 헤더가 같으면 첫 줄만 읽는다
        if rest is not None:
            with open(path, "w", newline="") as f:
                w = csv.writer(f); w.writerow(header)
                w.writerows([dict(zip(old, x)).get(k, "") for k in header] for x in rest)
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)
