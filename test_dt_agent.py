"""
test_dt_agent.py — BTC 자율 에이전트(--mode btc-agent) 가짜 클라이언트 테스트. 네트워크·주문 없음.

    ./venv/bin/python test_dt_agent.py
"""

import json
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta

import numpy as np

from bot.config import DtConfig
from bot.dt_agent import filter_lessons, parse_decision
from bot.dt_executor import DtExecutor
from bot.dt_guard import BuyContext, check_buy as guard_buy
from bot.dt_live import DtBot
from bot.dt_snapshot import KST, to_df
from bot.upbit_client import UpbitClientError

NOW = datetime(2026, 11, 10, 14, 0, 5, tzinfo=KST)
CFG = DtConfig(budget_krw=1_000_000, live=True)


def candles(unit, n=200, trend=0.0, now=NOW, seed=1):
    """업비트 형식(최신 먼저). 마지막 봉은 진행 중."""
    minutes = 7 * 1440 if unit == "week" else int(unit)
    rng = np.random.default_rng(seed)
    end = now.replace(tzinfo=None)
    if minutes >= 1440:
        start_last = end.replace(hour=9, minute=0, second=0)
    else:
        m = (end.hour * 60 + end.minute) // minutes * minutes
        start_last = end.replace(hour=m // 60, minute=m % 60, second=0)
    px, out = 100_000_000.0, []
    for i in range(n):
        t = start_last - timedelta(minutes=minutes * (n - 1 - i))
        o = px
        px *= 1 + trend + rng.normal(0, 0.002)
        out.append({"candle_date_time_kst": t.strftime("%Y-%m-%dT%H:%M:%S"),
                    "candle_date_time_utc": (t - timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%S"),
                    "opening_price": o, "high_price": max(o, px) * 1.001, "low_price": min(o, px) * 0.999,
                    "trade_price": px, "candle_acc_trade_volume": 10.0, "candle_acc_trade_price": 10 * px})
    return out[::-1]


class FakeUpbit:
    def __init__(self, price=100_000_000, krw=2_000_000, btc=0.0):
        self.price, self.krw, self.btc = price, krw, btc
        self.orders, self.tests = {}, 0
        self.fill_ratio = [1.0]          # 주문마다 체결 비율 (마지막 값 반복)
        self.drop_next = False           # 다음 주문 응답을 끊는다 (주문은 접수됨)
        self.n = 0

    def _request(self, method, endpoint, params=None, data=None, auth=False, retry=True):
        p = params or {}
        if endpoint == "/v1/ticker":
            return [{"trade_price": self.price, "signed_change_rate": 0, "acc_trade_price_24h": 1e11}]
        if endpoint == "/v1/orderbook":
            u = [{"ask_price": self.price + 1000, "bid_price": self.price, "ask_size": 1, "bid_size": 1}] * 15
            return [{"orderbook_units": u}]
        if endpoint == "/v1/accounts":
            return [{"currency": "KRW", "balance": str(self.krw), "locked": "0"},
                    {"currency": "BTC", "balance": str(self.btc), "locked": "0", "avg_buy_price": "0"}]
        if endpoint == "/v1/market/all":
            return [{"market": "KRW-BTC", "market_event": {"warning": False}}]
        if endpoint == "/v1/trades/ticks":
            return [{"trade_price": self.price, "trade_volume": 0.01, "ask_bid": "BID", "timestamp": 1_000_000 + i}
                    for i in range(200)]
        if endpoint.startswith("/v1/candles/"):
            unit = "week" if "weeks" in endpoint else 1440 if "days" in endpoint else int(endpoint.split("/")[-1])
            return candles(unit, p.get("count", 200))
        if endpoint == "/v1/orders/test":
            self.tests += 1
            return {"uuid": "test"}
        if endpoint == "/v1/orders" and method == "POST":
            return self._order(data)
        if endpoint == "/v1/order" and method == "GET":
            if "identifier" in p:
                for o in self.orders.values():
                    if o["identifier"] == p["identifier"]:
                        return o
                raise UpbitClientError("API 에러 [404]: order_not_found")
            return self.orders[p["uuid"]]
        if endpoint == "/v1/order" and method == "DELETE":
            o = self.orders[p["uuid"]]
            if o["state"] == "wait":
                o["state"] = "cancel"
            return o
        raise AssertionError(f"모르는 호출 {method} {endpoint}")

    def _order(self, d):
        self.n += 1
        ratio = self.fill_ratio[min(self.n - 1, len(self.fill_ratio) - 1)]
        u = f"o{self.n}"
        o = {"uuid": u, "identifier": d["identifier"], "side": d["side"], "ord_type": d["ord_type"],
             "state": "wait", "trades": [], "paid_fee": "0"}
        if d["ord_type"] in ("best", "market"):
            if d["side"] == "bid":
                funds = float(d["price"]) * ratio
                vol = funds / (self.price + 1000)
            else:
                vol = float(d["volume"]) * ratio
                funds = vol * self.price
            o.update(state="cancel" if ratio < 1 else "done", paid_fee=str(funds * 0.0005),
                     trades=[{"volume": str(vol), "funds": str(funds), "price": str(funds / vol if vol else 0)}])
        self.orders[u] = o
        if self.drop_next:
            self.drop_next = False
            raise UpbitClientError("최대 재시도 횟수 초과: 타임아웃")
        return o


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


def make_bot(cfg=CFG, client=None, llm=None, clock=None):
    d = tempfile.mkdtemp()
    client = client or FakeUpbit()
    sent = []
    bot = DtBot(cfg, client, executor=DtExecutor(client, cfg.live, sleep=lambda s: None),
                llm=llm or (lambda *a: ('{}', {"route": "cli"})), now=clock or Clock(), notify=sent.append,
                state_dir=d, log_dir=d, async_llm=False)
    bot.sent = sent
    bot.price = client.price
    return bot


def ctx(**kw):
    base = dict(stop_file=False, weekly_stop=False, halt_reason="", day_pnl_krw=0, buys_today=0, holding=False,
                cooldown_left_min=0, confidence=0.8, spread_bp=1.0, warning=False, discretionary=False,
                size_pct=50, krw_available=2_000_000)
    base.update(kw)
    return BuyContext(**base)


# ---------------------------------------------------------------- 테스트
def check_buy_steps():
    ok, why, amt = guard_buy(CFG, ctx())
    assert ok and amt == 500_000, (why, amt)
    cases = {"1": dict(stop_file=True), "2": dict(weekly_stop=True), "3": dict(day_pnl_krw=-30_000),
             "4": dict(buys_today=6), "5": dict(holding=True), "6": dict(cooldown_left_min=5),
             "7": dict(confidence=0.5), "8": dict(spread_bp=6), "9": dict(discretionary=True, size_pct=31),
             "10": dict(krw_available=5_000)}
    for step, kw in cases.items():
        ok, why, _ = guard_buy(CFG, ctx(**kw))
        assert not ok and why.startswith(step + " "), (step, why)
    assert not guard_buy(CFG, ctx(warning=True))[0]
    assert guard_buy(replace(CFG, max_buys_per_day=0), ctx(buys_today=99))[0]   # 0 = 제한 없음
    assert guard_buy(CFG, ctx(krw_available=100_000))[2] == 99_500   # 가용 × 0.995


def check_clamp():
    d, notes = parse_decision(CFG, json.dumps({
        "regime": "상승추세", "plan": {"strategy": "rsi_momo_1h", "params": {"entry": 90, "exit": 10, "x": 1},
                                    "size_pct": 150, "stop_loss_pct": 9}, "action": "jump",
        "confidence": 3, "next_check_min": 60}))
    assert d["plan"]["params"] == {"entry": 75, "exit": 40, "max_hold": 48}, d
    assert d["plan"]["size_pct"] == 100 and d["plan"]["stop_loss_pct"] == 5.0
    assert d["action"] == "hold" and d["confidence"] == 1 and d["next_check_min"] == 15
    assert any("x 무시" in n for n in notes)
    d, _ = parse_decision(CFG, '{"plan":{"strategy":"mystery"}}')
    assert d["plan"]["strategy"] == "keep"


def check_plan_min_hold():
    clock = Clock()
    bot = make_bot(clock=clock)
    dec = lambda sid: parse_decision(CFG, json.dumps({"plan": {"strategy": sid}, "action": "hold", "confidence": 0.7}))[0]
    bot.apply(dec("rsi_momo_4h"))
    assert bot.s["plan"]["strategy"] == "rsi_momo_4h"
    clock.t += timedelta(minutes=30)
    rej = bot.apply(dec("breakout_vol_1h"))
    assert bot.s["plan"]["strategy"] == "rsi_momo_4h" and "최소 유지" in rej[0], rej
    bot.apply(dec("none"))                       # 현금 전환은 언제나
    assert bot.s["plan"]["strategy"] == "none"
    clock.t += timedelta(minutes=61)
    bot.apply(dec("breakout_vol_1h"))
    assert bot.s["plan"]["strategy"] == "breakout_vol_1h"


def _hold_pos(bot, strategy="rsi_momo_1h", params=None, entry=100_000_000, vol=0.005):
    bot.s["position"] = {"volume": vol, "entry_price": entry, "cost_krw": vol * entry * 1.0005,
                         "entry_time": bot.now().isoformat(), "strategy": strategy,
                         "params": params or {"entry": 70, "exit": 50, "max_hold": 48}, "source": "플랜",
                         "stop_pct": 3.0, "stop_price": entry * 0.97}
    bot.c.btc = vol


def check_handover():
    bot = make_bot()
    bot.hist[1440] = to_df(candles(1440, 200, trend=0.003))[:-1]      # 상승: 종가 > SMA
    bot.hist[60] = to_df(candles(60, 200, trend=-0.003))[:-1]         # 하락: RSI < exit
    _hold_pos(bot)
    bot.apply(parse_decision(CFG, '{"plan":{"strategy":"sma_trend_daily","params":{"period":30}},"confidence":0.7}')[0])
    p = bot.s["position"]
    assert p["strategy"] == "sma_trend_daily" and p["stop_price"] is None, p
    bot2 = make_bot()
    bot2.hist = bot.hist
    _hold_pos(bot2, strategy="vb_daily", params={"k": 0.5, "ma_n": 5})
    bot2.apply(parse_decision(CFG, '{"plan":{"strategy":"rsi_momo_1h"},"confidence":0.7}')[0])
    assert bot2.s["position"]["strategy"] == "vb_daily"               # 새 전략이 보유 상태 아님 → 기존 규칙 유지
    assert "매도 규칙을 계속" in bot2.sent[-1]


def check_discretionary_cap():
    bot = make_bot()
    rej = bot.apply(parse_decision(CFG, '{"plan":{"strategy":"keep"},"action":"buy_now","action_size_pct":50,"confidence":0.9}')[0])
    assert bot.s["position"] is None and "9 재량" in rej[0], rej
    bot.apply(parse_decision(CFG, '{"plan":{"strategy":"keep"},"action":"buy_now","action_size_pct":30,"confidence":0.9}')[0])
    p = bot.s["position"]
    assert p and p["strategy"] == "discretionary" and abs(p["cost_krw"] - 300_150) < 1, p


def check_kill():
    bot = make_bot()
    _hold_pos(bot, vol=0.01)                     # 원가 약 100만원 = 예산 전부
    bot.price = bot.c.price = 84_000_000         # −16%
    bot.s["position"]["stop_price"] = None
    bot.protect()
    assert bot.s["position"] is None and "킬" in bot.s["halt"], bot.s
    assert not bot.try_buy("rsi_momo_1h", {}, 50, 3, 0.9, "플랜", "t")
    assert "정지 상태" in bot.s["rejections"][-1]["reason"]


def check_shared_pool():
    """코인 봇들이 예산 하나를 같이 쓴다: 다른 코인이 쓴 돈은 빼고 사고, 손실 한도는 합쳐서 본다."""
    bot = make_bot()                              # 예산 100만
    bot.others = lambda: [{"pool_used": 700_000, "pnl_today": 0, "realized_total": 0, "unrealized": 0}]
    assert bot.cash() == 300_000
    bot.try_buy("rsi_momo_1h", {}, 50, 3, 0.9, "플랜", "t")      # 예산 50% = 50만 원이지만 남은 30만 원까지만
    assert bot.s["position"] and bot.s["position"]["cost_krw"] <= 300_000, bot.s["position"]
    bot2 = make_bot()
    bot2.others = lambda: [{"pool_used": 0, "pnl_today": -40_000, "pnl_7d": -40_000, "realized_total": -40_000, "unrealized": 0}]
    assert not bot2.try_buy("rsi_momo_1h", {}, 50, 3, 0.9, "플랜", "t")   # 다른 코인 손실로 하루 한도(3%) 넘음
    assert bot2.s["position"] is None


def check_api_monthly_reset():
    """매달 충전일에 API 누적 비용을 0 으로 (처음 켤 때는 안 지움)."""
    from bot.dt_live import credit_period
    assert (credit_period(datetime(2026, 11, 15, tzinfo=KST), 16), credit_period(datetime(2026, 11, 16, tzinfo=KST), 16),
            credit_period(datetime(2026, 1, 3, tzinfo=KST), 16), credit_period(datetime(2026, 1, 3, tzinfo=KST), 0)) \
        == ("2026-10", "2026-11", "2025-12", "")
    clock = Clock(datetime(2026, 11, 15, 23, 0, tzinfo=KST))
    bot = make_bot(cfg=replace(CFG, api_reset_day=16), clock=clock)
    bot.s["llm"]["api_total_usd"] = 60.0
    bot._llm_day()
    assert bot.s["llm"]["api_total_usd"] == 60.0              # 처음: 그대로
    clock.t = datetime(2026, 11, 16, 0, 1, tzinfo=KST)
    bot._llm_day()
    assert bot.s["llm"]["api_total_usd"] == 0.0 and bot.s["llm"]["api_period"] == "2026-11"


def check_daily_weekly():
    clock = Clock()
    bot = make_bot(clock=clock)
    bot.s["pnl_log"] = [[(NOW - timedelta(hours=1)).isoformat(), -31_000]]
    assert not bot.try_buy("rsi_momo_1h", {}, 50, 3, 0.9, "플랜", "t")
    assert bot.s["rejections"][-1]["reason"].startswith("rsi_momo_1h 매수: 3 당일"), bot.s["rejections"]
    bot.s["pnl_log"] = [[(NOW - timedelta(days=3)).isoformat(), -81_000]]
    bot.check_limits()
    assert bot.s["weekly_stop"]
    clock.t += timedelta(days=1)
    assert not bot.try_buy("rsi_momo_1h", {}, 50, 3, 0.9, "플랜", "t")
    assert "2 주간" in bot.s["rejections"][-1]["reason"]
    bot.handle("/dt_go")
    assert not bot.s["weekly_stop"]


def check_lost_response():
    c = FakeUpbit()
    x = DtExecutor(c, live=True, sleep=lambda s: None)
    c.drop_next = True
    f = x.buy(100_000, c.price)
    assert f and len(c.orders) == 1 and abs(f.funds - 100_000) < 1, (f, c.orders)   # 재주문 없이 identifier 조회
    c2 = FakeUpbit()
    x2 = DtExecutor(c2, live=True, sleep=lambda s: None)
    orig = c2._order
    c2._order = lambda d: (_ for _ in ()).throw(UpbitClientError("최대 재시도 횟수 초과: 연결"))  # 접수 안 됨
    assert x2.buy(100_000, c2.price) is None and len(c2.orders) == 0 and x2.errors == 1
    c2._order = orig


def check_partial_retry():
    c = FakeUpbit()
    c.fill_ratio = [0.5, 1.0]
    x = DtExecutor(c, live=True, sleep=lambda s: None)
    f = x.buy(100_000, c.price)
    assert len(c.orders) == 2 and abs(f.funds - 100_000) < 2, (f.funds, c.orders)
    c = FakeUpbit()
    c.fill_ratio = [0.3, 0.3, 1.0]
    x = DtExecutor(c, live=True, sleep=lambda s: None)
    f = x.sell(0.01, c.price)
    kinds = [o["ord_type"] for o in c.orders.values()]
    assert kinds == ["best", "best", "market"] and abs(f.volume - 0.01) < 1e-8, (kinds, f.volume)


def check_post_only_cancel():
    clock = Clock(NOW.replace(hour=11))
    bot = make_bot(clock=clock)
    bot.s["plan"] = {"strategy": "vwap_revert", "params": {"dev": 1.0}, "size_pct": 50, "stop_loss_pct": 3,
                     "confidence": 0.8, "set_at": clock.t.isoformat(), "reason": ""}
    bot.vwap = 101_000_000
    bot.run_vwap()
    pend = bot.s["pending"]
    assert pend and pend["side"] == "bid" and pend["price"] == 99_990_000, pend
    o = bot.c.orders[pend["uuid"]]
    assert o["ord_type"] == "limit"
    clock.t += timedelta(minutes=31)
    bot.run_vwap()
    assert bot.s["pending"] is None and o["state"] == "cancel" and bot.s["position"] is None


def check_llm_failure():
    def boom(*a):
        raise RuntimeError("timeout")
    bot = make_bot(llm=boom)
    bot.s["plan"]["strategy"] = "rsi_momo_4h"
    r = bot.decide(["t"])
    assert r["decision"]["action"] == "hold" and bot.s["plan"]["strategy"] == "rsi_momo_4h", r["decision"]
    bot.llm = lambda *a: ("이건 JSON 아님", {"route": "cli", "in": 10, "out": 3})
    r = bot.decide(["t"])
    assert "parse_error" in r["notes"] and bot.s["plan"]["strategy"] == "rsi_momo_4h"


def check_llm_cap():
    calls = []
    bot = make_bot(cfg=replace(CFG, max_deep_calls_per_day=2),
                   llm=lambda *a: (calls.append(1), ('{"plan":{"strategy":"keep"},"action":"hold"}', {"route": "cli"}))[1])
    assert bot.decide(["a"]) and bot.decide(["b"])
    assert bot.decide(["c"]) is None and len(calls) == 2
    assert any("하루 상한" in m for m in bot.sent)
    # 보유 중 urgent_sell 은 깊은 판단 상한을 넘어도 허용
    bot.llm = lambda p, *a: (calls.append(p), ('{"verdict":"escalate","urgent_sell":true,"reason":"급락"}'
                                              if p == "dt_quick.md" else '{"plan":{"strategy":"keep"},"action":"hold"}',
                                              {"route": "cli"}))[1]
    _hold_pos(bot)
    bot.decide(["예약 점검"], kind="quick")
    assert calls[-2:] == ["dt_quick.md", "dt_system.md"], calls


def check_lessons_filter():
    md = ("- 11/10 rsi_momo_4h 하락추세 진입 손실 −1.2%\n- 손실 한도를 늘리자\n- 비중을 키워서 만회\n"
          "- 하루 매수 횟수 한도 완화 필요\n- 횡보장에는 none 이 나았다\n" + "\n".join(f"- 줄 {i}" for i in range(40)))
    out, dropped = filter_lessons(md)
    assert len(dropped) == 3 and "늘리자" not in out and "none 이 나았다" in out
    assert len(out.splitlines()) == 30


def check_stop_and_cooldown():
    bot = make_bot()
    _hold_pos(bot)
    bot.price = bot.c.price = 96_900_000
    bot.protect()
    assert bot.s["position"] is None and bot.s["cooldown_until"]
    assert not bot.try_buy("rsi_momo_1h", {}, 50, 3, 0.9, "플랜", "t")
    assert "6 쿨다운" in bot.s["rejections"][-1]["reason"]
    st = json.load(open(bot.state_path)) if bot.save() is None else None
    assert st["btc_volume"] == 0 and st["reserve_krw"] < 1_000_000
    paper = make_bot(cfg=replace(CFG, live=False))
    _hold_pos(paper)
    paper.save()
    st = json.load(open(paper.state_path))
    assert st["reserve_krw"] == 0 and st["btc_volume"] == 0      # 가상 체결은 50일선 봇 몫을 줄이지 않는다


def check_trend_bot_excludes_agent_share():
    """50일선 봇은 에이전트 몫(현금·BTC)을 빼고 매매한다 — 안 빼면 전량 매도가 에이전트 BTC 까지 판다."""
    from bot.strategies import SmaTrendStrategy
    from bot.trader import Trader
    c = FakeUpbit(krw=1_000_000, btc=0.003)
    c.dry_run = False
    c.get_balance = lambda cur: c.krw if cur == "KRW" else c.btc
    t = Trader(SmaTrendStrategy(50), client=c, market="KRW-BTC")
    t._dt_share = lambda: (300_000.0, 0.002)
    assert t._get_krw_balance() == 700_000 and abs(t._get_coin_balance() - 0.001) < 1e-12
    t._dt_share = lambda: (2_000_000.0, 0.01)
    assert t._get_krw_balance() == 0 and t._get_coin_balance() == 0


def check_multi_coin_share_and_tick():
    """코인별 AI 봇: 원화는 모든 봇 몫을 합쳐 빼고, 코인 수량은 같은 마켓 몫만. 호가 단위는 코인마다 다르다."""
    import os
    from bot import notifier
    from bot.strategies import SmaTrendStrategy
    from bot.trader import Trader
    root = tempfile.mkdtemp()
    for sub, s in (("", {"reserve_krw": 200_000, "btc_volume": 0.002}),          # 예전 파일: market 없음 = BTC
                   ("dt_ETH", {"reserve_krw": 100_000, "btc_volume": 0.05, "market": "KRW-ETH"})):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
        json.dump(s, open(os.path.join(root, sub, "dt_live.json"), "w"))
    orig = notifier.dt_dirs
    notifier.dt_dirs = lambda: [root, os.path.join(root, "dt_ETH")]
    try:
        c = FakeUpbit(krw=1_000_000, btc=0.003); c.dry_run = False
        assert Trader(SmaTrendStrategy(50), client=c, market="KRW-BTC")._dt_share() == (300_000.0, 0.002)
        assert Trader(SmaTrendStrategy(50), client=c, market="KRW-ETH")._dt_share() == (300_000.0, 0.05)
        assert Trader(SmaTrendStrategy(50), client=c, market="KRW-XRP")._dt_share() == (300_000.0, 0.0)
        # 공용 예산: 92만 원을 같이 쓰고 BTC 봇이 40만, ETH 봇이 10만 원어치 들고 있으면 남은 42만 원만 뺀다
        json.dump({"pool_krw": 920_000, "pool_used": 400_000, "btc_volume": 0.004},
                  open(os.path.join(root, "dt_live.json"), "w"))
        json.dump({"pool_krw": 920_000, "pool_used": 100_000, "btc_volume": 0.03, "market": "KRW-ETH"},
                  open(os.path.join(root, "dt_ETH", "dt_live.json"), "w"))
        assert Trader(SmaTrendStrategy(50), client=c, market="KRW-BTC")._dt_share() == (420_000.0, 0.004)
    finally:
        notifier.dt_dirs = orig
    x = DtExecutor(FakeUpbit(), live=False)
    x.tick = 100                                   # SOL
    assert (x.tick_floor(113_549), x.tick_ceil(113_501)) == (113_500, 113_600)
    x.tick = 1                                     # XRP
    assert f"{x.tick_ceil(1428.2):.10g}" == "1429"


def check_router_fallback():
    """경로 1 rate_limit → 바로 경로 2, 60분 뒤 경로 1 복귀. 시간 초과·형식 오류는 2회 연속이면 전환."""
    from bot.dt_agent import LlmError, LlmRouter
    clock, sent, log = Clock(), [], []

    def cli(cfg, p, les, user, model):
        log.append("cli")
        if cli.fail:
            raise LlmError(*cli.fail)
        return "{}", {"route": "cli"}
    cli.fail = ("claude -p 오류: error_during_execution 429 rate_limit", True)
    api = lambda cfg, p, les, user, model: (log.append("api"), ("{}", {"route": "api"}))[1]
    r = LlmRouter(CFG, notify=sent.append, now=clock, cli=cli, api=api)
    assert r("dt_quick.md", "", "{}", "m")[1]["route"] == "api" and log == ["cli", "api"]
    assert "전환" in sent[-1] and r.limit_errors == 1
    clock.t += timedelta(minutes=30)
    r("dt_quick.md", "", "{}", "m")
    assert log[-1] == "api" and log.count("cli") == 1            # 1시간 동안은 경로 2
    cli.fail = None
    clock.t += timedelta(minutes=31)
    assert r("dt_quick.md", "", "{}", "m")[1]["route"] == "cli" and "복귀" in sent[-1]
    cli.fail = ("claude -p 시간 초과 120초", False)
    try:
        r("dt_quick.md", "", "{}", "m"); raise AssertionError("1회 시간 초과는 그대로 실패해야 함")
    except LlmError:
        pass
    assert r("dt_quick.md", "", "{}", "m")[1]["route"] == "api"   # 2회 연속 → 전환
    r2 = LlmRouter(replace(CFG, llm_fallback="none"), now=clock, cli=cli, api=api)
    cli.fail = ("rate_limit", True)
    try:
        r2("dt_quick.md", "", "{}", "m"); raise AssertionError("fallback=none 이면 예외")
    except LlmError:
        pass



def check_router_api_first_budget():
    """API 먼저: 누적 비용이 예산에 닿으면 구독. API 오류(키 없음 등)면 60분 구독 후 API 재시도."""
    from bot.dt_agent import LlmError, LlmRouter
    clock, sent, log, spent = Clock(), [], [], [0.0]

    def api(cfg, p, les, user, model):
        log.append("api")
        if api.fail:
            raise LlmError(api.fail)
        return "{}", {"route": "api"}
    api.fail = None
    cli = lambda cfg, p, les, user, model: (log.append("cli"), ("{}", {"route": "cli"}))[1]
    cfg = replace(CFG, llm_primary="api", llm_fallback="claude_cli", api_total_budget_usd=100)
    r = LlmRouter(cfg, notify=sent.append, now=clock, cli=cli, api=api, spent=lambda: spent[0])
    assert r("dt_quick.md", "", "{}", "m")[1]["route"] == "api" and r.route_now() == "api"
    api.fail = "ANTHROPIC_API_KEY 없음"
    assert r("dt_quick.md", "", "{}", "m")[1]["route"] == "cli" and log[-2:] == ["api", "cli"]
    assert r.route_now() == "claude_cli" and "API → 구독" in sent[-1]
    api.fail = None
    clock.t += timedelta(minutes=61)
    assert r("dt_quick.md", "", "{}", "m")[1]["route"] == "api" and "복귀" in sent[-1]
    spent[0] = 100.0
    n = len(log)
    assert r("dt_quick.md", "", "{}", "m")[1]["route"] == "cli" and log[n:] == ["cli"]   # API 를 부르지도 않음
    assert "$100" in sent[-1] and r.route_now() == "claude_cli"
    r("dt_quick.md", "", "{}", "m")
    assert sum("$100" in x for x in sent) == 1                                          # 알림은 한 번만


def check_cli_env_and_flags():
    """claude -p: 빈 폴더, 도구·MCP·설정 끔, ANTHROPIC_API_KEY 제거(구독 대신 API 과금 방지), structured_output 읽기."""
    import os
    from bot.dt_agent import call_cli
    seen = {}

    class R:
        returncode, stderr = 0, ""
        stdout = "\n".join(json.dumps(x) for x in [   # stream-json: 이벤트가 줄마다
            {"type": "system", "subtype": "init"},
            {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
                "five_hour": {"utilization": 0.18, "resetsAt": 1794339600, "status": "allowed"},
                "seven_day": {"utilization": 0.2, "resetsAt": 1794459600, "status": "allowed"}}}},
            {"type": "result", "subtype": "success", "is_error": False, "structured_output": {"verdict": "keep"},
             "usage": {"input_tokens": 5, "output_tokens": 7, "cache_read_input_tokens": 3000},
             "total_cost_usd": 0.001, "duration_ms": 900}])

    def run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return R()
    os.environ["ANTHROPIC_API_KEY"] = "sk-test-not-real"
    try:
        wd = tempfile.mkdtemp()
        text, meta = call_cli(replace(CFG, cli_workdir=wd), "dt_quick.md", "교훈", "{}", "claude-haiku-5-5", run=run)
    finally:
        del os.environ["ANTHROPIC_API_KEY"]
    c = seen["cmd"]
    assert "ANTHROPIC_API_KEY" not in seen["env"] and seen["cwd"] == wd
    assert c[c.index("--tools") + 1] == "" and "--strict-mcp-config" in c and "--bare" not in c
    assert c[c.index("--model") + 1] == "claude-haiku-5-5" and seen["input"].startswith("[교훈 메모]")
    assert json.loads(text) == {"verdict": "keep"} and meta["cache_read"] == 3000
    assert meta["limits"]["five_hour"]["pct"] == 18.0 and meta["limits"]["seven_day"]["resets_at"] == 1794459600
    R.stdout = json.dumps({"subtype": "error", "is_error": True, "api_error_status": 429, "result": "rate limit"})
    from bot.dt_agent import LlmError
    try:
        call_cli(replace(CFG, cli_workdir=wd), "dt_quick.md", "", "{}", "m", run=run); raise AssertionError
    except LlmError as e:
        assert e.hard


