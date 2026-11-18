"""
dt_live.py — BTC 자율 에이전트 루프 (--mode btc-agent, docs/BTC_DAYTRADE.md)
============================================================================
에이전트(LLM)가 플랜(전략·파라미터·비중·손절)을 정하고, 실행기가 봉 마감마다 그 전략을 기계적으로
집행한다. 리스크 가드(dt_guard)는 에이전트가 넘을 수 없다. 10초마다 한 틱.

50일선 봇(--mode trade)과 같은 계좌를 쓴다:
- 에이전트 몫(현금 reserve_krw, BTC btc_volume)을 state/dt_live.json 에 쓰고, 50일선 봇은 그만큼 빼고 매매한다.
- 계좌 BTC 를 넘겨받지 않는다(DT_ADOPT_ACCOUNT_BTC=true 일 때만). 잔고 대조는 '계좌 ≥ 에이전트 기록'으로 본다.
- 텔레그램은 50일선 봇이 읽고 /dt_* 명령을 state/dt_commands.txt 로 넘긴다.
"""

from __future__ import annotations

import csv
import fcntl
import json
import os
import time
import traceback
from datetime import datetime, timedelta

import pandas as pd

from . import dt_snapshot as snap
from . import notifier
from .config import DtConfig
from concurrent.futures import ThreadPoolExecutor

from .dt_agent import LlmRouter, filter_lessons, parse_decision, parse_quick, parse_review
from .dt_executor import DtExecutor, Fill
from .dt_guard import STOP_FILE, BuyContext, check_buy, clamp_stop, kill_hit, weekly_loss_hit
from .logger import get_logger
from .paths import PROJECT_ROOT
from .strategies import (BUY, LIBRARY, SELL, bar_action, ko, ko_params, ko_sub, make_library_strategy,
                         simulate_library, window_stats)

logger = get_logger(__name__)
KST = snap.KST
FEE = 0.0005

# 2-1 장기 근거 (에이전트 입력에 그대로)
EVIDENCE = {
    "rsi_momo_1h": "6년(2020.9~2026.11, 편도 0.1%) +476.1%, MDD -17.1%, PF 1.67, 연 60회. 최근 2년 +12.3%, 최근 1년 -7.9% (같은 기간 보유 -26%). 무작위 진입 대비 100백분위 [업비트 데이터 직접 계산]",
    "rsi_momo_4h": "6년 +471.9%, MDD -18.9%, PF 2.12, 승률 54.3%, 연 27회. 최근 2년 +0.5%, 최근 1년 +6.1% [업비트 데이터 직접 계산]",
    "breakout_vol_1h": "6년(편도 0.05%) +715.3%, MDD -24.4%, PF 1.56. 최근 1년 +6.3% [업비트 데이터 직접 계산]",
    "sma_trend_daily": "이 봇 연구 기록: 연 +62~75%, MDD 56%, OOS(2024~) +166~180%, 2025년 +18%. 큰 하락장에서는 손실을 줄일 뿐 여전히 잃음 [이 봇 검증기]",
    "vb_daily": "2022~2026.11 +50.4%(편도 0.05%) / 슬리피지 0.05% 추가 시 +3.6%. 최근 1년 +3.4% / -3.3%. 이 봇 연구에서 133개 코인 중 이익 코인 3% [약함]",
    "vwap_revert": "이 봇 연구: 비용 전 거래당 +0.1~0.5%, 지정가 기준 BTC 거래당 -0.07%. 가상거래 10/3~10/19 BTC 6회 3승 +0.22% (같은 기간 BTC 약 -5%) [본전 근처, 표본 작음]",
    "discretionary": "근거 없음. 예산 일부만",
    "hold": "단순 보유 6년 +732.7%, MDD -74.9% [기준선]",
}
EXCLUDED = ("RSI 30 아래 역추세 매수(1h -34.4%), 거래량 급증 음봉 매수(-58.8%), 1~30분봉 RSI70 신호(5분 -82.4%~30분 -22.8%), "
            "시간대 매매(06~08시 보유 -54.2%), 데이트레이딩 후보 A~G, 공개 단타 셋업 H1~H8. discretionary 로도 금지")
SCORE_UNITS = (15, 60, 240, 1440)
QUICK_WAKES = ("예약 점검", "가격 ", "1분 거래대금")   # 이것만이면 빠른 점검부터
SCORE_DAYS = 90


def _iso(t: datetime | None) -> str | None:
    return t.isoformat(timespec="seconds") if t else None


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def credit_period(t: datetime, day: int) -> str:
    """월간 크레딧 기간 이름. 충전일(day) 전이면 지난달 기간. day=0 이면 "" (안 지움)."""
    if day <= 0:
        return ""
    y, m = (t.year, t.month) if t.day >= day else ((t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12))
    return f"{y}-{m:02d}"


def trading_day(t: datetime) -> str:
    return snap.session_start(t).strftime("%Y-%m-%d")


