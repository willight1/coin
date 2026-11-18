"""하이퍼리퀴드 추세 봇 검사 (네트워크 없음). ./venv/bin/python test_hl_agent.py"""

import json
import os
import tempfile

import numpy as np
import pandas as pd

from bot import hl_live, hl_signals as sig
from bot.config import HlConfig
from bot.hl_client import TAKER_FEE


hl_live.hl_ai.shadow_async = lambda *a, **k: None          # 검사에서는 AI 를 부르지 않는다
hl_live.PAPER_STATE = os.path.join(tempfile.gettempdir(), "hl_no_paper.json")   # 실제 연습용 봇 상태를 읽지 않게
hl_live.DESK = os.path.join(tempfile.gettempdir(), "hl_test_desk.jsonl")        # 검사가 실제 점검 기록(logs/hl_desk.jsonl)에 쓰지 않게
hl_live.CHAT = os.path.join(tempfile.gettempdir(), "hl_test_chat.jsonl")        # 검사 알림이 실제 대화 기록(logs/hl_chat.jsonl)에 섞이지 않게
hl_live.INBOX = os.path.join(tempfile.gettempdir(), "hl_test_no_inbox.txt")     # 검사가 실제 명령함(state/hl_commands.txt)을 비우지 않게


def fake_ohlc(n, start=100.0, drift=0.0, seed=0):
    r = np.random.default_rng(seed).normal(drift, 0.01, n)
    c = start * np.cumprod(1 + r)
    df = pd.DataFrame({"close": c}, index=pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC"))
    df["open"] = df["close"].shift(1).fillna(start); df["high"] = df[["open", "close"]].max(axis=1) * 1.002
    df["low"] = df[["open", "close"]].min(axis=1) * 0.998; df["volume"] = 1.0
    return df


def check_signals():
    sig.SIGNALS_PATH = "/nonexistent/hl_signals.json"          # 실제 투표단 파일 말고 기본 8개로 검사
    sig._cache.update(mtime=None, chosen=sig.DEFAULT_CHOSEN)
    up = fake_ohlc(600, drift=0.004); dn = fake_ohlc(600, drift=-0.004)
    e_up, v_up = sig.vote(up, up); e_dn, v_dn = sig.vote(dn, dn)
    assert len(v_up) == 8 and -1 <= e_up <= 1 and -1 <= e_dn <= 1
    assert e_up > 0.5 and e_dn < -0.5, (e_up, e_dn)                         # 꾸준히 오르면 롱, 내리면 숏
    assert all(v in (-1.0, 0.0, 1.0) for v in v_up.values())
    short = fake_ohlc(30)
    e, v = sig.vote(short, short)
    assert all(v[k] == 0 for k in ("일봉 이동평균50 위/아래", "일봉 이평50 + ADX>25", "일봉 이동평균100 위/아래", "4h 이동평균200 위/아래", "4h 90봉 모멘텀 부호")), "이동평균 워밍업 부족이면 0표"
    calm = fake_ohlc(600, drift=0); calm["close"] = 100 + np.arange(600) * 0.001     # 거의 안 움직임
    assert sig.leverage(calm, 0.6, 3.0) == 3.0, "변동성 거의 0 → 상한"
    wild = fake_ohlc(600); wild["close"] = 100 * np.cumprod(1 + np.random.default_rng(1).normal(0, 0.05, 600))
    assert sig.leverage(wild, 0.6, 3.0) < 0.5, "변동성 크면 배수 1 아래"
    assert sig.leverage(wild, 0.6, 50.0, 2.0) == 2.0, "하한 2 면 거친 장에서 2배"
    print("신호·배수 OK", {k: v for k, v in v_up.items()})


class FakeClient:
    def __init__(self): self.meta = [{"name": "BTC", "szDecimals": 5}, {"name": "ETH", "szDecimals": 4}]; self.live = False
    def sz_decimals(self, coin): return 5 if coin == "BTC" else 4
    def max_leverage(self, coin): return 40.0


def check_paper_fills():
    with tempfile.TemporaryDirectory() as d:
        hl_live.STATE, hl_live.STOP_FILE = os.path.join(d, "s.json"), os.path.join(d, "STOP")
        hl_live.TRADES, hl_live.EQUITY = os.path.join(d, "t.csv"), os.path.join(d, "e.csv")
        hl_live.notifier.notify = lambda t: True
        hl_live.HlBot.reselect = lambda self, reason: None            # 실제 재선정 프로세스를 띄우지 않게
        bot = hl_live.HlBot(HlConfig(budget_usd=1000), FakeClient())
        bot.marks = {"BTC": 100.0, "ETH": 10.0}
        bot.trade("BTC", 2.0, 100.0, "열기")                                # 롱 2개 $200
        assert bot.s["pos"]["BTC"] == {"size": 2.0, "entry": 100.0}
        assert abs(bot.s["cash_usd"] - (1000 - 200 * TAKER_FEE)) < 1e-9
        bot.marks["BTC"] = 110.0
        assert abs(bot.equity() - (1000 - 200 * TAKER_FEE + 20)) < 1e-9       # 미실현 +20
        bot.trade("BTC", -3.0, 110.0, "뒤집기")                              # 2개 청산(+20 실현) + 숏 1개
        assert bot.s["pos"]["BTC"] == {"size": -1.0, "entry": 110.0}
        assert abs(bot.s["cash_usd"] - (1000 - 200 * TAKER_FEE + 20 - 330 * TAKER_FEE)) < 1e-9
        bot.trade("BTC", -1.0, 100.0, "숏 추가")                              # 평균 단가 105
        assert bot.s["pos"]["BTC"]["size"] == -2.0 and abs(bot.s["pos"]["BTC"]["entry"] - 105.0) < 1e-9
        bot.marks["BTC"] = 90.0
        bot.flatten("정리")                                                   # 숏 2개 @105 → 90: +30
        assert "BTC" not in bot.s["pos"]
        bot.save()
        st = json.load(open(hl_live.STATE)); assert st["equity_usd"] == bot.equity() and st["live"] is False
        # 킬 드로다운: 평가가 40% 아래로 → 정지 파일 + 전량 청산
        bot.s["start_usd"], bot.s["cash_usd"] = 1000.0, 300.0
        bot.s["pos"] = {"ETH": {"size": 5.0, "entry": 10.0}}
        class C(FakeClient):
            def ctx(self): return {"BTC": {"mid": 90.0, "funding": 0.0}, "ETH": {"mid": 10.0, "funding": 0.0}}
            def candles(self, *a): raise AssertionError("정지 상태에서는 신호를 보지 않아도 된다")
        bot.c = C(); bot.s["last_bar"] = 10 ** 12; bot.s["last_funding_hour"] = 10 ** 12
        bot.tick()
        assert bot.s["halt"] and os.path.exists(hl_live.STOP_FILE) and not bot.s["pos"]
        print("가상 체결·킬 OK")


def check_funding():
    with tempfile.TemporaryDirectory() as d:
        hl_live.STATE, hl_live.STOP_FILE = os.path.join(d, "s.json"), os.path.join(d, "STOP")
        hl_live.EQUITY = os.path.join(d, "e.csv"); hl_live.notifier.notify = lambda t: True
        bot = hl_live.HlBot(HlConfig(budget_usd=1000), FakeClient())
        bot.s["pos"] = {"BTC": {"size": -1.0, "entry": 100.0}}             # 숏 1개
        class C(FakeClient):
            def ctx(self): return {"BTC": {"mid": 100.0, "funding": 0.0001}, "ETH": {"mid": 10.0, "funding": 0.0}}
        bot.c = C(); bot.s["last_bar"] = 10 ** 12; bot.s["last_funding_hour"] = 1
        bot.tick()
        assert abs(bot.s["cash_usd"] - (1000 + 100 * 0.0001)) < 1e-9, "양수 펀딩이면 숏이 받는다"
        assert abs(bot.s["funding"]["total"] - 100 * 0.0001) < 1e-12 and abs(bot.s["funding"]["day"] - 0.01) < 1e-12, "펀딩 기록"
        print("펀딩 OK")


def check_band():
    with tempfile.TemporaryDirectory() as d:
        hl_live.STATE, hl_live.STOP_FILE = os.path.join(d, "s.json"), os.path.join(d, "STOP")
        hl_live.DESK = os.path.join(d, "desk.jsonl")
        hl_live.TRADES, hl_live.EQUITY, hl_live.VOTES = os.path.join(d, "t.csv"), os.path.join(d, "e.csv"), os.path.join(d, "v.csv")
        hl_live.notifier.notify = lambda t: True; hl_live.HlBot.reselect = lambda self, reason: None
        class C(FakeClient):
            def candles(self, coin, iv, n):
                df = fake_ohlc(50); df.iloc[-1, df.columns.get_loc("close")] = 100.0; return df   # 이익 보호가 끼지 않게 종가 고정
        bot = hl_live.HlBot(HlConfig(budget_usd=1000, coins=("BTC",)), C())
        bot.marks = {"BTC": 100.0}
        ov, ol = sig.vote, sig.leverage
        sig.leverage = lambda *a, **k: 1.0
        try:
            sig.vote = lambda *a, **k: (-1.0, {"x": -1.0})
            bot.rebalance(); assert bot.s["pos"]["BTC"]["size"] == -10.0          # 처음 진입: 목표 −10개
            sig.vote = lambda *a, **k: (-0.8, {"x": -0.8})
            bot.rebalance(); assert bot.s["pos"]["BTC"]["size"] == -10.0, "차이 20% < 완충 30% → 그대로"
            sig.vote = lambda *a, **k: (-0.6, {"x": -0.6})
            bot.rebalance(); assert abs(bot.s["pos"]["BTC"]["size"] + 6.0) < 0.01, "차이 40% → 목표까지 맞춤"
            sig.vote = lambda *a, **k: (0.6, {"x": 0.6})
            bot.rebalance(); assert bot.s["pos"]["BTC"]["size"] > 0, "방향이 바뀌면 완충 없이 뒤집기"
            desk, hl_live.STATE, hl_live.DESK = hl_live.DESK, os.path.join(d, "s2.json"), os.path.join(d, "desk2.jsonl")     # 멈춘 코인(/hl_hold) 몫은 나머지 코인이 쓴다
            sig.vote = lambda *a, **k: (-1.0, {"x": -1.0})
            b2 = hl_live.HlBot(HlConfig(budget_usd=1000), C()); b2.marks = {"BTC": 100.0, "ETH": 100.0}
            b2.rebalance(); assert b2.s["pos"]["BTC"]["size"] == b2.s["pos"]["ETH"]["size"] == -5.0, "두 코인이 계좌를 반씩"
            b2.s["manual"] = ["ETH"]; b2.s["hold"] = ["ETH"]
            b2.rebalance(); assert -10.0 <= b2.s["pos"]["BTC"]["size"] < -9.9 and b2.s["pos"]["ETH"]["size"] == -5.0, b2.s["pos"]
            hl_live.DESK = desk
        finally:
            sig.vote, sig.leverage = ov, ol
        rows = [json.loads(x) for x in open(hl_live.DESK)]               # 점검 기록: 한 번에 한 줄, 결과 문구
        got = [r["coins"]["BTC"]["result"] for r in rows]
        assert got[0].startswith("숏 진입 10") and "완충" in got[1] and got[2].startswith("숏 줄임") and got[3].startswith("롱으로 뒤집기"), got
        assert rows[0]["coins"]["BTC"]["desk"] == {"추세": -1.0} and rows[0]["coins"]["BTC"]["mode"] == "정상" and rows[0]["coins"]["BTC"]["rule"] == -1.0
        print("완충·점검 기록 OK")
        hl_live.DESK = os.path.join(tempfile.gettempdir(), "hl_test_desk.jsonl")


def check_half():
    with tempfile.TemporaryDirectory() as d:
        hl_live.STATE, hl_live.STOP_FILE, hl_live.HALF_FILE, hl_live.WAIT_FILE = os.path.join(d, "s.json"), os.path.join(d, "STOP"), os.path.join(d, "HALF"), os.path.join(d, "WAIT")
        hl_live.TRADES, hl_live.EQUITY, hl_live.VOTES = os.path.join(d, "t.csv"), os.path.join(d, "e.csv"), os.path.join(d, "v.csv")
        hl_live.notifier.notify = lambda t: True; hl_live.HlBot.reselect = lambda self, reason: None
        class C(FakeClient):
            def ctx(self): return {"BTC": {"mid": 100.0, "funding": 0.0}}
            def candles(self, coin, iv, n):
                df = fake_ohlc(50); df.iloc[-1, df.columns.get_loc("close")] = 100.0; return df
        bot = hl_live.HlBot(HlConfig(budget_usd=1000, coins=("BTC",)), C())
        bot.marks = {"BTC": 100.0}
        ov, ol = sig.vote, sig.leverage
        sig.leverage = lambda *a, **k: 1.0
        try:
            sig.vote = lambda *a, **k: (-1.0, {"x": -1.0})
            bot.rebalance(); assert bot.s["pos"]["BTC"]["size"] == -10.0
            bot.s["last_bar"] = 10 ** 12; bot.s["last_funding_hour"] = 10 ** 12
            open(hl_live.HALF_FILE, "w").close()
            bot.tick(); assert abs(bot.s["pos"]["BTC"]["size"] + 5.0) < 0.01 and bot.s["half"] == {"BTC": -1}, "절반으로 줄임"
            assert not os.path.exists(hl_live.HALF_FILE)
            bot.rebalance(); assert abs(bot.s["pos"]["BTC"]["size"] + 5.0) < 0.01, "같은 방향이면 절반 유지"
            sig.vote = lambda *a, **k: (0.8, {"x": 0.8})
            bot.rebalance(); assert bot.s["half"] == {} and bot.s["pos"]["BTC"]["size"] > 7.9, "새 추세(롱)는 전액"
            open(hl_live.WAIT_FILE, "w").close()
            bot.tick(); assert not bot.s["pos"].get("BTC", {}).get("size") and bot.s["half"] == {"BTC": 1}, "대기: 전부 정리"
            bot.rebalance(); assert not bot.s["pos"].get("BTC", {}).get("size"), "같은 방향이면 계속 비움"
            sig.vote = lambda *a, **k: (-1.0, {"x": -1.0})
            bot.rebalance(); assert bot.s["half"] == {} and bot.s["pos"]["BTC"]["size"] < -9.9, "새 추세(숏)는 전액"
        finally:
            sig.vote, sig.leverage = ov, ol
        print("절반·대기 모드 OK")


def check_guard():
    with tempfile.TemporaryDirectory() as d:
        hl_live.STATE, hl_live.STOP_FILE = os.path.join(d, "s.json"), os.path.join(d, "STOP")
        hl_live.TRADES, hl_live.EQUITY, hl_live.VOTES = os.path.join(d, "t.csv"), os.path.join(d, "e.csv"), os.path.join(d, "v.csv")
        sent = []; hl_live.notifier.notify = lambda t: sent.append(t) or True; hl_live.HlBot.reselect = lambda self, reason: None
        px = {"v": 100.0}
        class C(FakeClient):
            def candles(self, coin, iv, n):
                df = fake_ohlc(50); df.iloc[-1, df.columns.get_loc("close")] = px["v"]; return df
        bot = hl_live.HlBot(HlConfig(budget_usd=1000, coins=("BTC",)), C())
        ov, ol = sig.vote, sig.leverage
        sig.vote = lambda *a, **k: (-1.0, {"x": -1.0}); sig.leverage = lambda *a, **k: 1.0
        try:
            def step(p):
                px["v"] = p; bot.marks = {"BTC": p}; bot.rebalance(); return bot.s["pos"].get("BTC", {}).get("size", 0.0)
            assert step(100.0) == -10.0                                    # 숏 진입 @100
            assert step(90.0) < -10.0 + 1e-9                               # 10% 이익 → 그대로(평가금액 늘어 조금 늘 수 있음)
            g = bot.s["guard"]["BTC"]; assert abs(g["best"] - 0.10) < 1e-9 and abs(g["line"] - 100 * (1 - (1.10 / 1.05 - 1))) < 0.01
            size = step(93.0)                                              # 최고 이익 10% → 지금 7%: (1.10/1.07)−1 = 2.8% < 5% → 그대로
            assert bot.s["guard"]["BTC"]["cut"] == 1.0 and size <= -10.0
            size = step(95.5)                                              # (1.10/1.045)−1 = 5.3% → 75% 정리
            assert bot.s["guard"]["BTC"]["cut"] == 0.25 and -3.5 < size < -2.0, size
            assert any("이익 보호" in t for t in sent)
            assert step(85.0) == size, "정리 뒤엔 다시 늘리지 않음 (완충 안쪽이면 그대로)"
            sig.vote = lambda *a, **k: (1.0, {"x": 1.0})
            assert step(85.0) > 0 and bot.s["guard"]["BTC"]["cut"] == 1.0, "방향 바뀌면 새 거래"
        finally:
            sig.vote, sig.leverage = ov, ol
        print("이익 보호 OK")


def check_scalp():
    """분봉 단타: 문턱을 넘은 쪽으로 20배, 반대쪽이 넘으면 뒤집기, 손절로 사라지면 그 방향은 쉼, 끄면 정리. 손절 주문은 포지션에 맞춰 건다."""
    with tempfile.TemporaryDirectory() as d:
        hl_live.STATE, hl_live.STOP_FILE = os.path.join(d, "s.json"), os.path.join(d, "STOP")
        hl_live.TRADES = os.path.join(d, "t.csv")
        sent = []; hl_live.notifier.notify = lambda t: sent.append(t) or True
        hl_live.HlBot.reselect = lambda self, reason: (_ for _ in ()).throw(AssertionError("단타 거래로 투표단을 다시 뽑으면 안 된다"))
        class C(FakeClient):
            def candles(self, *a): return None
        share = [0.5, 0.2]
        old = hl_live.hl_tfboard.vote_share; hl_live.hl_tfboard.vote_share = lambda df: tuple(share)
        try:
            bot = hl_live.HlBot(HlConfig(budget_usd=1000, coins=("BTC", "ETH")), C())
            bot.marks = {"BTC": 100.0, "ETH": 10.0}
            size = lambda: bot.s["pos"].get("BTC", {}).get("size", 0.0)
            plan = bot.manual_plan("/hl_long BTC 100 20x")                                     # 배수 고르기: 넣는 돈 100달러 × 20배 = 2,000달러 = 20개
            assert abs(plan[1] - 20.0) < 1e-9 and "× 20배" in plan[2] and "교차 마진" in plan[2], plan
            assert isinstance(bot.manual_plan("/hl_long BTC 10x 20x"), str) and isinstance(bot.manual_plan("/hl_short BTC 100 99x"), str)
            r = bot.resv_plan("/hl_at BTC 120 short 100 20x"); assert r["cmd"] == "/hl_short btc 100 20x" and "$100 × 20배" in r["text"], r     # 예약에도 같은 금액 형식
            r = bot.resv_plan("/hl_at BTC 120 short 2x"); assert r["cmd"] == "/hl_short btc 2x" and "계좌의 2배" in r["text"] and r["up"], r
            assert hl_live.hl_ai.CHAT_CMD.fullmatch("/hl_at btc 65300 short 10x") and hl_live.hl_ai.CHAT_CMD.fullmatch("/hl_at btc 65300 short 100 20x")
            assert hl_live.hl_ai.CHAT_CMD.fullmatch("/hl_short btc 100 20x") and not hl_live.hl_ai.CHAT_CMD.fullmatch("/hl_short btc 100 20")
            bot.handle("/hl_scalp on"); assert "99%" in sent[-1] and "scalp" not in bot.s      # 확인 문구에 측정 결과
            bot.handle("/hl_yes"); assert bot.s["scalp"]["coin"] == "BTC" and "BTC" in bot.s["manual"]
            bot.scalp(60 * 1000 + 5); assert not size()                                        # 60% 미만 → 아직 방향 없음
            share[:] = 0.7, 0.1
            bot.scalp(60 * 1000 + 9); assert not size()                                        # 같은 1분봉 안에서는 한 번만 판단
            bot.scalp(60 * 1001 + 5); assert abs(size() - 200.0) < 1e-6, size()                # 롱 20배 = 1000 × 20 ÷ 100
            share[:] = 0.5, 0.3
            bot.scalp(60 * 1002 + 5); assert abs(size() - 200.0) < 1e-6                        # 문턱 아래로 내려가도 들고 간다
            assert "분봉 단타" in bot.manual_plan("/hl_close BTC")                             # 단타 코인에는 직접 주문을 못 낸다
            share[:] = 0.1, 0.8
            bot.scalp(60 * 1003 + 5); assert size() < -190, size()                             # 숏으로 뒤집기
            bot.s["pos"].pop("BTC")                                                            # 손절 주문이 체결돼 포지션이 사라짐
            bot.scalp(60 * 1004 + 5); assert not size() and bot.s["scalp"]["block"] == -1 and "정리됐어요" in sent[-1]
            bot.scalp(60 * 1005 + 5); assert not size()                                        # 숏 투표가 이어져도 다시 안 잡는다
            share[:] = 0.9, 0.0
            bot.scalp(60 * 1006 + 5); assert size() > 0 and bot.s["scalp"]["block"] == 0       # 반대쪽이 넘으면 다시 시작
            # 손절 주문 맞추기: 롱이면 진입가 2% 아래 매도, 이미 맞으면 그대로, 포지션이 없으면 취소
            calls = []
            class S(C):
                have = []
                def px_round(self, coin, px): return round(px, 1)
                def stops(self, coin): return list(self.have)
                def cancel(self, coin, oid): calls.append(("cancel", oid)); self.have = [o for o in self.have if o["oid"] != oid]
                def stop_order(self, coin, size, px): calls.append(("stop", size, px)); self.have.append({"oid": len(calls), "size": size, "px": px})
            bot.c = S(); bot.s["pos"]["BTC"] = {"size": 2.0, "entry": 100.0}
            bot.sync_stop("BTC"); assert calls == [("stop", -2.0, 98.0)] and bot.s["scalp"]["sl"] == 98.0
            bot.sync_stop("BTC"); assert len(calls) == 1
            bot.s["pos"]["BTC"] = {"size": -1.0, "entry": 100.0}
            bot.sync_stop("BTC"); assert calls[1:] == [("cancel", 1), ("stop", 1.0, 102.0)], calls
            bot.s["pos"].pop("BTC"); bot.sync_stop("BTC"); assert calls[-1] == ("cancel", 3) and not bot.c.have and bot.s["scalp"]["sl"] is None
            # 끄기: 포지션 정리 → 상태 삭제
            bot.c = C(); bot.s["pos"]["BTC"] = {"size": 2.0, "entry": 100.0}
            bot.handle("/hl_scalp off"); bot.handle("/hl_yes"); assert not size() and bot.s["scalp"]["off"]
            bot.scalp(60 * 1007 + 5); assert "scalp" not in bot.s and "BTC" in bot.s["manual"]
            assert hl_live.hl_ai.CHAT_CMD.fullmatch("/hl_scalp on") and hl_live.hl_ai.CHAT_CMD.fullmatch("/hl_scalp off")
        finally:
            hl_live.hl_tfboard.vote_share = old; hl_live.HlBot.reselect = lambda self, reason: None
        print("분봉 단타 OK")


def check_alerts():
    with tempfile.TemporaryDirectory() as d:
        hl_live.STATE, hl_live.STOP_FILE = os.path.join(d, "s.json"), os.path.join(d, "STOP")
        sent = []; hl_live.notifier.notify = lambda t: sent.append(t) or True
        bot = hl_live.HlBot(HlConfig(budget_usd=1000, coins=("BTC",)), FakeClient())
        bot.s["daily_sent"] = hl_live.datetime.now(hl_live.KST).strftime("%Y-%m-%d")      # 아침 요약은 따로
        bot.s["pos"] = {"BTC": {"size": -1.0, "entry": 100.0, "liq": 125.0}}
        t = 1_000_000.0
        bot.marks = {"BTC": 100.0}; bot.alerts(t)                          # 청산까지 25% → 알림 없음
        assert not sent
        bot.marks = {"BTC": 110.0}; bot.alerts(t + 30)                     # 13.6% → 20% 선 알림 1번
        bot.alerts(t + 60)
        assert len([x for x in sent if "청산가" in x]) == 1, sent
        assert json.loads(open(hl_live.CHAT).readlines()[-1])["who"] == "bot"     # 알림은 대시보드 주문 대화창 기록에도 남는다
        bot.marks = {"BTC": 117.0}; bot.alerts(t + 90)                     # 6.8% → 10% 선 알림
        assert len([x for x in sent if "청산가" in x]) == 2
        bot.marks = {"BTC": 95.0}; bot.alerts(t + 120)                     # 31% 로 멀어짐 → 단계 초기화
        assert bot.s["liq_tier"]["BTC"] is None
        sent.clear(); bot.px_hist.clear(); bot.s["liq_tier"] = {}; bot.s["pos"]["BTC"]["liq"] = 1000.0
        for i in range(121):                                               # 1시간에 100 → 104 (+4%)
            bot.marks = {"BTC": 100.0 + 4.0 * i / 120}; bot.alerts(t + 1000 + i * 30)
        moves = [x for x in sent if "1시간" in x]
        assert len(moves) == 1 and "불리" in moves[0], sent              # 숏인데 올랐으니 불리, 쿨다운으로 한 번만
        bot.s["daily_sent"] = "2000-01-01"; bot.alerts(t + 9999)
        assert any("아침 요약" in x for x in sent) == ((hl_live.datetime.now(hl_live.KST).hour, hl_live.datetime.now(hl_live.KST).minute) >= hl_live.DAILY_AT)
        print("알림 OK")


def check_desk():
    v = {"일봉 이동평균50 위/아래": -1.0, "4h 90봉 모멘텀 부호": -1.0, "일봉 이동평균20 위/아래": -1.0,          # 추세 3표
         "일봉 청산 급증(z>2) 뒤 3일 역방향, 아니면 이평50": 1.0, "일봉 전체 계정 롱숏 z 역방향(>1 숏/<-1 롱)": 0.0,   # 파생 2표
         "일봉 이평50 + 옵션 변동성 급등(z>2) 시 절반": -0.5}                                                # 거시·심리 1표
    assert sig.desk(v) == {"추세": -1.0, "파생": 0.5, "거시·심리": -0.5}
    assert abs(sig.combine(v) - (-1.0 + 0.5 - 0.5) / 3) < 1e-12, "분석가별 평균의 평균 (표 수가 많은 묶음이 독점하지 않음)"
    assert sig.combine({}) == 0.0 and sig.combine({"x": 0.7}) == 0.7
    print("분석가 집계 OK")


class LiveClient(FakeClient):
    """실거래 흉내: 지정가는 fill_ratio 만큼만 체결, 시장가는 전부 체결."""
    def __init__(self, fill_ratio, fail=False):
        super().__init__(); self.size, self.fill_ratio, self.fail, self.log = 0.0, fill_ratio, fail, []
    def account(self):
        self.withdrawable = 0.0
        return 1000.0, ({"BTC": {"size": self.size, "entry": 100.0, "liq": None, "lev": 5, "upnl": 0.0, "margin": 0.0}} if self.size else {})
    def maker_order(self, coin, size, wait, reduce_only=False):
        if self.fail:
            raise RuntimeError("취소 미확인")
        got = round(size * self.fill_ratio, 5); self.size += got; self.log.append(("maker", got)); return got
    def market_order(self, coin, size):
        self.size += size; self.log.append(("market", round(size, 5))); return {"avgPx": "100.0"}
    def fills_pnl(self, coin, since): return 0.0, 0.1, 100.0


def check_maker():
    with tempfile.TemporaryDirectory() as d:
        hl_live.STATE, hl_live.STOP_FILE = os.path.join(d, "s.json"), os.path.join(d, "STOP")
        hl_live.TRADES, hl_live.EQUITY = os.path.join(d, "t.csv"), os.path.join(d, "e.csv")
        sent = []; hl_live.notifier.notify = lambda t: sent.append(t) or True
        hl_live.HlBot.reselect = lambda self, reason: None
        def run(ratio, urgent=False, wait=300.0, fail=False):
            if os.path.exists(hl_live.STATE):
                os.remove(hl_live.STATE)
            c = LiveClient(ratio, fail)
            bot = hl_live.HlBot(HlConfig(live=True, maker_wait_sec=wait), c); bot.marks = {"BTC": 100.0}
            bot.trade("BTC", -2.0, 100.0, "시험", urgent)
            return c, bot
        c, bot = run(1.0); assert c.log == [("maker", -2.0)] and bot.s["pos"]["BTC"]["size"] == -2.0, "다 체결되면 시장가 없음"
        c, bot = run(0.25); assert c.log == [("maker", -0.5), ("market", -1.5)] and c.size == -2.0, "남은 수량만 시장가"
        c, bot = run(0.0); assert c.log == [("maker", 0.0), ("market", -2.0)], "하나도 안 채워지면 전부 시장가"
        c, bot = run(0.97); assert c.log == [("maker", -1.94)] and c.size == -1.94, "남은 게 10달러 미만이면 그대로 둠"
        c, bot = run(1.0, urgent=True); assert c.log == [("market", -2.0)], "급한 주문(정지·절반·대기·킬)은 바로 시장가"
        c, bot = run(1.0, wait=0.0); assert c.log == [("market", -2.0)], "HL_MAKER_WAIT_SEC=0 이면 지금처럼 시장가"
        sent.clear(); c, bot = run(1.0, fail=True)
        assert c.log == [] and c.size == 0.0 and any("지정가 주문에 문제" in x for x in sent), "취소가 확인 안 되면 시장가로 잇지 않고 알림"
        def only(ratio, have, delta):                                 # HL_MAKER_ONLY: 늘리는 몫은 지정가만, 줄이는 몫은 시장가로 마무리
            if os.path.exists(hl_live.STATE):
                os.remove(hl_live.STATE)
            c = LiveClient(ratio); c.size = have
            bot = hl_live.HlBot(HlConfig(live=True, maker_only=True), c); bot.marks = {"BTC": 100.0}
            if have:
                bot.s["pos"] = {"BTC": {"size": have, "entry": 100.0}}
            bot.trade("BTC", delta, 100.0, "시험"); c.bot = bot
            return c
        c = only(0.25, 0.0, -2.0); assert c.log == [("maker", -0.5)] and c.size == -0.5, "새로 여는 주문: 못 채운 건 시장가로 안 삼"
        c = only(0.0, -1.0, -1.0); assert c.log == [("maker", 0.0)] and c.size == -1.0, "늘리는 주문도 같음"
        c = only(0.5, 1.0, -3.0); assert c.log == [("maker", -1.5)] and c.size == -0.5 and not c.bot.s["pending"], "지정가로 이미 넘어갔으면 다시 걸 것 없음"
        c = only(0.25, 2.0, -2.0); b = c.bot                         # 정리 주문: 시장가 없이 남은 걸 틱마다 지정가로 다시 건다
        assert c.log == [("maker", -0.5)] and c.size == 1.5 and b.s["pending"] == {"BTC": 0.0}
        c.ctx = lambda: {"BTC": {"mid": 100.0, "funding": 0.0}, "ETH": {"mid": 10.0, "funding": 0.0}}
        b.s["last_bar"] = 10 ** 12; b.s["last_funding_hour"] = 10 ** 12; b.sync_funding = lambda: None
        c.fill_ratio = 0.5; b.tick(); assert c.log[-1] == ("maker", -0.75) and c.size == 0.75 and b.s["pending"] == {"BTC": 0.0}
        c.fill_ratio = 1.0; b.tick(); assert c.size == 0.0 and not b.s["pending"] and all(k == "maker" for k, _ in c.log), "시장가는 한 번도 안 씀"
        n = len(c.log); b.tick(); assert len(c.log) == n, "다 정리되면 더 걸지 않음"
        c = only(0.0, 1.0, -3.0); assert c.log == [("maker", 0.0)] and c.size == 1.0 and c.bot.s["pending"] == {"BTC": 0.0}, "뒤집기: 정리까지만 다시 시도"
        c = only(0.0, -2.0, 1.0); assert c.bot.s["pending"] == {"BTC": -1.0}, "일부 줄이기: 목표 수량까지"
        c = only(1.0, 2.0, -2.0); c.bot.s["pos"] = {"BTC": {"size": 2.0, "entry": 100.0}}; c.size = 2.0; c.log.clear()
        c.bot.trade("BTC", -2.0, 100.0, "킬", urgent=True, market=True); assert c.log == [("market", -2.0)], "킬 드로다운 정리만 시장가"
        c = only(1.0, 0.0, -2.0); c.log.clear(); c.size = 0.0; c.bot.s["pos"] = {}
        c.bot.trade("BTC", -1.0, 100.0, "직접 주문", urgent=True); assert c.log == [("maker", -1.0)], "급한 주문(명령·직접 주문)도 지정가"
        print("지정가 우선 OK")


def check_commands():
    with tempfile.TemporaryDirectory() as d:
        hl_live.STATE, hl_live.STOP_FILE, hl_live.HALF_FILE, hl_live.WAIT_FILE = os.path.join(d, "s.json"), os.path.join(d, "STOP"), os.path.join(d, "HALF"), os.path.join(d, "WAIT")
        hl_live.TRADES, hl_live.EQUITY, hl_live.VOTES = os.path.join(d, "t.csv"), os.path.join(d, "e.csv"), os.path.join(d, "v.csv")
        inbox, old_inbox = os.path.join(d, "cmd.txt"), hl_live.INBOX
        hl_live.INBOX = inbox
        sent = []; hl_live.notifier.notify = lambda t: sent.append(t) or True
        hl_live.HlBot.reselect = lambda self, reason: None
        class C(FakeClient):
            def ctx(self): return {"BTC": {"mid": px[0], "funding": 0.0}}
            def candles(self, coin, iv, n):
                df = fake_ohlc(50); df.iloc[-1, df.columns.get_loc("close")] = px[0]; return df
        px = [100.0]                                                  # 마지막 4시간 종가 = 지금 가격
        bot = hl_live.HlBot(HlConfig(budget_usd=1000, coins=("BTC",)), C()); bot.marks = {"BTC": 100.0}
        ov, ol = sig.vote, sig.leverage
        sig.vote = lambda *a, **k: (-1.0, {"x": -1.0}); sig.leverage = lambda *a, **k: 1.0
        def send(*cmds):
            with open(inbox, "a") as f:
                f.writelines(c + "\n" for c in cmds)
            bot.tick()
        try:
            bot.rebalance(); assert bot.s["pos"]["BTC"]["size"] == -10.0
            bot.s["last_bar"] = 10 ** 12; bot.s["last_funding_hour"] = 10 ** 12
            with open(os.path.join(d, "p.json"), "w") as f:
                json.dump({"equity_usd": 110.0, "start_usd": 100.0, "pos": {"ETH": {"size": -2.0, "entry": 10.0}}}, f)
            hl_live.PAPER_STATE = f.name
            sent.clear(); send("/hl_status")
            assert any("연습용(가상 돈, 대기 없이 투표대로): 평가 $110.00 (시작 대비 +10.00%) · ETH 숏 2개" in x for x in sent), sent
            assert any("BTC 숏 10개" in x and "분석가 의견: 추세 -1.00" in x and "다음 점검" in x for x in sent), sent
            send("/hl_wait"); assert bot.s["pos"]["BTC"]["size"] == -10.0, "확인 전에는 아무것도 안 함"
            send("/hl_yes"); assert not bot.s["pos"].get("BTC") and bot.s["half"] == {"BTC": -1} and bot.s["half_k"] == 0.0, "/hl_wait + /hl_yes → 전부 정리·대기"
            sent.clear(); send("/hl_yes"); assert any("확정할 명령이 없어요" in x for x in sent)
            send("/hl_half"); bot.s["confirm"][1] -= 61; sent.clear(); send("/hl_yes")
            assert any("확정할 명령이 없어요" in x for x in sent), "60초가 지나면 무효"
            send("/hl_go", "/hl_yes"); assert bot.s["half"] == {}, "대기 해제"
            bot.rebalance(); assert bot.s["pos"]["BTC"]["size"] < -9.9, "해제 뒤 다음 점검에서 투표대로"
            send("/hl_stop", "/hl_yes"); assert os.path.exists(hl_live.STOP_FILE) and not bot.s["pos"].get("BTC"), "정지: 바로 전부 정리"
            bot.rebalance(); assert not bot.s["pos"].get("BTC"), "정지 중에는 안 들어감"
            send("/hl_go", "/hl_yes"); assert not os.path.exists(hl_live.STOP_FILE)
            sent.clear(); send("/hl_abc"); assert any("모르는 명령" in x for x in sent)
            hl_live.TAG = "paper"; sent.clear(); send("/hl_wait")      # 연습용 쌍둥이: 안내 문구의 명령 이름이 /hlp_*
            assert any("/hlp_wait" in x and "/hlp_yes" in x and "/hl_" not in x for x in sent), sent
            hl_live.TAG = ""; bot.s.pop("confirm")
            # 직접 주문: 손댄 코인은 /hl_auto 전까지 봇이 안 건드린다
            size = lambda: bot.s["pos"].get("BTC", {}).get("size", 0.0)
            send("/hl_long btc 200"); assert size() == 0.0, "확인 전에는 주문 안 함"
            send("/hl_yes"); assert abs(size() - 2.0) < 1e-9 and bot.s["manual"] == ["BTC"]
            bot.rebalance(); assert abs(size() - 2.0) < 1e-9, "투표는 숏이지만 직접 주문한 코인은 그대로"
            send("/hl_wait", "/hl_yes"); assert abs(size() - 2.0) < 1e-9 and "BTC" not in bot.s["half"], "대기 명령도 직접 주문 포지션은 안 건드림"
            send("/hl_go", "/hl_yes")
            send("/hl_short btc 500", "/hl_yes"); assert abs(size() + 3.0) < 1e-9, "롱 2 → 숏 3 (뒤집기)"
            send("/hl_close btc", "/hl_yes"); assert size() == 0.0 and bot.s["manual"] == ["BTC"]
            bot.rebalance(); assert size() == 0.0, "정리 뒤에도 /hl_auto 전에는 봇이 다시 안 삼"
            for bad, word in (("/hl_long btc 5", "10달러"), ("/hl_long doge 100", "이렇게 보내"), ("/hl_long btc", "이렇게 보내"),
                              ("/hl_long btc nan", "10달러"), ("/hl_long btc 999999", "상한"), ("/hl_close btc", "포지션이 없어요")):
                sent.clear(); send(bad, "/hl_yes"); assert any(word in x for x in sent) and size() == 0.0, (bad, sent)
            # 예상 청산가: 최대 40배 → 유지 증거금률 1/80. 계좌 0.5배 롱은 청산 없음, 10배 롱 −8.86%·숏 +8.64%
            eq = bot.equity()
            assert bot.liq_estimate("BTC", 0.5 * eq / 100) is None
            assert abs(bot.liq_estimate("BTC", 10 * eq / 100) / 100 - (1 - (0.1 - 1 / 80) / (1 - 1 / 80))) < 1e-9
            assert abs(bot.liq_estimate("BTC", -10 * eq / 100) / 100 - (1 + (0.1 - 1 / 80) / (1 + 1 / 80))) < 1e-9
            sent.clear(); send("/hl_short btc 2x"); assert any("의 2.00배" in x and "예상 청산가 $148.1 (지금 가격에서 +48.1%" in x for x in sent), sent
            assert bot.chat_ctx()["예상 청산가 표(이 코인만 계좌 평가금액의 N배로 새로 잡을 때)"]["BTC"]["10배"] == {"롱": 91.1, "숏": 108.6}
            bot.s.pop("confirm")
            send("/hl_auto btc", "/hl_yes"); assert bot.s["manual"] == []
            sent.clear(); send("/hl_auto btc"); assert any("봇이 맡고" in x for x in sent)
            bot.rebalance(); before = size(); send("/hl_hold btc", "/hl_yes"); assert bot.s["manual"] == bot.s["hold"] == ["BTC"] and size() == before, "멈춤은 주문을 내지 않는다"
            short = sig.vote; sig.vote = lambda *a, **k: (1.0, {"x": 1.0}); bot.rebalance(); sig.vote = short
            assert size() == before < 0, "멈춘 코인은 투표가 뒤집혀도 그대로"
            send("/hl_auto btc", "/hl_yes"); assert bot.s["manual"] == [] == bot.s["hold"]
            bot.rebalance(); assert size() < -9.9, "돌려준 뒤 다음 점검에서 투표대로"
            send("/hl_close btc", "/hl_yes"); send("/hl_long btc 100", "/hl_yes"); assert abs(size() - 1.0) < 1e-9
            send("/hl_stop", "/hl_yes"); assert size() == 0.0, "정지는 직접 주문 포지션도 정리"
            sent.clear(); send("/hl_long btc 100"); assert any("정지 중" in x for x in sent)
            send("/hl_go", "/hl_yes", "/hl_auto btc", "/hl_yes"); assert bot.s["manual"] == []
            # 직접 주문 포지션의 이익 보호: 최고 이익에서 5% 되돌린 4h 종가 → 75% 정리, 한 번만
            def close_at(p): px[0] = p; bot.tick(); bot.rebalance()
            send("/hl_long btc 200", "/hl_yes"); assert abs(size() - 2.0) < 1e-9 and "BTC" not in bot.s.get("guard", {})
            close_at(100.0); assert abs(size() - 2.0) < 1e-9 and abs(bot.s["guard"]["BTC"]["line"] - 95.24) < 0.01, bot.s["guard"]
            close_at(110.0); assert abs(size() - 2.0) < 1e-9 and abs(bot.s["guard"]["BTC"]["best"] - 0.10) < 1e-9
            close_at(106.0); assert abs(size() - 2.0) < 1e-9, "3.8% 되돌림은 아직"
            sent.clear(); close_at(104.0); assert abs(size() - 0.5) < 1e-9, "5% 넘게 되돌림 → 75% 정리"
            assert any("이익 보호" in x and "/hl_auto" in x for x in sent), sent
            close_at(90.0); assert abs(size() - 0.5) < 1e-9, "남은 25% 는 그대로 (다시 줄이지 않음)"
            px[0] = 100.0; bot.tick(); send("/hl_long btc 100", "/hl_yes")
            assert abs(size() - 1.5) < 1e-9 and "BTC" not in bot.s["guard"], "추가 주문 → 이익 보호를 새 포지션 기준으로 다시"
            close_at(100.0); assert abs(size() - 1.5) < 1e-9
            close_at(93.0); assert abs(size() - 0.375) < 1e-9, "이익 없이 5% 불리하게 가도 75% 정리"
            px[0] = 100.0; bot.tick(); send("/hl_close btc", "/hl_yes", "/hl_auto btc", "/hl_yes"); assert size() == 0.0 and bot.s["manual"] == []
            # 예약 주문: 가격이 닿으면 직접 주문과 같은 경로로 실행하고 지운다
            sent.clear(); send("/hl_at btc 2% close"); assert any("포지션이 없어서" in x for x in sent), sent
            send("/hl_at btc 95 long 200"); assert not bot.s.get("resv"), "확인 전에는 안 걸림"
            send("/hl_yes"); r = bot.s["resv"][0]; assert (r["px"], r["up"], r["cmd"]) == (95.0, False, "/hl_long btc 200"), r
            px[0] = 97.0; bot.tick(); assert size() == 0.0 and len(bot.s["resv"]) == 1, "아직 안 닿음"
            px[0] = 94.0; bot.tick(); assert abs(size() - 200 / 94) < 1e-4 and bot.s["resv"] == [] and bot.s["manual"] == ["BTC"], "닿으면 실행하고 지움"
            send("/hl_at btc 2% close", "/hl_yes"); r = bot.s["resv"][0]; assert abs(r["px"] - 94 * 1.02) < 0.01 and r["up"], "롱의 2% 이익 = 진입가 위"
            send("/hl_at btc -3% close", "/hl_yes"); assert abs(bot.s["resv"][1]["px"] - 94 * 0.97) < 0.01 and not bot.s["resv"][1]["up"], "손실 쪽"
            send("/hl_at btc 80 short 100", "/hl_yes"); sent.clear(); send("/hl_list"); assert any("번 · BTC" in x and "/hl_cancel" in x for x in sent) and "예약" in bot.status_text(), sent
            send(f"/hl_cancel {bot.s['resv'][2]['id']}"); assert len(bot.s["resv"]) == 2
            sent.clear(); px[0] = 96.0; bot.tick(); assert size() == 0.0 and bot.s["resv"] == [], "이익 2% 지점에서 전부 정리, 남은 정리 예약은 포지션이 없어 지움"
            assert any("이미 없어요" in x for x in sent), sent
            sent.clear(); send("/hl_at btc x close", "/hl_cancel 9"); assert any("이렇게 보내" in x for x in sent) and any("그 번호의 예약이 없어요" in x for x in sent)
            px[0] = 100.0; bot.tick(); send("/hl_auto btc", "/hl_yes"); assert size() == 0.0 and bot.s["manual"] == []
            C = hl_live.hl_ai.CHAT_CMD
            assert C.fullmatch("/hl_at eth 2% close") and C.fullmatch("/hl_at btc 63000 long 100") and C.fullmatch("/hl_at eth -3% close") and C.fullmatch("/hl_cancel all")
            assert not C.fullmatch("/hl_at eth 2% close\n/hl_yes") and not C.fullmatch("/hl_at eth 2% long")
            # 대화: 답을 보내고, AI 가 제안한 명령은 명령함을 거쳐 확인을 다시 받는다 (/hl_yes 는 제안 못 함)
            assert hl_live.hl_ai.CHAT_CMD.fullmatch("/hl_short btc 100") and hl_live.hl_ai.CHAT_CMD.fullmatch("/hl_long eth 10x") and not hl_live.hl_ai.CHAT_CMD.fullmatch("/hl_yes")
            assert not hl_live.hl_ai.CHAT_CMD.fullmatch("/hl_short btc 100\n/hl_yes")
            asked = []
            hl_live.hl_ai.chat = lambda q, ctx, hist: (asked.append((q, ctx, hist)) or ("숏 100달러 주문을 준비했어요", "/hl_short btc 100", {}))
            sent.clear(); bot.answer("비트코인 숏 100달러 넣어줘", bot.chat_ctx())
            assert any("주문을 준비했어요" in x for x in sent) and "상태" in asked[0][1] and size() == 0.0
            sent.clear(); bot.tick(); assert any("/hl_yes 를 보내면" in x for x in sent) and size() == 0.0, "제안만으로는 주문 안 함"
            send("/hl_yes"); assert abs(size() + 1.0) < 1e-9
            bot.answer("고마워", bot.chat_ctx()); assert asked[1][2] == [{"질문": "비트코인 숏 100달러 넣어줘", "답": "숏 100달러 주문을 준비했어요"}]
        finally:
            sig.vote, sig.leverage, hl_live.INBOX = ov, ol, old_inbox
        print("명령 OK")


def check_chase():
    """HlClient.maker_order 의 호가 따라가기: 가짜 거래소·가짜 시계로 실제 함수를 돌린다."""
    import types
    import bot.hl_client as hc
    real_time = hc.time
    def run(size, script, wait=60.0, cancel_ok=True):
        t, pos, bid, orders, log = [0.0], [0.0], [100.0], {}, []
        class Ex:
            def order(self, coin, is_buy, sz, px, typ, reduce_only=False):
                assert typ == {"limit": {"tif": "Alo"}} and is_buy == (size > 0)
                oid = len(log) + 1; orders[oid] = sz; log.append(("order", sz, px, reduce_only))
                return {"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": oid}}]}}}
            def cancel(self, coin, oid):
                log.append(("cancel", oid))
                if cancel_ok:
                    orders.pop(oid, None)
                return {"status": "ok"}
        class Info:
            def l2_snapshot(self, coin): return {"levels": [[{"px": str(bid[0])}], [{"px": str(bid[0] + 1)}]]}
            def open_orders(self, wallet): return [{"oid": o} for o in orders]
        def fill(frac):                                              # 걸려 있는 주문의 frac 만큼 체결
            for oid in list(orders):
                got = orders[oid] * frac; pos[0] += got if size > 0 else -got
                orders[oid] -= got
                if orders[oid] < 1e-9:
                    orders.pop(oid)
        def sleep(x):
            t[0] += x
            for at, what, v in script:
                if abs(t[0] - at) < 1e-9:
                    bid.__setitem__(0, v) if what == "bid" else fill(v)
        hc.time = types.SimpleNamespace(time=lambda: t[0], sleep=sleep)
        c = object.__new__(hc.HlClient); c.ex, c.info_sdk, c.wallet = Ex(), Info(), "w"
        c.sz_decimals = lambda coin: 5
        c.account = lambda: (1000.0, {"BTC": {"size": pos[0]}} if pos[0] else {})
        try:
            return c.maker_order("BTC", size, wait), log, orders
        finally:
            hc.time = real_time
    got, log, left = run(1.0, [(20.0, "bid", 101.0), (30.0, "fill", 1.0)])
    assert got == 1.0 and log == [("order", 1.0, 100.0, False), ("cancel", 1), ("order", 1.0, 101.0, False)] and not left, log
    got, log, left = run(1.0, [(10.0, "fill", 0.4), (20.0, "bid", 101.0)])
    assert abs(got - 0.4) < 1e-9 and log[2] == ("order", 0.6, 101.0, False) and log[-1][0] == "cancel" and not left, "일부 체결 뒤엔 남은 수량만 다시 걸고, 끝나면 취소"
    got, log, left = run(-1.0, [(5.0, "fill", 1.0)])
    assert got == -1.0 and log == [("order", 1.0, 101.0, False)], "매도는 매도호가에"
    got, log, left = run(1.0, [])
    assert got == 0.0 and log == [("order", 1.0, 100.0, False), ("cancel", 1)] and not left, "호가가 그대로면 다시 걸지 않고 기다렸다 취소"
    got, log, left = run(0.00005, [])
    assert got == 0.0 and log == [], "10달러 미만은 걸지 않음"
    try:
        run(1.0, [], cancel_ok=False); raise AssertionError("취소 미확인이면 예외여야 함")
    except RuntimeError:
        pass
    print("지정가 따라가기 OK")