def check_quick_escalate_and_intervals():
    prompts = []
    verdict = {"v": "keep"}

    def llm(p, les, user, model):
        prompts.append((p, model))
        if p == "dt_quick.md":
            return json.dumps({"verdict": verdict["v"], "urgent_sell": False, "reason": "r"}), {"route": "cli"}
        return '{"plan":{"strategy":"keep"},"action":"hold","next_check_min":3}', {"route": "cli"}
    clock = Clock()
    bot = make_bot(llm=llm, clock=clock)
    bot.decide(["예약 점검"], kind="quick")
    assert prompts == [("dt_quick.md", CFG.quick_model)]                       # keep → 깊은 판단 없음
    assert _next_min(bot, clock) == 5                                          # 현금·조용 → 5분
    verdict["v"] = "escalate"
    bot.decide(["예약 점검"], kind="quick")
    assert prompts[-1] == ("dt_system.md", CFG.deep_model)                     # escalate → 깊은 판단
    assert _next_min(bot, clock) == 3                                          # 깊은 판단 next_check_min 이 더 짧으면 그것
    _hold_pos(bot)
    assert bot.check_interval_min() == 2                                       # 보유 → 2분
    bot.s["last_signal_at"] = clock.t.isoformat()
    assert bot.check_interval_min() == 1                                       # 신호 후 30분 → 1분
    bot.s["llm"]["routes"]["api"] = {"calls": 9, "cache_hits": 9, "cost_usd": 3.5}
    assert bot.check_interval_min() == 2                                       # API 하루 비용 초과 → 2배
    # 이전 호출이 안 끝났으면 건너뛴다
    n = len(prompts)
    bot.job = {"future": None}
    bot.start_llm("deep", ["체결(매수)"])
    assert len(prompts) == n
    # 빠른 점검은 주문하지 않는다: urgent_sell 이어도 판단은 깊은 판단이 한다
    bot.job = None
    verdict["v"] = "keep"
    bot.llm = lambda p, *a: ('{"verdict":"keep","urgent_sell":false,"reason":"x","action":"sell_now"}', {"route": "cli"})
    vol = bot.s["position"]["volume"]
    bot.decide(["예약 점검"], kind="quick")
    assert bot.s["position"]["volume"] == vol


