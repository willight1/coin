"""
dashboard.py — 트레이딩 로그 시각화 대시보드
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from bot import notifier
from bot.config import BOT_CFG, DEFAULT_TRADE_STRATEGY, UPBIT_CFG
from bot.paths import PROJECT_ROOT
from bot.strategies import get_strategy_by_name
from bot.upbit_client import UpbitClient

LOG_DIR = Path("logs")


def list_log_files() -> list[Path]:
    return sorted(LOG_DIR.glob("trade_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)


def bot_running() -> bool:
    return subprocess.run(["pgrep", "-f", "main.py --mode trade"],
                          capture_output=True).returncode == 0


def manual_hold(market: str) -> str:
    """봇의 수동 주문 정지 상태 (bot/trader.py _set_manual_hold 가 쓰는 파일). 없으면 ""."""
    try:
        return json.loads((Path(PROJECT_ROOT) / "state" / f"manual_hold_{market}.json")
                          .read_text(encoding="utf-8")).get("signal", "")
    except (OSError, ValueError):
        return ""


def parse_log(log_path: Path) -> dict:
    signal_counter = Counter()
    buy_block_counter = Counter()
    sell_block_counter = Counter()
    events = Counter()
    errors = 0
    warnings = 0

    re_signal = re.compile(r"전략 신호(?: 변경: [^>]+ ->)?\s*(BUY|SELL|HOLD)")
    re_buy_block = re.compile(r"매수 차단:\s*(.+)$")
    re_sell_block = re.compile(r"매도 차단:\s*(.+)$")
    re_snapshot = re.compile(
        r"\[계좌\]\s*(START|END|TICK)\s+market=([A-Z0-9\-]+)\s+equity=([0-9.]+)"
    )
    re_snapshot_detail = re.compile(
        r"\[계좌\]\s*(START|END|TICK)\s+market=([A-Z0-9\-]+)\s+equity=([0-9.]+)\s+"
        r"krw=([0-9.]+)\s+coin=([0-9.]+)\s+coin_value=([0-9.]+)\s+price=([0-9.]+)"
        r"(?:\s+avg_buy=([0-9.]+)\s+buy_cost=([0-9.]+))?"
    )
    re_ts = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

    snapshots: list[dict] = []
    snapshot_details: list[dict] = []
    ai_lines: list[str] = []
    trade_lines: list[str] = []
    trade_execs: list[dict] = []

    with log_path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if "[ERROR" in line:
                errors += 1
            if "[WARNING" in line:
                warnings += 1

            m = re_signal.search(line)
            if m:
                signal_counter[m.group(1)] += 1

            m = re_buy_block.search(line)
            if m:
                buy_block_counter[m.group(1).strip()] += 1

            m = re_sell_block.search(line)
            if m:
                sell_block_counter[m.group(1).strip()] += 1

            if ">> 매수 실행" in line:
                events["buy_attempt"] += 1
                trade_lines.append(line.strip())
                ts_match = re_ts.search(line)
                if ts_match:
                    trade_execs.append(
                        {"timestamp": pd.to_datetime(ts_match.group(1)), "side": "BUY"}
                    )
            if ">> 매도 실행" in line:
                events["sell_attempt"] += 1
                trade_lines.append(line.strip())
                ts_match = re_ts.search(line)
                if ts_match:
                    trade_execs.append(
                        {"timestamp": pd.to_datetime(ts_match.group(1)), "side": "SELL"}
                    )
            if "매수 주문 실패" in line:
                events["buy_fail"] += 1
                trade_lines.append(line.strip())
            if "매도 주문 실패" in line:
                events["sell_fail"] += 1
                trade_lines.append(line.strip())
            if "매도 보류:" in line:
                events["sell_hold"] += 1
            if "손절 도달:" in line:
                events["stop_loss"] += 1
            if "익절 도달:" in line:
                events["take_profit"] += 1
            if "트레일링 스탑 도달:" in line:
                events["trailing_stop"] += 1

            if "AI 신호 분석:" in line or "AI 최종 신호 적용:" in line:
                ai_lines.append(line.strip())

            m = re_snapshot.search(line)
            if m:
                label, market, equity = m.groups()
                ts_match = re_ts.search(line)
                ts = pd.to_datetime(ts_match.group(1)) if ts_match else pd.NaT
                snapshots.append(
                    {
                        "timestamp": ts,
                        "label": label,
                        "market": market,
                        "equity": float(equity),
                    }
                )
            d = re_snapshot_detail.search(line)
            if d:
                label, market, equity, krw, coin, coin_value, price, avg_buy, buy_cost = d.groups()
                ts_match = re_ts.search(line)
                ts = pd.to_datetime(ts_match.group(1)) if ts_match else pd.NaT
                snapshot_details.append(
                    {
                        "timestamp": ts,
                        "label": label,
                        "market": market,
                        "equity": float(equity),
                        "krw": float(krw),
                        "coin": float(coin),
                        "coin_value": float(coin_value),
                        "price": float(price),
                        "avg_buy": float(avg_buy) if avg_buy else 0.0,
                        "buy_cost": float(buy_cost) if buy_cost else 0.0,
                    }
                )

    snap_df = pd.DataFrame(snapshots)
    if not snap_df.empty:
        snap_df = snap_df.sort_values("timestamp").reset_index(drop=True)

    start_equity = None
    end_equity = None
    last_equity = None
    market = ""
    if not snap_df.empty:
        market = str(snap_df["market"].iloc[-1])
        start_rows = snap_df[snap_df["label"] == "START"]
        end_rows = snap_df[snap_df["label"] == "END"]
        start_equity = float(start_rows["equity"].iloc[0]) if not start_rows.empty else None
        end_equity = float(end_rows["equity"].iloc[-1]) if not end_rows.empty else None
        last_equity = float(snap_df["equity"].iloc[-1])

    final_equity = end_equity if end_equity is not None else last_equity
    ret_pct = None
    if start_equity and final_equity and start_equity > 0:
        ret_pct = (final_equity - start_equity) / start_equity * 100

    latest_snapshot_detail = None
    if snapshot_details:
        snapshot_details = sorted(
            snapshot_details,
            key=lambda x: x.get("timestamp", pd.NaT),
        )
        latest_snapshot_detail = snapshot_details[-1]

    return {
        "signals": signal_counter,
        "buy_blocks": buy_block_counter,
        "sell_blocks": sell_block_counter,
        "events": events,
        "errors": errors,
        "warnings": warnings,
        "snapshots": snap_df,
        "start_equity": start_equity,
        "end_equity": end_equity,
        "last_equity": last_equity,
        "market": market,
        "return_pct": ret_pct,
        "ai_lines": ai_lines[-200:],
        "trade_lines": trade_lines[-200:],
        "trade_execs": pd.DataFrame(trade_execs),
        "latest_snapshot_detail": latest_snapshot_detail,
    }


@st.cache_data(ttl=1, show_spinner=False)
def load_candles(market: str, unit: int, count: int) -> pd.DataFrame:
    client = UpbitClient(dry_run=True)
    candles = client.get_candles_minutes(unit=unit, market=market, count=count)
    if not candles:
        return pd.DataFrame()
    raw = pd.DataFrame(candles)
    if "candle_date_time_kst" not in raw.columns:
        return pd.DataFrame()

    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(raw["candle_date_time_kst"]),
            "open": raw.get("opening_price"),
            "high": raw.get("high_price"),
            "low": raw.get("low_price"),
            "close": raw.get("trade_price"),
            "volume": raw.get("candle_acc_trade_volume"),
        }
    )
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])


@st.cache_data(ttl=2, show_spinner=False)
def load_account_snapshot() -> dict:
    client = UpbitClient(dry_run=True)
    accounts = client.get_accounts()
    rows = []
    for a in accounts:
        currency = str(a.get("currency", ""))
        balance = float(a.get("balance", 0) or 0)
        locked = float(a.get("locked", 0) or 0)
        avg_buy_price = float(a.get("avg_buy_price", 0) or 0)
        unit_currency = str(a.get("unit_currency", ""))
        total = balance + locked
        if total <= 0:
            continue
        rows.append(
            {
                "화폐": currency,
                "주문통화": unit_currency,
                "보유수량": balance,
                "주문중수량": locked,
                "총수량": total,
                "평균매수가": avg_buy_price,
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return {"krw_available": 0.0, "krw_locked": 0.0, "krw_total": 0.0, "coins": df}

    krw_row = df[df["화폐"] == "KRW"]
    krw_available = float(krw_row["보유수량"].iloc[0]) if not krw_row.empty else 0.0
    krw_locked = float(krw_row["주문중수량"].iloc[0]) if not krw_row.empty else 0.0
    coin_df = df[df["화폐"] != "KRW"].copy().reset_index(drop=True)
    return {
        "krw_available": krw_available,
        "krw_locked": krw_locked,
        "krw_total": krw_available + krw_locked,
        "coins": coin_df,
    }


def render_account_panel(data: dict) -> None:
    st.subheader("현재 잔액 / 보유 코인")
    detail = data.get("latest_snapshot_detail")
    if detail:
        st.caption("봇 스냅샷 (DRY_RUN이면 가상자산 기준)")
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("봇코인", f"{detail['coin']:.8f}")
        d2.metric("매수금", f"{detail.get('buy_cost', 0.0):,.0f} KRW")
        d3.metric("매수코인평단", f"{detail.get('avg_buy', 0.0):,.0f} KRW")
        d4.metric("현재코인평단", f"{detail['price']:,.0f} KRW")
        st.write(
            f"마켓: `{detail['market']}` / 봇 총자산: `{detail['equity']:,.0f} KRW` / "
            f"스냅샷: `{detail['label']}`"
        )
        st.divider()

    st.caption("업비트 실계좌 조회")
    try:
        snapshot = load_account_snapshot()
    except Exception as e:
        st.warning(f"계좌 조회 실패: {e}")
        st.divider()
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("KRW 사용가능", f"{snapshot['krw_available']:,.0f} KRW")
    c2.metric("KRW 주문중", f"{snapshot['krw_locked']:,.0f} KRW")
    c3.metric("KRW 합계", f"{snapshot['krw_total']:,.0f} KRW")

    coin_df: pd.DataFrame = snapshot["coins"]
    if coin_df.empty:
        st.info("보유 코인이 없습니다.")
    else:
        st.dataframe(coin_df, use_container_width=True, hide_index=True)
    st.divider()


def render_realtime_chart(data: dict, market: str, unit: int, count: int) -> None:
    try:
        cdf = load_candles(market=market, unit=unit, count=count)
    except Exception as e:
        st.error(f"캔들 조회 실패: {e}")
        return

    if cdf.empty:
        st.warning("캔들 데이터가 없습니다.")
        return

    fig = go.Figure(
        data=[
            go.Candlestick(
                x=cdf["timestamp"],
                open=cdf["open"],
                high=cdf["high"],
                low=cdf["low"],
                close=cdf["close"],
                name=f"{market} {unit}m",
            )
        ]
    )

    exec_df: pd.DataFrame = data["trade_execs"]
    if not exec_df.empty:
        exec_df = exec_df.copy()
        exec_df = exec_df[
            (exec_df["timestamp"] >= cdf["timestamp"].min())
            & (exec_df["timestamp"] <= cdf["timestamp"].max())
        ]
        if not exec_df.empty:
            merged = pd.merge_asof(
                exec_df.sort_values("timestamp"),
                cdf[["timestamp", "close"]].sort_values("timestamp"),
                on="timestamp",
                direction="nearest",
                tolerance=pd.Timedelta(minutes=max(1, unit)),
            )
            buy_df = merged[merged["side"] == "BUY"].dropna(subset=["close"])
            sell_df = merged[merged["side"] == "SELL"].dropna(subset=["close"])
            if not buy_df.empty:
                fig.add_trace(
                    go.Scatter(
                        x=buy_df["timestamp"],
                        y=buy_df["close"],
                        mode="markers",
                        marker=dict(color="green", size=10, symbol="triangle-up"),
                        name="BUY",
                    )
                )
            if not sell_df.empty:
                fig.add_trace(
                    go.Scatter(
                        x=sell_df["timestamp"],
                        y=sell_df["close"],
                        mode="markers",
                        marker=dict(color="red", size=10, symbol="triangle-down"),
                        name="SELL",
                    )
                )

    fig.update_layout(
        xaxis_rangeslider_visible=False,
        height=520,
        margin=dict(l=10, r=10, t=30, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_dashboard(log_path: Path, data: dict | None = None) -> None:
    if data is None:
        data = parse_log(log_path)
    signals = data["signals"]
    events = data["events"]

    st.title("업비트 트레이딩 로그 대시보드")
    st.caption(f"로그 파일: {log_path}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("BUY 신호", signals.get("BUY", 0))
    c2.metric("SELL 신호", signals.get("SELL", 0))
    c3.metric("HOLD 신호", signals.get("HOLD", 0))
    c4.metric("오류/경고", f"{data['errors']} / {data['warnings']}")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("매수 시도/실패", f"{events.get('buy_attempt', 0)} / {events.get('buy_fail', 0)}")
    c6.metric("매도 시도/실패", f"{events.get('sell_attempt', 0)} / {events.get('sell_fail', 0)}")
    c7.metric("매도 보류", events.get("sell_hold", 0))
    ret_txt = "-" if data["return_pct"] is None else f"{data['return_pct']:.3f}%"
    c8.metric("수익률(스냅샷)", ret_txt)

    st.divider()

    snap_df: pd.DataFrame = data["snapshots"]
    if not snap_df.empty:
        st.subheader("자산 추이")
        chart_df = snap_df[["timestamp", "equity"]].dropna()
        if not chart_df.empty:
            st.line_chart(chart_df.set_index("timestamp"))

        s1, s2, s3 = st.columns(3)
        s1.write(f"시장: `{data['market']}`")
        s2.write(f"시작 자산: `{data['start_equity']}`")
        final_eq = data["end_equity"] if data["end_equity"] is not None else data["last_equity"]
        s3.write(f"종료/마지막 자산: `{final_eq}`")
    else:
        st.info("스냅샷 데이터가 없습니다.")

    st.divider()

    b1, b2 = st.columns(2)
    with b1:
        st.subheader("매수 차단 상위")
        if data["buy_blocks"]:
            st.dataframe(
                pd.DataFrame(data["buy_blocks"].most_common(15), columns=["사유", "횟수"]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.write("없음")
    with b2:
        st.subheader("매도 차단 상위")
        if data["sell_blocks"]:
            st.dataframe(
                pd.DataFrame(data["sell_blocks"].most_common(15), columns=["사유", "횟수"]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.write("없음")

    st.divider()
    st.subheader("AI 신호 로그 (최근)")
    if data["ai_lines"]:
        st.code("\n".join(data["ai_lines"][-80:]), language="text")
    else:
        st.write("AI 로그 없음")

    st.subheader("주문/실패 로그 (최근)")
    if data["trade_lines"]:
        st.code("\n".join(data["trade_lines"][-80:]), language="text")
    else:
        st.write("주문 로그 없음")


def render_controls() -> None:
    """주문은 대시보드가 직접 내지 않고 봇에 넘긴다 (텔레그램 /buy·/sell 과 같은 경로).
    직접 내면 봇이 10초 뒤 신호대로 되돌려 버리고, 수동 주문 후 정지도 안 걸린다."""
    market = UPBIT_CFG.market
    st.sidebar.header(f"봇 주문 ({market})")
    if bot_running():
        st.sidebar.success("봇 실행 중")
    else:
        st.sidebar.error("봇이 꺼져 있습니다 — 주문이 전달되지 않습니다")
    hold = manual_hold(market)
    if hold:
        st.sidebar.warning(f"자동매매 정지 중 — 신호가 {hold}에서 바뀌면 재개")
    st.sidebar.caption("현재가 시장가 (매수: 현금 전액, 매도: 전량). 주문 후에는 신호가 바뀔 때까지 "
                       "자동매매가 멈춥니다. 결과는 텔레그램과 아래 주문 로그에 남습니다.")
    armed = st.sidebar.checkbox("주문 잠금 해제")
    b1, b2 = st.sidebar.columns(2)
    if b1.button("매수", disabled=not armed, use_container_width=True):
        notifier.send_command("/buy", "/yes")
        st.sidebar.info("봇에 매수 전달 — 10초 안에 처리됩니다")
    if b2.button("매도", disabled=not armed, use_container_width=True):
        notifier.send_command("/sell", "/yes")
        st.sidebar.info("봇에 매도 전달 — 10초 안에 처리됩니다")
    if st.sidebar.button("자동매매 재개", disabled=not hold, use_container_width=True):
        notifier.send_command("/auto")
        st.sidebar.info("봇에 재개 전달")
    st.sidebar.divider()


def render_signal_panel(market: str) -> None:
    """봇과 같은 전략·같은 확정봉 기준 신호 (봇은 진행 중인 봉을 빼고 판단한다)."""
    strategy = get_strategy_by_name(DEFAULT_TRADE_STRATEGY)
    try:
        df = load_candles(market=market, unit=BOT_CFG.candle_unit, count=200)
    except Exception as e:
        st.warning(f"신호 계산용 캔들 조회 실패: {e}")
        return
    if strategy is None or len(df) < 2:
        return
    closed = df.iloc[:-1]
    signal = strategy.generate_signal(closed)
    price = float(df["close"].iloc[-1])
    th = strategy.intraday_threshold(closed)
    st.subheader(f"봇 신호 — {strategy.name}")
    c1, c2, c3 = st.columns(3)
    c1.metric("현재가", f"{price:,.0f}")
    c2.metric("확정 신호", {"BUY": "매수(보유)", "SELL": "매도(현금)"}.get(signal, signal),
              f"확정봉 {closed['timestamp'].iloc[-1]:%m-%d}", delta_color="off")
    if th:
        c3.metric("오늘 종가 기준선", f"{th:,.0f}", f"{(price / th - 1) * 100:+.2f}%")
    extra = strategy.status_text(closed)
    if extra:
        st.caption(extra)
    st.divider()


def main() -> None:
    st.set_page_config(page_title="Trading Dashboard", layout="wide")

    logs = list_log_files()
    if not logs:
        st.error("logs/ 디렉토리에 trade_*.log 파일이 없습니다.")
        return

    labels = [p.name for p in logs]
    selected = st.sidebar.selectbox("로그 선택", options=labels, index=0)
    market = st.sidebar.text_input("차트 마켓", value="KRW-BTC")
    unit_options = [1, 3, 5, 15, 30, 60, 240, 1440]
    unit = st.sidebar.selectbox(
        "봉 단위(분)", options=unit_options,
        index=unit_options.index(BOT_CFG.candle_unit)
        if BOT_CFG.candle_unit in unit_options else 0,
    )
    count = st.sidebar.slider("조회 캔들 수", min_value=50, max_value=500, value=200, step=10)
    render_controls()
    if st.sidebar.button("차트 새로고침"):
        st.cache_data.clear()
        st.rerun()
    auto_refresh = st.sidebar.checkbox("차트 자동 갱신", value=True)
    refresh_sec = st.sidebar.slider("자동 갱신(초)", min_value=1, max_value=30, value=1, step=1)

    log_path = next(p for p in logs if p.name == selected)

    def render_all() -> None:
        data = parse_log(log_path)
        render_signal_panel(market)
        render_account_panel(data)
        st.subheader("실시간 캔들 차트")
        render_realtime_chart(data, market=market, unit=unit, count=count)
        st.divider()
        render_dashboard(log_path, data=data)

    if auto_refresh:
        @st.fragment(run_every=f"{refresh_sec}s")
        def live_all() -> None:
            render_all()

        live_all()
    else:
        render_all()


if __name__ == "__main__":
    main()