def check_ai_paper():
    """AI 연습 계좌(--mode hl-ai): 노출을 AI 답으로 정한다. 실패·예산 초과면 지난 판단 유지, 이익 보호 없음."""
    with tempfile.TemporaryDirectory() as d:
        P = lambda n: os.path.join(d, n)
        hl_live.STATE, hl_live.STOP_FILE, hl_live.HALF_FILE, hl_live.WAIT_FILE = P("s.json"), P("STOP"), P("HALF"), P("WAIT")
        hl_live.TRADES, hl_live.EQUITY, hl_live.VOTES = P("t.csv"), P("e.csv"), P("v.csv")
        old = hl_live.DESK, hl_live.hl_ai.ask, hl_live.hl_ai.whales_now, sig.vote, sig.leverage
        hl_live.DESK, hl_live.AI_MODE = P("desk.jsonl"), True
        hl_live.notifier.notify = lambda t: True
        hl_live.HlBot.reselect = lambda self, reason: None
        calls, exp, fail = [], [0.5], [False]
        def ask(boards, prompt="hl_main.md", **k):
            calls.append((prompt, boards))
            if fail[0]:
                raise RuntimeError("API 오류")
            return {"BTC": {"exposure": exp[0], "reason": "이유"}, "easy": "쉬운 말"}, {"cost_usd": 0.02, "model": "m"}
        hl_live.hl_ai.ask, hl_live.hl_ai.whales_now = ask, lambda: {}
        sig.vote = lambda *a, **k: (-1.0, {"x": -1.0}); sig.leverage = lambda *a, **k: 2.0
        class C(FakeClient):
            def candles(self, coin, iv, n):
                df = fake_ohlc(300); df.iloc[-1, df.columns.get_loc("close")] = 100.0; return df
        try:
            bot = hl_live.HlBot(HlConfig(budget_usd=1000, coins=("BTC",)), C()); bot.marks = {"BTC": 100.0}
            size = lambda: bot.s["pos"].get("BTC", {}).get("size", 0.0)
            bot.rebalance()
            prompt, boards = calls[-1]
            assert prompt == "hl_trader.md" and list(boards[0]["지표"]) == ["15분봉", "1시간봉", "4시간봉", "일봉"] and boards[0]["지난 판단"] == []
            assert abs(size() - 10.0) < 1e-6, "규칙은 숏이지만 AI 가 +0.5 → 롱 (1000 × 0.5 × 2배 ÷ 100)"
            assert bot.s["ai"]["coins"]["BTC"]["exposure"] == 0.5 and bot.s["ai"]["easy"] == "쉬운 말" and abs(bot.s["ai_cost"]["usd"] - 0.02) < 1e-9
            assert "BTC" not in bot.s.get("guard", {}), "AI 연습 계좌에는 이익 보호를 걸지 않는다"
            fail[0] = True; bot.rebalance(); assert abs(size() - 10.0) < 1e-6, "AI 호출 실패 → 지난 판단 유지"
            fail[0] = False; exp[0] = -1.0; bot.rebalance(); assert size() < -19.0, "판단이 바뀌면 뒤집는다"
            assert calls[-1][1][0]["지난 판단"][0]["exposure"] == 0.5 and len(bot.s["ai_hist"]["BTC"]) == 2
            bot.s["ai_cost"]["usd"] = 99.0; n = len(calls); before = size(); bot.rebalance()
            assert len(calls) == n and size() == before, "하루 예산을 넘으면 AI 를 부르지 않고 그대로"
            rec = json.loads(open(hl_live.DESK).readlines()[-1])["coins"]["BTC"]
            assert rec["rule"] == -1.0 and rec["exposure"] == -1.0
        finally:
            hl_live.AI_MODE = False
            hl_live.DESK, hl_live.hl_ai.ask, hl_live.hl_ai.whales_now, sig.vote, sig.leverage = old
        print("AI 연습 계좌 OK")


if __name__ == "__main__":
    check_signals(); check_desk(); check_paper_fills(); check_funding(); check_band(); check_half(); check_maker(); check_chase(); check_commands()
    check_ai_paper()
    check_guard(); check_alerts(); check_scalp()
    print("모든 hl-agent 테스트 통과")