def _next_min(bot, clock):
    from bot.dt_live import _dt
    return round((_dt(bot.s["next_check_at"]) - clock.t).total_seconds() / 60)


def check_quick_cap():
    bot = make_bot(cfg=replace(CFG, max_quick_calls_per_day=1),
                   llm=lambda *a: ('{"verdict":"keep","urgent_sell":false,"reason":""}', {"route": "cli"}))
    assert bot.decide(["예약 점검"], kind="quick")
    assert bot.decide(["예약 점검"], kind="quick") is None and any("빠른 점검이 하루 상한" in m for m in bot.sent)


def check_no_double_scheduled_check():
    """호출이 도는 동안 틱이 지나도, 끝난 뒤 예약 점검이 바로 한 번 더 불리지 않는다."""
    from concurrent.futures import Future
    clock = Clock()
    calls = []
    bot = make_bot(clock=clock, llm=lambda p, *a: (calls.append(p),
                   ('{"verdict":"keep","urgent_sell":false,"reason":"r"}', {"route": "cli"}))[1])
    bot.s["next_check_at"] = (clock.t - timedelta(minutes=1)).isoformat()
    bot.s["last_llm_price"] = bot.c.price
    bot.check_bars = bot.run_intraday = bot.reconcile = lambda: None
    fut = Future()
    bot.job = {"future": fut, "wake": ["예약 점검"], "snapshot": {}, "user_chars": 0, "price": bot.c.price,
               "started": clock.t}
    for _ in range(3):                      # 호출 중 틱 3번
        clock.t += timedelta(seconds=10)
        bot.tick()
    fut.set_result({"kind": "quick", "calls": [], "quick": {"verdict": "keep", "urgent_sell": False, "reason": ""}})
    clock.t += timedelta(seconds=10)
    bot.tick()                              # 여기서 결과 적용, 다음 점검은 5분 뒤
    assert bot.job is None and calls == [], (bot.job, calls)