class DtBot:
    def __init__(self, cfg: DtConfig, client, executor: DtExecutor | None = None, llm=None,
                 now=lambda: datetime.now(KST), notify=notifier.notify,
                 state_dir: str | None = None, log_dir: str | None = None, async_llm: bool = True):
        self.cfg, self.c, self.now, self._send = cfg, client, now, notify
        # llm(prompt_name, lessons, user, model) -> (text, meta). 기본: 구독 claude -p → API 예비
        # API 예산(DT_API_TOTAL_BUDGET_USD)은 모든 코인 봇이 같이 쓴다
        self.llm = llm or LlmRouter(cfg, notify=self.notify, now=now, spent=self._api_spent_all)
        self.async_llm = async_llm
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.job: dict | None = None
        self.x = executor or DtExecutor(client, cfg.live)
        self.state_dir = state_dir or os.path.join(PROJECT_ROOT, "state")
        self.log_dir = log_dir or os.path.join(PROJECT_ROOT, "logs")
        self.state_path = os.path.join(self.state_dir, "dt_live.json")
        self.lessons_path = os.path.join(self.state_dir, "dt_lessons.md")
        self.stop_path = STOP_FILE if state_dir is None else os.path.join(self.state_dir, "DT_STOP")
        self.inbox = notifier.DT_COMMAND_INBOX if state_dir is None else os.path.join(self.state_dir, "dt_commands.txt")
        # 실제 state/ 아래에서 도는 봇(코인별 폴더 포함)만 공용 정지 파일·다른 코인 API 비용을 본다 (테스트 폴더 제외)
        self.shared = os.path.abspath(self.state_dir).startswith(os.path.join(PROJECT_ROOT, "state"))
        self.hist: dict = {}            # unit -> 확정봉 df (점수판·실행기 공용)
        self.forming_daily = None       # 오늘 진행 중 일봉 (vb_daily 기준가)
        self.vwap: float | None = None
        self.wake: list[str] = []
        self.data = None                # 마지막 collect() 결과
        self.price = 0.0
        self._last_bar: dict = {}
        self._last_tick: datetime | None = None
        self._last_equity_log: datetime | None = None
        self._last_reconcile: datetime | None = None
        self.s = self._load()

    # ================================================================ 상태
    def _default(self) -> dict:
        return {"position": None,
                "plan": {"strategy": "none", "params": {}, "size_pct": 50, "stop_loss_pct": 3.0,
                         "confidence": 0.0, "set_at": None, "reason": "시작 (현금)"},
                "realized_total": 0.0, "pnl_log": [], "day": {"date": "", "buys": 0},
                "weekly_stop": False, "halt": "", "cooldown_until": None,
                "llm": {"date": "", "quick": 0, "deep": 0, "calls": 0, "routes": {}, "limit_errors": 0,
                        "cap_notified": [], "in": 0, "out": 0, "total_calls": 0, "total_in": 0, "total_out": 0},
                "decisions": [], "trades": [], "rejections": [], "reject_counts": {},
                "pending": None, "last_llm_price": None, "last_llm_at": None, "next_check_at": None,
                "vb_session": "", "scoreboard": {}, "last_review": "", "last_weekly": "",
                "flat_pending_at": None, "started_at": None, "last_signal_at": None, "hold_end_fired": None}

    def _load(self) -> dict:
        s = self._default()
        try:
            with open(self.state_path, encoding="utf-8") as f:
                s.update(json.load(f))
        except (OSError, ValueError):
            pass
        return s

    def save(self) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        p = self.s["position"]
        pend = self.s["pending"] or {}
        locked_krw = pend.get("price", 0) * pend.get("volume", 0) * (1 + FEE) if pend.get("side") == "bid" else 0
        locked_btc = pend.get("volume", 0) if pend.get("side") == "ask" else 0
        # 50일선 봇이 빼고 쓰는 몫. 업비트 balance 는 미체결 주문에 잠긴 금액을 이미 빼고 주므로 그만큼 덜 잡는다
        # 가상 체결(DT_LIVE_CONFIRM 없음)은 실제 돈을 안 쓰므로 50일선 봇 몫을 줄이지 않는다
        self.s["reserve_krw"] = max(0.0, self.cash() - locked_krw) if self.cfg.live else 0.0
        # 50일선 봇은 pool_krw − Σ pool_used 만큼 뺀다 (trader._dt_share)
        self.s["pool_krw"] = self.cfg.budget_krw if self.cfg.live else 0.0
        self.s["pool_used"] = self._own_used() + locked_krw if self.cfg.live else 0.0
        self.s["btc_volume"] = max(0.0, (p["volume"] if p else 0.0) - locked_btc) if self.cfg.live else 0.0
        self.s["market"] = snap.MARKET  # btc_volume 은 이 코인의 수량 (이름은 예전 그대로)
        self.s["equity"] = self.equity()
        self.s["price"] = self.price
        # 대시보드용 (dt_dashboard.py 가 읽는다)
        self.s["pnl_today"], self.s["pnl_7d"], self.s["unrealized"] = self.day_pnl(), self.pnl_7d(), self.unrealized()
        self.s["llm_route_now"] = self.llm.route_now() if hasattr(self.llm, "route_now") else self.cfg.llm_primary
        self.s["api_total_budget_usd"] = self.cfg.api_total_budget_usd
        self.s["llm_primary"] = self.cfg.llm_primary
        self.s["llm_busy_since"] = _iso(self.job["started"]) if self.job else None
        self.s["next_interval_min"] = self.check_interval_min() if self.price else None
        self.s["stop_file"] = self.stopped()
        self.s["max_buys_per_day"] = self.cfg.max_buys_per_day
        self.s["live"] = self.cfg.live
        self.s["budget_krw"] = self.cfg.budget_krw
        self.s["updated_at"] = _iso(self.now())
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.s, f, ensure_ascii=False, indent=1, default=str)
        os.replace(tmp, self.state_path)

    def _append(self, name: str, row) -> None:
        os.makedirs(self.log_dir, exist_ok=True)
        path = os.path.join(self.log_dir, name)
        if name.endswith(".jsonl"):
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            return
        new = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)

    def _api_spent_all(self) -> float:
        total = float(self.s["llm"].get("api_total_usd", 0.0))
        if self.shared:
            for d in notifier.dt_dirs():
                if os.path.abspath(d) != os.path.abspath(self.state_dir):
                    try:
                        with open(os.path.join(d, "dt_live.json"), encoding="utf-8") as f:
                            total += float(json.load(f).get("llm", {}).get("api_total_usd", 0.0))
                    except (OSError, ValueError):
                        pass
        return total

    def stopped(self) -> bool:
        """코인별 DT_STOP 또는 공용 state/DT_STOP (touch 하나로 모든 코인 신규 매수 중지)."""
        return os.path.exists(self.stop_path) or (self.shared and os.path.exists(STOP_FILE))

    def notify(self, text: str) -> None:
        self._send(f"[AI 매매 · {snap.COIN_KO}{'' if self.cfg.live else ' 연습'}] {ko_sub(text)}")

    # ================================================================ 손익
    # 코인별 봇 4개가 DT_BUDGET_KRW 하나를 같이 쓴다: 먼저 사는 코인이 쓰고, 남은 돈을 다른 코인이 쓴다.
    # 손실 한도(하루·7일·킬)도 모든 코인 손익을 합쳐서 본다. 다른 봇 값은 그 봇이 10초마다 저장한 파일에서 읽는다.
    def others(self) -> list[dict]:
        out = []
        if self.shared:
            for d in notifier.dt_dirs():
                if os.path.abspath(d) != os.path.abspath(self.state_dir):
                    try:
                        with open(os.path.join(d, "dt_live.json"), encoding="utf-8") as f:
                            out.append(json.load(f))
                    except (OSError, ValueError):
                        pass
        return out

    def _own_used(self) -> float:
        p = self.s["position"]
        return (p["cost_krw"] if p else 0.0) - self.s["realized_total"]

    def cash(self) -> float:
        return self.cfg.budget_krw - self._own_used() - sum(float(o.get("pool_used") or 0) for o in self.others())

    def _sum_others(self, key: str) -> float:
        return sum(float(o.get(key) or 0) for o in self.others())

    def day_pnl_all(self) -> float:
        return self.day_pnl() + self._sum_others("pnl_today")

    def pnl_7d_all(self) -> float:
        return self.pnl_7d() + self._sum_others("pnl_7d")

    def equity_all(self) -> float:
        return self.equity() + self._sum_others("realized_total") + self._sum_others("unrealized")

    def unrealized(self) -> float:
        p = self.s["position"]
        return p["volume"] * self.price * (1 - FEE) - p["cost_krw"] if p and self.price else 0.0

    def equity(self) -> float:
        return self.cfg.budget_krw + self.s["realized_total"] + self.unrealized()

    def realized_since(self, t: datetime) -> float:
        return sum(v for ts, v in self.s["pnl_log"] if _dt(ts) >= t)

    def day_pnl(self) -> float:
        return self.realized_since(snap.session_start(self.now())) + self.unrealized()

    def pnl_7d(self) -> float:
        return self.realized_since(self.now() - timedelta(days=7)) + self.unrealized()

    # ================================================================ 주문
    def _reject(self, reason: str) -> None:
        logger.info(f"거절: {reason}")
        r = self.s["rejections"]
        r.append({"time": _iso(self.now()), "reason": reason})
        del r[:-10]
        key = reason.split(" (")[0][:40]
        self.s["reject_counts"][key] = self.s["reject_counts"].get(key, 0) + 1

    def halt_reason(self) -> str:
        if self.s["halt"]:
            return self.s["halt"]
        if self.x.errors >= 3:
            return f"주문 오류 연속 {self.x.errors}회"
        return ""

    def try_buy(self, *a, **k) -> bool | str:
        """두 코인 봇이 같은 순간 사면 같은 돈을 두 번 쓸 수 있다 → 잔고 확인·주문·저장을 한 봇씩 차례로."""
        lock = os.path.join(notifier.dt_dirs()[0] if self.shared else self.state_dir, "dt_pool.lock")
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        with open(lock, "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                return self._try_buy(*a, **k)
            finally:
                self.save()                       # 다른 코인 봇이 줄어든 공용 돈을 바로 보게

    def _try_buy(self, strategy: str, params: dict, size_pct: float, stop_pct: float, confidence: float,
                 source: str, reason: str, limit_price: int | None = None) -> bool | str:
        """가드 통과 시 매수. limit_price 가 있으면 post_only 지정가 주문을 걸고 'pending' 반환."""
        book = self._book()
        cd = _dt(self.s["cooldown_until"])
        krw_avail = min(self.cash(), self._krw_balance())
        ok, why, amt = check_buy(self.cfg, BuyContext(
            stop_file=self.stopped(), weekly_stop=self.s["weekly_stop"],
            halt_reason=self.halt_reason(), day_pnl_krw=self.day_pnl_all(), buys_today=self.s["day"]["buys"],
            holding=self.s["position"] is not None or self.s["pending"] is not None,
            cooldown_left_min=max(0.0, (cd - self.now()).total_seconds() / 60) if cd else 0.0,
            confidence=confidence, spread_bp=book.get("spread_bp"), warning=self._warning(),
            discretionary=strategy == "discretionary", size_pct=size_pct, krw_available=krw_avail))
        if not ok:
            self._reject(f"{strategy} 매수: {why}")
            return False
        mid = book.get("mid") or self.price
        if limit_price:
            u = self.x.limit("bid", limit_price, amt / limit_price)
            if not u:
                self._reject(f"{strategy} 지정가 매수: 주문 실패")
                return False
            self.s["pending"] = {"uuid": u, "side": "bid", "price": limit_price, "volume": amt / limit_price,
                                 "placed_at": _iso(self.now()), "strategy": strategy, "params": params,
                                 "stop_pct": stop_pct, "source": source, "reason": reason, "mid": mid}
            self.notify(f"지정가 매수 주문을 걸었습니다: {limit_price:,}원에 {amt:,.0f}원어치 ({ko(strategy)})")
            return "pending"
        f = self.x.buy(amt, mid, book)
        if not f:
            self._reject(f"{strategy} 매수: 주문 실패 (연속 오류 {self.x.errors}회)")
            return False
        self._on_buy(f, strategy, params, stop_pct, source, reason)
        return True

    def _on_buy(self, f: Fill, strategy, params, stop_pct, source, reason) -> None:
        now = self.now()
        no_stop = strategy == "sma_trend_daily"
        self.s["position"] = {"volume": f.volume, "entry_price": f.avg, "cost_krw": f.funds + f.fee,
                              "entry_time": _iso(now), "strategy": strategy, "params": params,
                              "source": source, "stop_pct": None if no_stop else stop_pct,
                              "stop_price": None if no_stop else f.avg * (1 - stop_pct / 100)}
        self.s["day"]["buys"] += 1
        self._trade_log("매수", f, 0.0, strategy, source, reason)
        self.wake.append("체결(매수)")
        self.notify(f"🟢 매수했습니다: {f.funds + f.fee:,.0f}원어치 ({f.volume:.8f} {snap.COIN}, 평균 {f.avg:,.0f}원)\n"
                    f"전략: {ko(strategy)} · 계기: {source} · 호가 대비 체결 차이 {f.slip_bp:+.1f}bp\n"
                    + (f"손절가: {self.s['position']['stop_price']:,.0f}원" if not no_stop
                       else "손절가: 없음 (전체 손실 한도만 적용)")
                    + f"\n이유: {reason[:200]}")

    def sell(self, source: str, reason: str, pct: float = 100) -> bool:
        p = self.s["position"]
        if not p:
            return False
        self.cancel_pending()
        p = self.s["position"]
        if not p:
            return False
        vol = p["volume"] * min(pct, 100) / 100
        vol = min(vol, self._btc_balance())          # 봇 기록과 가용 잔고 중 작은 값
        book = self._book()
        f = self.x.sell(vol, book.get("mid") or self.price, book)
        if not f:
            return False
        self._on_sell(f, source, reason)
        return True

    def _on_sell(self, f: Fill, source: str, reason: str) -> None:
        p = self.s["position"]
        frac = min(1.0, f.volume / p["volume"]) if p["volume"] else 1.0
        cost = p["cost_krw"] * frac
        pnl = f.funds - f.fee - cost
        now = self.now()
        self.s["realized_total"] += pnl
        self.s["pnl_log"].append([_iso(now), pnl])
        self.s["pnl_log"] = [x for x in self.s["pnl_log"] if _dt(x[0]) >= now - timedelta(days=8)]
        self._trade_log("매도", f, pnl, p["strategy"], source, reason)
        left = p["volume"] - f.volume
        if left * self.price < 5_000 or frac >= 0.999:
            self.s["position"] = None
            self.s["cooldown_until"] = _iso(now + timedelta(minutes=self.cfg.cooldown_min))
        else:
            p["volume"], p["cost_krw"] = left, p["cost_krw"] - cost
        self.wake.append("체결(매도)")
        self.notify(f"{'🔵' if pnl >= 0 else '🔴'} 매도했습니다: 평균 {f.avg:,.0f}원, 손익 {pnl:+,.0f}원 "
                    f"({pnl / cost * 100 if cost else 0:+.2f}%)\n계기: {source} · 호가 대비 체결 차이 {f.slip_bp:+.1f}bp"
                    f"\n이유: {reason[:200]}")

    def _trade_log(self, side: str, f: Fill, pnl: float, strategy: str, source: str, reason: str) -> None:
        row = {"time": _iso(self.now()), "side": side, "volume": round(f.volume, 8), "avg_price": round(f.avg),
               "krw": round(f.funds), "fee": round(f.fee, 1), "slippage_bp": round(f.slip_bp, 2),
               "pnl_krw": round(pnl), "strategy": strategy, "source": source, "reason": reason[:300],
               "uuids": " ".join(f.uuids)}
        self._append("dt_trades.csv", row)
        self.s["trades"] = (self.s["trades"] + [row])[-5:]
        if abs(f.slip_bp) > 10:
            self.notify(f"⚠️ {side} 체결가가 호가보다 {abs(f.slip_bp):.1f}bp(0.01%=1bp) 불리했습니다")

    def cancel_pending(self) -> None:
        pend = self.s["pending"]
        if not pend:
            return
        o = self.x.cancel(pend["uuid"])
        self.s["pending"] = None
        self._settle_limit(pend, o)

    def _settle_limit(self, pend: dict, o: dict) -> None:
        """지정가 주문 최종 상태에서 체결분을 포지션에 반영."""
        f = Fill(pend["side"], mid=pend.get("mid", 0))
        f.add(o)
        if f.volume <= 0:
            return
        if pend["side"] == "bid":
            self._on_buy(f, pend["strategy"], pend["params"], pend["stop_pct"], pend["source"], pend["reason"])
        elif self.s["position"]:
            self._on_sell(f, pend.get("source", "VWAP"), pend.get("reason", "VWAP 복귀"))

    # ================================================================ 시세·잔고
    def _book(self) -> dict:
        try:
            return snap.orderbook_info(self.c._request("GET", "/v1/orderbook", params={"markets": snap.MARKET})[0])
        except Exception as e:
            logger.warning(f"호가 조회 실패: {e}")
            return {}

    def _warning(self) -> bool | None:
        return self.data["market"].get("warning") if self.data else None

    def _accounts(self) -> dict:
        return {a["currency"]: a for a in self.c._request("GET", "/v1/accounts", auth=True)}

    def _krw_balance(self) -> float:
        if not self.cfg.live:
            return self.cash()
        return float(self._accounts().get("KRW", {}).get("balance", 0))

    def _btc_balance(self) -> float:
        if not self.cfg.live:
            return self.s["position"]["volume"] if self.s["position"] else 0.0
        return float(self._accounts().get(snap.COIN, {}).get("balance", 0))

    # ================================================================ 점수판
    def load_history(self) -> None:
        for u in SCORE_UNITS:
            n = int(SCORE_DAYS * 1440 / u) + 250
            self.hist[u] = snap.split_closed(snap.fetch_history(self.c, u, n), u, self.now())[0]
        self.update_scoreboard()

    def _merge(self, u, df: pd.DataFrame) -> None:
        old = self.hist.get(u)
        df = df if old is None or old.empty else pd.concat([old, df])
        df = df.drop_duplicates("candle_date_time_kst", keep="last").sort_values("candle_date_time_kst")
        keep = int(SCORE_DAYS * 1440 / u) + 300
        self.hist[u] = df.tail(keep).reset_index(drop=True)

    def update_scoreboard(self, units=SCORE_UNITS) -> None:
        now = pd.Timestamp(self.now().replace(tzinfo=None))
        for sid, cls in LIBRARY.items():
            if cls.UNIT not in units or sid == "discretionary" or cls.UNIT not in self.hist:
                continue
            st = cls()
            df = self.hist[cls.UNIT]
            if len(df) < 60:
                continue
            sim = simulate_library(st, df, cost=0.0005 if sid == "vwap_revert" else 0.001)
            e, x = st.entry_exit(df)
            self.s["scoreboard"][sid] = {
                **{f"d{d}": window_stats(sim, now - pd.Timedelta(days=d)) for d in (7, 30, 90)},
                "entry_signal_now": bool(e.iloc[-1]) if st.KIND == "bar" else None,
                "in_position_state": st.holds(df) if st.KIND == "bar" else None,
                "updated": _iso(self.now())}
        if 60 in self.hist and len(self.hist[60]):
            c = self.hist[60]
            ts = pd.to_datetime(c["candle_date_time_kst"])
            self.s["scoreboard"]["hold"] = {f"d{d}": {"ret": round(
                float(c["close"].iloc[-1] / c["close"][ts >= now - pd.Timedelta(days=d)].iloc[0] - 1) * 100, 2)}
                for d in (7, 30, 90)}

    # ================================================================ 봉 마감
    def _bar_id(self, u: int, t: datetime) -> int:
        return int(t.timestamp() // (u * 60))      # 업비트 봉은 UTC 기준 정렬 (일봉 = KST 09:00)

    def check_bars(self) -> None:
        t = self.now() - timedelta(seconds=3)       # 마감 직후 체결 반영 여유
        for u in (1, 15, 60, 240, 1440):
            b = self._bar_id(u, t)
            if self._last_bar.get(u) is None:
                self._last_bar[u] = b
            elif b != self._last_bar[u]:
                self._last_bar[u] = b
                try:
                    self.on_bar_close(u)
                except Exception as e:
                    logger.error(f"{u}분봉 마감 처리 실패: {e}\n{traceback.format_exc()}")

    def on_bar_close(self, u: int) -> None:
        raw = snap.fetch_candles(self.c, u)
        closed, forming = snap.split_closed(raw, u, self.now())
        if u == 1:
            v = closed["value"]
            if len(v) > 61 and v.iloc[-1] >= 3 * v.iloc[-61:-1].mean():
                self.wake.append(f"1분 거래대금 {v.iloc[-1] / v.iloc[-61:-1].mean():.1f}배")
            return
        self._merge(u, closed)
        if u == 15:
            self.vwap = snap.session_vwap(raw, self.now())
        if u == 1440:
            self.forming_daily = forming
            self.on_session_start()
        self.update_scoreboard((u,))
        if u in (60, 240, 1440):
            self.wake.append(f"{'일' if u == 1440 else str(u) + '분'}봉 마감")
        for sid, cls in LIBRARY.items():
            if cls.UNIT == u and cls.KIND == "bar" and self.s["scoreboard"].get(sid, {}).get("entry_signal_now"):
                self.wake.append(f"새 진입 신호 {sid}")
                self.s["last_signal_at"] = _iso(self.now())
        self.run_bar_strategies(u)

    def run_bar_strategies(self, u: int) -> None:
        p, plan = self.s["position"], self.s["plan"]
        df = self.hist.get(u)
        if df is None or df.empty:
            return
        if p:
            st = make_library_strategy(p["strategy"], p.get("params"))
            if st and st.KIND == "bar" and st.UNIT == u:
                e, x = st.entry_exit(df)
                held = (self.now() - _dt(p["entry_time"])).total_seconds() / 3600
                if bar_action(True, bool(e.iloc[-1]), bool(x.iloc[-1]), held, st.max_hold_h) == SELL:
                    self.sell("플랜", f"{st.ID} 청산 조건 ({df['candle_date_time_kst'].iloc[-1][5:16]} 확정봉)")
            return
        st = make_library_strategy(plan["strategy"], plan.get("params"))
        if st and st.KIND == "bar" and st.UNIT == u and not self.s["pending"]:
            e, x = st.entry_exit(df)
            if bar_action(False, bool(e.iloc[-1]), bool(x.iloc[-1]), 0, st.max_hold_h) == BUY:
                self.try_buy(st.ID, st.params, plan["size_pct"], plan["stop_loss_pct"], plan.get("confidence", 0),
                             "플랜", f"{st.ID} 진입 신호 ({df['candle_date_time_kst'].iloc[-1][5:16]} 확정봉)")

    def on_session_start(self) -> None:
        """09:00: vb_daily 보유분 매도, vwap_revert 미체결 취소·보유분 강제 청산."""
        p = self.s["position"]
        pend = self.s["pending"]
        if pend:                      # 지정가 주문은 vwap_revert 만 쓴다
            self.cancel_pending()
            p = self.s["position"]
        if p and p["strategy"] in ("vb_daily", "vwap_revert"):
            self.sell("플랜", f"{p['strategy']} 09:00 청산")

    # ================================================================ 장중 전략
    def vb_level(self) -> float | None:
        if self.forming_daily is None or 1440 not in self.hist:
            return None
        st = make_library_strategy("vb_daily", self.s["plan"].get("params"))
        daily = pd.concat([self.hist[1440], self.forming_daily.to_frame().T], ignore_index=True)
        for k in ("open", "high", "low", "close"):
            daily[k] = daily[k].astype(float)
        return st.level(daily)

    def run_intraday(self) -> None:
        plan, p, now = self.s["plan"], self.s["position"], self.now()
        if plan["strategy"] == "vb_daily" and not p and not self.s["pending"]:
            key = trading_day(now)
            lvl = self.vb_level()
            if lvl and self.price >= lvl and self.s["vb_session"] != key:
                r = self.try_buy("vb_daily", plan["params"], plan["size_pct"], plan["stop_loss_pct"],
                                 plan.get("confidence", 0), "플랜", f"vb_daily 기준가 {lvl:,.0f} 돌파")
                if r:
                    self.s["vb_session"] = key
        self.run_vwap()

    def run_vwap(self) -> None:
        plan, now = self.s["plan"], self.now()
        pend, p = self.s["pending"], self.s["position"]
        if pend:
            o = self.x.get(pend["uuid"], self.price)
            if o.get("state") == "done":
                self.s["pending"] = None
                self._settle_limit(pend, o)
            elif pend["side"] == "bid" and (plan["strategy"] != "vwap_revert"
                                            or now - _dt(pend["placed_at"]) > timedelta(minutes=30)):
                logger.info("vwap 매수 지정가 취소 (30분 미체결 또는 플랜 변경)")
                self.cancel_pending()
            elif pend["side"] == "ask" and self.vwap and abs(self.x.tick_ceil(self.vwap) - pend["price"]) >= self.x.tick \
                    and now - _dt(pend["placed_at"]) > timedelta(minutes=15):
                self.cancel_pending()            # VWAP 이 움직였으면 다시 건다
            return
        if not self.vwap or now < snap.session_start(now) + timedelta(minutes=15):
            return
        if p and p["strategy"] == "vwap_revert":
            target = self.x.tick_ceil(self.vwap)
            if self.price >= target:
                self.sell("플랜", f"VWAP {self.vwap:,.0f} 복귀")
            else:
                u = self.x.limit("ask", target, p["volume"])
                if u:
                    self.s["pending"] = {"uuid": u, "side": "ask", "price": target, "volume": p["volume"],
                                         "placed_at": _iso(now), "source": "플랜", "reason": "VWAP 복귀 지정가",
                                         "mid": self.price}
        elif not p and plan["strategy"] == "vwap_revert":
            book = self._book()
            target = self.vwap * (1 - plan["params"]["dev"] / 100)
            limit = self.x.tick_floor(min(target, book.get("best_bid") or target))
            self.try_buy("vwap_revert", plan["params"], plan["size_pct"], plan["stop_loss_pct"],
                         plan.get("confidence", 0), "플랜", f"VWAP {self.vwap:,.0f} −{plan['params']['dev']}% 지정가",
                         limit_price=limit)

    # ================================================================ 보호 장치 (매 틱)
    def protect(self) -> None:
        p = self.s["position"]
        if not p:
            return
        if kill_hit(self.cfg, self.equity_all()):
            self.sell("킬", f"전체 코인 평가금액 {self.equity_all():,.0f} ≤ 예산의 {100 - self.cfg.kill_drawdown_pct:.0f}%")
            self.s["halt"] = "킬 드로다운 (전량 매도 후 정지, /dt_go 로 재개)"
            self.notify(f"⛔ 전체 손실 한도({self.cfg.kill_drawdown_pct:.0f}%)에 닿아 전부 팔고 멈췄습니다. "
                        f"전체 코인 평가금액 {self.equity_all():,.0f}원. 다시 시작하려면 /dt_go")
            return
        if p.get("stop_price") and self.price <= p["stop_price"]:
            self.sell("손절", f"현재가 {self.price:,.0f} ≤ 손절가 {p['stop_price']:,.0f}")
            return
        held = (self.now() - _dt(p["entry_time"])).total_seconds() / 3600
        st = make_library_strategy(p["strategy"], p.get("params"))
        limit = self.cfg.max_hold_hours
        if p["strategy"] == "discretionary" and st:
            limit = min(limit, st.params["max_hold"])
        if p["strategy"] != "sma_trend_daily" and held >= limit:
            self.sell("최대보유", f"보유 {held:.1f}시간 ≥ {limit:g}시간")

    def check_limits(self) -> None:
        if not self.s["weekly_stop"] and weekly_loss_hit(self.cfg, self.pnl_7d_all()):
            self.s["weekly_stop"] = True
            self.notify(f"⛔ 전체 코인 최근 7일 손실이 한도에 닿았습니다 ({self.pnl_7d_all():+,.0f}원). 새로 사지 않습니다. 재개: /dt_go")
        dl = -self.cfg.budget_krw * self.cfg.daily_loss_pct / 100
        key = trading_day(self.now())
        if self.day_pnl_all() <= dl and self.s.get("daily_notified") != key:
            self.s["daily_notified"] = key
            self.notify(f"⚠️ 전체 코인 오늘 손실이 한도에 닿았습니다 ({self.day_pnl_all():+,.0f}원). 내일 09시까지 새로 사지 않습니다")

    def reconcile(self) -> None:
        """1시간마다 계좌 BTC ≥ 봇 기록 확인 (50일선 봇 물량이 더해질 수 있어 같음이 아니라 이상)."""
        p = self.s["position"]
        if not (self.cfg.live and p):
            return
        a = self._accounts().get(snap.COIN, {})
        have = float(a.get("balance", 0)) + float(a.get("locked", 0))
        if have < p["volume"] * 0.999:
            self.s["halt"] = f"잔고 불일치 (계좌 {have:.8f} < 기록 {p['volume']:.8f})"
            self.notify(f"⛔ {self.s['halt']} — 신규 매수 중지. 확인 후 /dt_go")

    # ================================================================ 에이전트
    def snapshot(self, data: dict) -> dict:
        now, plan, p = self.now(), self.s["plan"], self.s["position"]
        lib = {}
        for sid, cls in LIBRARY.items():
            lib[sid] = {"unit": "자유" if sid == "discretionary" else ("1d" if cls.UNIT == 1440 else f"{cls.UNIT}m"),
                        "rule": cls.RULE, "ranges": {k: [lo, hi, d] for k, (lo, hi, d) in cls.RANGES.items()},
                        "evidence": EVIDENCE[sid], "score": self.s["scoreboard"].get(sid)}
        lib["hold_baseline"] = {"evidence": EVIDENCE["hold"], "score": self.s["scoreboard"].get("hold")}
        set_at = _dt(plan.get("set_at"))
        change_at = set_at + timedelta(minutes=self.cfg.min_plan_hold_min) if set_at else None
        pos = None
        if p:
            pos = {**{k: p[k] for k in ("volume", "entry_price", "strategy", "params", "source", "stop_price")},
                   "unrealized_pct": round((self.price * (1 - FEE) * p["volume"] / p["cost_krw"] - 1) * 100, 2),
                   "held_h": round((now - _dt(p["entry_time"])).total_seconds() / 3600, 1)}
        disc = p["cost_krw"] / self.cfg.budget_krw * 100 if p and p["strategy"] == "discretionary" else 0
        return {
            "time": _iso(now), "coin": f"{snap.COIN_KO} ({snap.MARKET})", "market": data["market"], "candles": snap.candles_section(data),
            "library": lib, "excluded": EXCLUDED,
            "plan": {**plan, "change_allowed_at": _iso(change_at) if change_at and change_at > now else "지금"},
            "pending_order": self.s["pending"],
            "me": {"position": pos, "buys_today": self.s["day"]["buys"],
                   "pnl_today_krw": round(self.day_pnl()), "pnl_7d_krw": round(self.pnl_7d()),
                   "equity_krw": round(self.equity()), "budget_krw": self.cfg.budget_krw,
                   "cash_left_krw": round(self.cash()),
                   "budget_note": "budget_krw 는 여러 코인 봇이 같이 쓰는 돈이다. cash_left_krw 는 다른 코인이 이미 쓴 돈을 뺀 남은 돈", "discretionary_used_pct": round(disc, 1),
                   "halted": self.halt_reason() or (self.s["weekly_stop"] and "주간 손실 정지") or
                   (self.stopped() and "DT_STOP") or "",
                   "recent_decisions": [{**d, "price_change_since_pct": round((self.price / d["price"] - 1) * 100, 2)}
                                        for d in self.s["decisions"][-5:] if d.get("price")],
                   "recent_trades": self.s["trades"][-5:], "recent_rejections": self.s["rejections"][-5:]},
            "limits": {"max_buys_per_day": self.cfg.max_buys_per_day or "제한 없음", "daily_loss_pct": self.cfg.daily_loss_pct,
                       "weekly_loss_pct": self.cfg.weekly_loss_pct, "kill_drawdown_pct": self.cfg.kill_drawdown_pct,
                       "stop_range_pct": [self.cfg.stop_min_pct, self.cfg.stop_max_pct],
                       "max_hold_h": self.cfg.max_hold_hours, "min_confidence": self.cfg.min_confidence,
                       "discretionary_max_pct": self.cfg.discretionary_max_pct}}

    def _lessons(self) -> str:
        try:
            with open(self.lessons_path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    # ---------------------------------------------------------------- LLM 호출 (빠른 점검 → 깊은 판단)
    def _llm_day(self) -> dict:
        l, key = self.s["llm"], trading_day(self.now())
        if l.get("date") != key:
            l.update(date=key, quick=0, deep=0, calls=0, routes={}, limit_errors=0, cap_notified=[],
                     **{"in": 0, "out": 0, "cache_creation": 0, "cache_read": 0})
            self.router_limit_base = getattr(self.llm, "limit_errors", 0)
        period = credit_period(self.now(), self.cfg.api_reset_day)
        if period and l.get("api_period") != period:
            if l.get("api_period"):               # 처음 켤 때는 지금까지 쓴 돈을 이번 달 몫으로 둔다
                l.update(api_total_usd=0.0, api_total_calls=0, api_total_in=0, api_total_out=0)
                if hasattr(self.llm, "budget_notified"):
                    self.llm.budget_notified = False
                logger.info(f"API 월간 크레딧 충전일 — 누적 비용 0 으로 ({period})")
            l["api_period"] = period
        return l

    def api_cost_today(self) -> float:
        return float(self._llm_day()["routes"].get("api", {}).get("cost_usd", 0.0))

    def _cap_ok(self, kind: str, urgent: bool = False) -> bool:
        l = self._llm_day()
        cap = self.cfg.max_quick_calls_per_day if kind == "quick" else self.cfg.max_deep_calls_per_day
        if l[kind] < cap or (kind == "deep" and urgent and self.s["position"]):
            return True
        if kind not in l["cap_notified"]:
            l["cap_notified"].append(kind)
            self.notify(f"AI {'빠른 점검' if kind == 'quick' else '깊은 판단'}이 하루 상한 {cap}회에 닿았습니다. 내일 09시까지 "
                        + ("지금 플랜대로만 움직입니다 (보유 중 급락 판단은 예외)" if kind == "deep" else "점검을 쉽니다"))
        return False

    def _count(self, kind: str, meta: dict) -> None:
        l = self._llm_day()
        l[kind] += 1
        l["calls"] = l.get("calls", 0) + 1
        l["total_calls"] = l.get("total_calls", 0) + 1
        for k in ("in", "out", "cache_creation", "cache_read"):
            l[k] = l.get(k, 0) + (meta.get(k) or 0)
            l[f"total_{k}"] = l.get(f"total_{k}", 0) + (meta.get(k) or 0)
        r = l["routes"].setdefault(meta.get("route", "?"), {"calls": 0, "cache_hits": 0, "cost_usd": 0.0})
        r["calls"] += 1
        r["cache_hits"] += int((meta.get("cache_read") or 0) > 0)
        r["cost_usd"] += meta.get("cost_usd") or 0.0
        if meta.get("route") == "api":     # 누적 (날이 바뀌어도 안 지움) → DT_API_TOTAL_BUDGET_USD
            l["api_total_usd"] = l.get("api_total_usd", 0.0) + (meta.get("cost_usd") or 0.0)
            l["api_total_calls"] = l.get("api_total_calls", 0) + 1
            l["api_total_in"] = l.get("api_total_in", 0) + sum(meta.get(k) or 0 for k in ("in", "cache_creation", "cache_read"))
            l["api_total_out"] = l.get("api_total_out", 0) + (meta.get("out") or 0)
            l["api_last_at"] = _iso(self.now())
        if meta.get("limits"):               # 구독 사용률 (내 Claude Code 사용분과 같이 깎인다)
            self.s["sub_limits"] = {**meta["limits"], "at": _iso(self.now())}
        l["limit_errors"] = getattr(self.llm, "limit_errors", 0) - getattr(self, "router_limit_base", 0)
        if meta.get("route") == "api" and not meta.get("cache_read") and not meta.get("cache_creation"):
            last = _dt(self.s.get("cache_alert_at"))
            if not last or self.now() - last > timedelta(hours=1):
                self.s["cache_alert_at"] = _iso(self.now())
                self.notify(f"⚠️ API 호출에서 프롬프트 캐시가 안 됐습니다 ({meta.get('model')}). 비용이 더 나옵니다")

    def check_interval_min(self) -> float:
        """빠른 점검 간격: 신호 임박 1분 / 보유 2분 / 조용 5분. API 하루 비용 초과면 2배."""
        if self.hot():
            m = self.cfg.check_hot_min
        elif self.s["position"]:
            m = self.cfg.check_holding_min
        else:
            m = self.cfg.check_idle_min
        return m * (2 if self.api_cost_today() > self.cfg.api_daily_budget_usd else 1)

    def hot(self) -> bool:
        """신호 임박(라이브러리 전략 진입가 0.3% 이내, 1h RSI 65~70 상승) 또는 신호 발생 후 30분."""
        t = _dt(self.s.get("last_signal_at"))
        if t and self.now() - t <= timedelta(minutes=30):
            return True
        px = self.price
        if not px:
            return False
        near = lambda lvl: lvl and abs(px / lvl - 1) <= 0.003
        h1, d1 = self.hist.get(60), self.hist.get(1440)
        if h1 is not None and len(h1) > 30:
            from .indicators import calc_rsi
            rsi = calc_rsi(h1["close"], 14)
            if 65 <= rsi.iloc[-1] < 70 and rsi.iloc[-1] > rsi.iloc[-2]:
                return True
            if near(float(h1["high"].tail(24).max())):
                return True
        if d1 is not None and len(d1) > 50 and near(float(d1["close"].tail(49).mean())):
            return True
        if near(self.vb_level()) or (self.vwap and near(self.vwap * 0.99)):
            return True
        return False

    def _prepare(self) -> tuple[dict, str]:
        self.data = snap.collect(self.c, self.now())
        self.price = self.data["market"]["price"]
        sn = self.snapshot(self.data)
        return sn, json.dumps(sn, ensure_ascii=False, separators=(",", ":"), default=str)

    def _llm_job(self, kind: str, user: str, lessons: str, urgent_ok: bool) -> dict:
        """워커 스레드에서 돈다: LLM 호출만 하고 상태는 건드리지 않는다 (적용은 메인 스레드)."""
        out = {"kind": kind, "calls": []}

        def call(prompt, model):
            t0 = time.time()
            try:
                text, meta = self.llm(prompt, lessons, user, model)
            except Exception as e:
                text, meta = None, {"error": str(e)[:300]}
            meta["sec"] = round(time.time() - t0, 1)
            out["calls"].append({"prompt": prompt, "meta": meta, "raw": text})
            return text, meta

        if kind == "quick":
            text, meta = call("dt_quick.md", self.cfg.quick_model)
            q = parse_quick(text)
            out["quick"] = q
            if q["verdict"] != "escalate" and not q["urgent_sell"]:
                return out
            if not urgent_ok:
                out["deep_skipped"] = "깊은 판단 하루 상한"
                return out
        text, meta = call("dt_system.md", self.cfg.deep_model)
        out["deep_raw"], out["deep_meta"] = text, meta
        return out

    def start_llm(self, kind: str, wake: list[str]) -> None:
        if self.job is not None:          # 이전 호출이 안 끝났으면 건너뛴다
            return
        if not self._cap_ok(kind):
            return
        sn, user = self._prepare()
        urgent_ok = self._cap_ok("deep", urgent=True) if kind == "quick" else True
        lessons = self._lessons()
        self.job = {"wake": wake, "snapshot": sn, "user_chars": len(user), "price": self.price,
                    "started": self.now()}
        if self.async_llm:
            self.job["future"] = self.pool.submit(self._llm_job, kind, user, lessons, urgent_ok)
        else:
            self.job["result"] = self._llm_job(kind, user, lessons, urgent_ok)
            self.finish_llm()

    def finish_llm(self) -> dict | None:
        """끝난 호출 결과를 메인 스레드에서 적용."""
        job = self.job
        if job is None:
            return None
        if "future" in job:
            if not job["future"].done():
                return None
            try:
                job["result"] = job["future"].result()
            except Exception as e:
                job["result"] = {"kind": "?", "calls": [], "error": str(e)}
        self.job = None
        r = job["result"]
        for c in r["calls"]:
            if "error" not in c["meta"] or c["raw"] is not None:
                self._count("quick" if c["prompt"] == "dt_quick.md" else "deep", c["meta"])
        now = self.now()
        rec = {"time": _iso(job["started"]), "done": _iso(now), "wake": job["wake"], "kind": r["kind"],
               "price": job["price"], "quick": r.get("quick"), "deep_skipped": r.get("deep_skipped"),
               "calls": [{"prompt": c["prompt"], **c["meta"]} for c in r["calls"]],
               "snapshot_chars": job["user_chars"]}
        nxt = self.check_interval_min()
        if "deep_raw" in r or (r["calls"] and r["calls"][-1]["prompt"] == "dt_system.md"):
            raw = r.get("deep_raw")
            d, notes = parse_decision(self.cfg, raw)
            if r.get("deep_meta", {}).get("error"):
                notes.append(f"llm_error: {r['deep_meta']['error']}")
            rec.update(decision=d, notes=notes, raw=raw, rejected=self.apply(d), plan_after=dict(self.s["plan"]),
                       equity=round(self.equity()))
            self.s["decisions"] = (self.s["decisions"] + [{
                "time": rec["time"], "regime": d["regime"], "strategy": self.s["plan"]["strategy"],
                "action": d["action"], "confidence": d["confidence"], "reason": d["reason"][:200], "easy": d.get("easy", ""),
                "price": job["price"]}])[-20:]
            self.s["last_llm_price"], self.s["last_llm_at"] = job["price"], _iso(now)
            nxt = min(nxt, d["next_check_min"])
            logger.info(f"깊은 판단 [{', '.join(job['wake'])}] {d['regime']} {d['plan'].get('strategy')} "
                        f"{d['action']} conf={d['confidence']:.2f} → 플랜 {self.s['plan']['strategy']} / "
                        f"거절 {rec['rejected']}")
        elif r.get("quick"):
            logger.info(f"빠른 점검 [{', '.join(job['wake'])}] {r['quick']['verdict']}: {r['quick']['reason'][:120]}")
        self.s["next_check_at"] = _iso(now + timedelta(minutes=nxt))
        self._append("dt_decisions.jsonl", {**rec, "snapshot": job["snapshot"]})
        return rec

    def decide(self, wake: list[str], kind: str = "deep", dry: bool = False) -> dict | None:
        """동기 호출 (테스트·점검용). dry=True 면 적용·기록 없이 결과만."""
        if dry:
            sn, user = self._prepare()
            r = self._llm_job(kind, user, self._lessons(), True)
            return {**r, "snapshot_chars": len(user), "snapshot": sn}
        if self.job is not None or not self._cap_ok(kind):
            return None
        was_async, self.async_llm = self.async_llm, False
        try:
            self.start_llm(kind, wake)
        finally:
            self.async_llm = was_async
        return self._last_rec()

    def _last_rec(self) -> dict | None:
        rows = self._read_jsonl("dt_decisions.jsonl")
        return rows[-1] if rows else None

    def apply(self, d: dict) -> list[str]:
        """판단 적용. 반환: 거절·보류 사유."""
        out, now, plan = [], self.now(), self.s["plan"]
        new = d["plan"]
        sid = new["strategy"]
        changed = sid != "keep" and (sid != plan["strategy"] or (new.get("params") or {}) != (plan.get("params") or {}))
        if changed:
            set_at = _dt(plan.get("set_at"))
            left = self.cfg.min_plan_hold_min - (now - set_at).total_seconds() / 60 if set_at else 0
            if sid != "none" and left > 0:
                out.append(f"플랜 변경 보류: 최소 유지 {left:.0f}분 남음")
            else:
                old = plan["strategy"]
                self.s["plan"] = {"strategy": sid, "params": new.get("params", {}),
                                  "size_pct": new.get("size_pct", plan.get("size_pct", 50)),
                                  "stop_loss_pct": new.get("stop_loss_pct", plan.get("stop_loss_pct", 3.0)),
                                  "confidence": d["confidence"], "set_at": _iso(now), "reason": new.get("reason", ""),
                                  "easy": d.get("easy", "")}
                note = self._handover(sid)
                self.notify(f"🧭 전략을 바꿨습니다: {ko(old)} → {ko(sid)}"
                            + (f"\n설정: {ko_params(new.get('params'))}" if sid not in ("none", "keep") else "")
                            + (f"\n살 때 예산의 {self.s['plan']['size_pct']:.0f}%, 손절 {self.s['plan']['stop_loss_pct']}%"
                               if sid not in ("none", "keep") else "")
                            + f"\n확신도 {d['confidence'] * 100:.0f}%{note}\n이유: {(d.get('easy') or new.get('reason', ''))[:300]}")
        elif sid != "keep":
            plan["confidence"] = d["confidence"]   # 같은 플랜 재확인: 확신도만 갱신
        a = d["action"]
        if a == "buy_now":
            st = make_library_strategy("discretionary", {})
            size = d["action_size_pct"] or self.s["plan"].get("size_pct", 25)
            r = self.try_buy("discretionary", st.params, size, clamp_stop(self.cfg, self.s["plan"].get("stop_loss_pct", 3)),
                             d["confidence"], "재량", d.get("easy") or d["reason"])
            if not r:
                out.append(self.s["rejections"][-1]["reason"] if self.s["rejections"] else "매수 실패")
        elif a == "sell_now":
            if not self.s["position"]:
                out.append("sell_now: 보유 없음")
            else:
                self.sell("재량", d.get("easy") or d["reason"], d["action_size_pct"] or 100)
        for r in out:
            if not r.startswith("discretionary 매수"):
                self._reject(r)
        return out

    def _handover(self, sid: str) -> str:
        p = self.s["position"]
        if not p or p["strategy"] == sid:
            return ""
        st = make_library_strategy(sid, self.s["plan"]["params"]) if sid in LIBRARY else None
        df = self.hist.get(st.UNIT) if st else None
        if st and st.KIND == "bar" and df is not None and len(df) and st.holds(df):
            prev = p["strategy"]
            p["strategy"], p["params"] = sid, st.params
            if sid == "sma_trend_daily":
                p["stop_price"] = p["stop_pct"] = None
            return f"\n들고 있던 코인은 새 전략({ko(sid)})이 이어서 관리합니다 (이전: {ko(prev)})"
        return f"\n들고 있던 코인은 이전 전략({ko(p['strategy'])})의 매도 규칙을 계속 따릅니다 (새 전략 청산 규칙 유지)"

    # ================================================================ 복기·요약
    def daily_review(self) -> None:
        now = self.now()
        since = now - timedelta(hours=24)
        decs = [d for d in self._read_jsonl("dt_decisions.jsonl") if _dt(d["time"]) >= since and d.get("decision")]
        c = self.hist.get(60)
        def chg(t, h):
            if c is None or c.empty:
                return None
            ts = pd.to_datetime(c["candle_date_time_kst"])
            t0 = pd.Timestamp(_dt(t).replace(tzinfo=None))
            a = c["close"][ts >= t0]; b = c["close"][ts >= t0 + pd.Timedelta(hours=h)]
            return round(float(b.iloc[0] / a.iloc[0] - 1) * 100, 2) if len(a) and len(b) else None
        rows = [{"time": d["time"], "regime": d["decision"]["regime"], "plan": d.get("plan_after", {}).get("strategy"),
                 "action": d["decision"]["action"], "confidence": d["decision"]["confidence"],
                 "reason": d["decision"]["reason"][:300], "rejected": d.get("rejected"),
                 "chg_1h": chg(d["time"], 1), "chg_4h": chg(d["time"], 4), "chg_24h": chg(d["time"], 24)} for d in decs]
        trades = [t for t in self._read_csv("dt_trades.csv") if _dt(t["time"]) >= since]
        user = json.dumps({"decisions": rows, "trades": trades, "scoreboard": self.s["scoreboard"],
                           "lessons": self._lessons()}, ensure_ascii=False, default=str)
        rev, dropped = None, []
        l = self._llm_day()
        routes = "\n".join(
            f"  경로 {k}: {v['calls']}회" + (f", 캐시 적중 {v['cache_hits']}/{v['calls']}" if k == "api" else "")
            + f", 추정 ${v['cost_usd']:.2f}" for k, v in l["routes"].items()) or "  호출 없음"
        llm_line = (f"AI 빠른 점검 {l['quick']}번 · 깊은 판단 {l['deep']}번 · 구독 한도 오류 {l['limit_errors']}번\n"
                    + routes)
        if self._cap_ok("deep"):
            try:
                raw, meta = self.llm("dt_review.md", "", user, self.cfg.deep_model)
                self._count("deep", meta)
                rev = parse_review(raw)
                lessons, dropped = filter_lessons(rev["lessons_md"])
                with open(self.lessons_path, "w", encoding="utf-8") as f:
                    f.write(lessons + "\n")
                self._append("dt_reviews.jsonl", {"time": _iso(now), "review": rev, "dropped": dropped, "raw": raw})
            except Exception as e:
                logger.warning(f"복기 실패: {e}")
        day = self.realized_since(since)
        self.notify(f"🌅 일일 요약 — 확정 손익 {day:+,.0f}원, 보유분 평가손익 {self.unrealized():+,.0f}원, 7일 {self.pnl_7d():+,.0f}원\n"
                    f"거래 {len(trades)}건, 판단 기록 {len(decs)}건\n{llm_line}\n"
                    f"지금 전략 {ko(self.s['plan']['strategy'])} · 평가금액 {self.equity():,.0f}원"
                    + (f"\n복기: 잘한 것 {len(rev['good'])} / 못한 것 {len(rev['bad'])}"
                       f"{f', 한도 완화 문장 {len(dropped)}줄 삭제' if dropped else ''}" if rev else ""))

    def weekly_summary(self) -> None:
        since = self.now() - timedelta(days=7)
        trades = [t for t in self._read_csv("dt_trades.csv") if _dt(t["time"]) >= since]
        by = {}
        for t in trades:
            if t["side"] == "매도":
                b = by.setdefault(t["strategy"], [0, 0.0]); b[0] += 1; b[1] += float(t["pnl_krw"])
        sb = {k: v["d7"]["ret"] for k, v in self.s["scoreboard"].items() if isinstance(v, dict) and "d7" in v}
        top = max(sb, key=sb.get) if sb else "-"
        lines = [f"📅 주간 요약 — 에이전트 7일 손익 {self.pnl_7d():+,.0f}원 ({self.pnl_7d() / self.cfg.budget_krw * 100:+.2f}%)"]
        lines += [f"  · {ko(k)}: {n}번 거래, {p:+,.0f}원" for k, (n, p) in by.items()] or ["  · 거래 없음"]
        lines.append(f"이번 주 가장 잘 맞았을 전략: {ko(top)} {sb.get(top, 0):+.2f}% "
                     f"(그냥 들고 있었으면 {sb.get('hold', 0):+.2f}%)")
        self.notify("\n".join(lines))

    def _read_jsonl(self, name: str) -> list[dict]:
        try:
            with open(os.path.join(self.log_dir, name), encoding="utf-8") as f:
                return [json.loads(x) for x in f if x.strip()]
        except OSError:
            return []

    def _read_csv(self, name: str) -> list[dict]:
        try:
            with open(os.path.join(self.log_dir, name), encoding="utf-8") as f:
                return list(csv.DictReader(f))
        except OSError:
            return []

    # ================================================================ 명령
    def handle(self, cmd: str) -> None:
        p, plan = self.s["position"], self.s["plan"]
        if cmd == "/dt_status":
            pos = (f"보유 중: {p['volume']:.8f} {snap.COIN} (평균 {p['entry_price']:,.0f}원, {ko(p['strategy'])}, "
                   f"평가손익 {self.unrealized():+,.0f}원)" if p else "보유 없음 (현금)")
            stop = self.halt_reason() or ("최근 7일 손실 한도" if self.s["weekly_stop"] else "") or \
                ("새로 사기 중지 (/dt_stop)" if self.stopped() else "없음")
            self.notify(f"📋 상태 — {snap.COIN_KO} {self.price:,.0f}원\n{pos}\n지금 전략: {ko(plan['strategy'])}\n"
                        f"오늘 {self.day_pnl():+,.0f}원 · 7일 {self.pnl_7d():+,.0f}원 · 평가금액 {self.equity():,.0f}원\n"
                        f"오늘 매수 {self.s['day']['buys']}번"
                        + (f"/{self.cfg.max_buys_per_day}" if self.cfg.max_buys_per_day else "") + " · "
                        f"AI 점검 {self.s['llm'].get('quick', 0)}번, 깊은 판단 {self.s['llm'].get('deep', 0)}번\n멈춤: {stop}")
        elif cmd == "/dt_plan":
            self.notify(f"🧭 지금 전략: {ko(plan['strategy'])}"
                        + (f" ({ko_params(plan.get('params'))}, 예산의 {plan.get('size_pct')}%, 손절 {plan.get('stop_loss_pct')}%)"
                           if plan["strategy"] not in ("none", "keep") else "")
                        + f"\n정한 시각: {(plan.get('set_at') or '-')[5:16].replace('T', ' ')}\n이유: {plan.get('easy') or plan.get('reason', '')}")
        elif cmd == "/dt_stop":
            open(self.stop_path, "w").close()
            self.notify("신규 매수 중지 (state/DT_STOP). 보유분 관리는 계속. /dt_go 로 재개")
        elif cmd == "/dt_go":
            for f in {self.stop_path, STOP_FILE} if self.shared else {self.stop_path}:
                if os.path.exists(f):
                    os.remove(f)
            self.s["weekly_stop"], self.s["halt"], self.x.errors = False, "", 0
            self.notify("재개 — DT_STOP·주간 정지·정지 상태 해제")
        elif cmd == "/dt_flat":
            self.s["flat_pending_at"] = _iso(self.now())
            self.notify("전량 매도 후 중지합니다. 60초 안에 /dt_yes 로 확인")
        elif cmd == "/dt_yes":
            t = _dt(self.s["flat_pending_at"])
            self.s["flat_pending_at"] = None
            if not t or self.now() - t > timedelta(seconds=60):
                self.notify("확인할 /dt_flat 이 없습니다 (60초 안에 /dt_yes)")
                return
            open(self.stop_path, "w").close()
            ok = self.sell("수동", "/dt_flat") if self.s["position"] else True
            self.cancel_pending()
            self.notify("전량 매도 완료, 신규 매수 중지" if ok else "매도 실패 — 로그 확인")

    # ================================================================ 루프
    def start(self) -> bool:
        if self.cfg.budget_krw <= 0:
            logger.error("DT_BUDGET_KRW 가 필요합니다.")
            return False
        if self.cfg.live:
            why = self.x.preflight()
            if why:
                logger.error(f"업비트 키·권한·IP 확인 실패 → 시작하지 않음: {why}")
                return False
        self.price = float(self.c._request("GET", "/v1/ticker", params={"markets": snap.MARKET})[0]["trade_price"])
        p = self.s["position"]
        if self.cfg.live:
            btc = self._accounts().get(snap.COIN, {})
            have = float(btc.get("balance", 0)) + float(btc.get("locked", 0))
            if p and have < p["volume"] * 0.999:
                self.s["halt"] = f"잔고 불일치 (계좌 {have:.8f} < 기록 {p['volume']:.8f})"
            elif not p and self.cfg.adopt_account_btc and have * self.price >= 5_000:
                avg = float(btc.get("avg_buy_price", 0)) or self.price
                self.s["position"] = {"volume": have, "entry_price": avg, "cost_krw": have * avg,
                                      "entry_time": _iso(self.now()), "strategy": "discretionary",
                                      "params": {"max_hold": 96}, "source": "인계", "stop_pct": 3.0,
                                      "stop_price": avg * 0.97}
                self.wake.append("계좌 BTC 인계 — 처리 전략 결정 필요")
        self.x.load_tick()
        self.load_history()
        d1 = snap.fetch_candles(self.c, 1440)
        self.forming_daily = snap.split_closed(d1, 1440, self.now())[1]
        self.vwap = snap.session_vwap(snap.fetch_candles(self.c, 15), self.now())
        self.s["started_at"] = _iso(self.now())
        self.wake.append("시작")
        self.save()
        sb = "\n".join(f"  · {ko(k)} {v['d30']['ret']:+.1f}%" for k, v in self.s["scoreboard"].items() if "d30" in v)
        self.notify(f"▶️ 시작했습니다 — 예산 {self.cfg.budget_krw:,.0f}원\n"
                    f"판단: {'Claude 구독' if self.cfg.llm_primary == 'claude_cli' else 'Claude API'} "
                    f"(빠른 점검 Haiku · 깊은 판단 Sonnet)\n"
                    f"지금 전략: {ko(self.s['plan']['strategy'])} · 보유 {'있음' if self.s['position'] else '없음'}"
                    + (f"\n⛔ {self.s['halt']}" if self.s["halt"] else "")
                    + f"\n\n최근 30일 동안 각 전략을 그대로 따랐다면 (주문 없이 계산):\n{sb}")
        return True

    def tick(self) -> None:
        now = self.now()
        key = trading_day(now)
        if self.s["day"]["date"] != key:
            self.s["day"] = {"date": key, "buys": 0}
        for cmd in notifier._drain_inbox(self.inbox):
            self.handle(cmd)
        self.price = float(self.c._request("GET", "/v1/ticker", params={"markets": snap.MARKET})[0]["trade_price"])
        self.protect()
        self.check_bars()
        self.run_intraday()
        self.check_limits()
        if not self._last_reconcile or now - self._last_reconcile >= timedelta(hours=1):
            self._last_reconcile = now
            self.reconcile()
        # 깨우기. 깊은 판단 바로: 새 신호·체결·1h/4h/1d 마감·플랜 유지시간 종료·FOMC/CPI·시작.
        # 나머지(예약 점검, 1% 급변, 1분 거래대금 3배)는 빠른 점검 먼저 → escalate 면 깊은 판단
        self.finish_llm()
        set_at = _dt(self.s["plan"].get("set_at"))
        if set_at and self.s["plan"]["strategy"] != "none" and self.s["hold_end_fired"] != self.s["plan"]["set_at"] \
                and now >= set_at + timedelta(minutes=self.cfg.min_plan_hold_min):
            self.s["hold_end_fired"] = self.s["plan"]["set_at"]
            self.wake.append("플랜 최소 유지시간 종료")
        nxt, lp = _dt(self.s["next_check_at"]), self.s["last_llm_price"]
        # 호출 중에는 예약 점검을 쌓지 않는다 (쌓으면 호출이 끝나자마자 같은 점검을 한 번 더 부른다)
        if self.job is None and (not nxt or now >= nxt):
            self.wake.append("예약 점검")
        if lp and abs(self.price / lp - 1) * 100 >= self.cfg.wake_move_pct:
            self.wake.append(f"가격 {(self.price / lp - 1) * 100:+.2f}%")
        span_h = (now - (self._last_tick or now)).total_seconds() / 3600
        for ev in snap.load_events():
            try:
                h = (datetime.fromisoformat(ev["time"]) - now).total_seconds() / 3600
            except (KeyError, ValueError, TypeError):
                continue
            for edge, label in ((0.5, "30분 전"), (-1.0, "60분 뒤")):
                if h + span_h > edge >= h:
                    self.wake.append(f"{ev.get('name', '이벤트')} {label}")
        if self.wake and self.job is None:
            wake, self.wake = list(dict.fromkeys(self.wake)), []
            quick_only = all(w.startswith(QUICK_WAKES) for w in wake)
            self.start_llm("quick" if quick_only else "deep", wake)
        if now.hour == 9 and now.minute >= 5 and self.s["last_review"] != key:
            self.s["last_review"] = key
            self.daily_review()
        if now.weekday() == 0 and now.hour == 9 and now.minute >= 10 and self.s["last_weekly"] != key:
            self.s["last_weekly"] = key
            self.weekly_summary()
        if not self._last_equity_log or now - self._last_equity_log >= timedelta(minutes=5):
            self._last_equity_log = now
            p = self.s["position"]
            self._append("dt_equity.csv", {"time": _iso(now), "price": round(self.price), "equity": round(self.equity()),
                                           "volume": p["volume"] if p else 0, "plan": self.s["plan"]["strategy"],
                                           "position_strategy": p["strategy"] if p else ""})
        self._last_tick = now
        self.save()

    def run(self, interval: int = 10) -> None:
        if not self.start():
            return
        last_ok = time.time()
        try:
            while True:
                try:
                    self.tick()
                    gap = time.time() - last_ok
                    if gap > 300:    # 인터넷 끊김·맥 잠자기로 멈췄다가 돌아옴 (그동안 손절 확인도 못 했다)
                        self.notify(f"⚠️ {gap / 60:.0f}분 동안 멈춰 있다가 방금 다시 돌기 시작했어요. "
                                    "인터넷이 끊겼거나 맥이 잠들었던 것 같아요 (그동안 매매·손절 확인 못 함)")
                    last_ok = time.time()
                except Exception as e:
                    logger.error(f"틱 오류: {e}\n{traceback.format_exc()}")
                    time.sleep(20)
                time.sleep(interval)
        except KeyboardInterrupt:
            self.save()
            self.notify(f"종료 — 포지션 유지, 상태 저장. 보유 {'있음' if self.s['position'] else '없음'}, "
                        f"평가 {self.equity():,.0f}원")
            logger.info("btc-agent 종료")