def check_easy_words():
    """쉬운 설명(easy)은 판단·점검 결과에 남고, 알림용 문장은 지표 이름이 쉬운 말로 바뀐다."""
    from bot.config import load_dt_config
    from bot.dt_agent import parse_decision, parse_quick
    from bot.strategies import ko_sub
    d, _ = parse_decision(load_dt_config(), '{"plan":{"strategy":"none"},"action":"hold","reason":"RSI 30","easy":"값이 계속 내려가서 기다려요."}')
    assert d["easy"] == "값이 계속 내려가서 기다려요." and d["reason"] == "RSI 30"
    assert parse_quick('{"verdict":"keep","easy":"그대로 둬요."}')["easy"] == "그대로 둬요."
    assert parse_decision(load_dt_config(), "깨진 답")[0]["easy"]
    t = ko_sub("rsi_momo_4h: 240m RSI 32.9, 4h mdi > pdi, 세션 VWAP -1%, 99.25M")
    assert t == "상승세 올라타기(4시간): 240분봉 과열 지수 32.9, 4시간봉 하락 힘 > 상승 힘, 오늘 평균 거래가 -1%, 99.25M", t


def main():
    for name, fn in list(globals().items()):
        if name.startswith("check_"):
            fn()
            print("ok", name)
    print("모든 btc-agent 테스트 통과")


if __name__ == "__main__":
    main()
