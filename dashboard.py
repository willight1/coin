"""
dashboard.py — 트레이딩 로그 시각화 대시보드
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from bot.paths import PROJECT_ROOT
from bot.strategies import get_all_strategies
from bot.upbit_client import UpbitClient

LOG_DIR = Path("logs")
RUNNER_STATE_PATH = LOG_DIR / "trader_runner_state.json"
RUNNER_STDOUT_LOG = LOG_DIR / "trader_runner.log"


def list_log_files() -> list[Path]:
    return sorted(LOG_DIR.glob("trade_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)


def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def get_runner_state() -> dict:
    if not RUNNER_STATE_PATH.exists():
        return {"running": False}
    try:
        state = json.loads(RUNNER_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"running": False}

    pid = int(state.get("pid", 0) or 0)
    state["running"] = _is_pid_running(pid)
    if not state["running"]:
        return {"running": False}
    return state


def start_trade_runner(strategy_name: str, dry_run: bool) -> tuple[bool, str]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not dry_run:
        return False, "대시보드 자동매매 시작은 현재 DRY_RUN만 지원합니다. (LIVE는 터미널 실행 권장)"
    existing = get_runner_state()
    if existing.get("running"):
        return False, f"이미 실행 중입니다. PID={existing.get('pid')}"

    cmd = [
        sys.executable,
        "main.py",
        "--mode",
        "trade",
        "--strategy",
        strategy_name,
    ]
    env = os.environ.copy()
    env["BOT_DRY_RUN"] = "true" if dry_run else "false"

    with RUNNER_STDOUT_LOG.open("a", encoding="utf-8") as out:
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=out,
            stderr=out,
            start_new_session=True,
        )

    state = {
        "pid": proc.pid,
        "strategy": strategy_name,
        "dry_run": dry_run,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "cmd": " ".join(cmd),
    }
    RUNNER_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return True, f"자동매매 시작: PID={proc.pid}, 전략={strategy_name}, DRY_RUN={dry_run}"


def stop_trade_runner() -> tuple[bool, str]:
    state = get_runner_state()
    if not state.get("running"):
        return False, "실행 중인 자동매매 프로세스가 없습니다."

    pid = int(state.get("pid", 0))
    try:
        os.killpg(pid, signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception as e:
            return False, f"중지 실패: {e}"

    RUNNER_STATE_PATH.write_text(
        json.dumps(
            {
                "running": False,
                "stopped_at": datetime.now().isoformat(timespec="seconds"),
                "last_pid": pid,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return True, f"자동매매 중지 요청 완료: PID={pid}"


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


def render_controls(default_market: str) -> None:
    st.sidebar.header("자동매매 제어")
    strategies = [s.name for s in get_all_strategies()]
    default_strategy = (
        "일봉 생존형 추세·눌림" if "일봉 생존형 추세·눌림" in strategies else strategies[0]
    )
    strategy_name = st.sidebar.selectbox(
        "실행 전략",
        options=strategies,
        index=strategies.index(default_strategy),
    )
    run_dry = st.sidebar.checkbox("자동매매 DRY_RUN", value=True)

    runner_state = get_runner_state()
    if runner_state.get("running"):
        st.sidebar.success(
            f"실행 중: PID={runner_state.get('pid')} / {runner_state.get('strategy')}"
        )
    else:
        st.sidebar.info("자동매매 정지 상태")

    c1, c2 = st.sidebar.columns(2)
    if c1.button("자동매매 시작", use_container_width=True):
        ok, msg = start_trade_runner(strategy_name, dry_run=run_dry)
        if ok:
            st.sidebar.success(msg)
        else:
            st.sidebar.error(msg)
        st.rerun()

    if c2.button("자동매매 중지", use_container_width=True):
        ok, msg = stop_trade_runner()
        if ok:
            st.sidebar.success(msg)
        else:
            st.sidebar.warning(msg)
        st.rerun()

    st.sidebar.divider()
    st.sidebar.header("수동 주문")
    manual_dry = st.sidebar.checkbox("수동주문 DRY_RUN", value=True)
    manual_market = st.sidebar.text_input("수동주문 마켓", value=default_market)
    buy_amount = st.sidebar.number_input(
        "매수 금액 (KRW)",
        min_value=5000.0,
        value=10000.0,
        step=1000.0,
    )
    sell_ratio = st.sidebar.slider("매도 비율(%)", min_value=1, max_value=100, value=100)

    b1, b2 = st.sidebar.columns(2)
    if b1.button("수동 매수", use_container_width=True):
        try:
            client = UpbitClient(dry_run=manual_dry)
            res = client.order_market_buy(market=manual_market, price=float(buy_amount))
            st.sidebar.success(f"매수 주문 성공: {res}")
        except Exception as e:
            st.sidebar.error(f"매수 주문 실패: {e}")

    if b2.button("수동 매도", use_container_width=True):
        try:
            client = UpbitClient(dry_run=manual_dry)
            currency = manual_market.split("-")[1] if "-" in manual_market else ""
            if not currency:
                st.sidebar.error("마켓 형식이 올바르지 않습니다. 예: KRW-BTC")
            else:
                total_volume = client.get_balance(currency)
                sell_volume = total_volume * (sell_ratio / 100.0)
                if sell_volume <= 0:
                    st.sidebar.warning("매도할 잔고가 없습니다.")
                else:
                    res = client.order_market_sell(market=manual_market, volume=sell_volume)
                    st.sidebar.success(f"매도 주문 성공: {res}")
        except Exception as e:
            st.sidebar.error(f"매도 주문 실패: {e}")


def main() -> None:
    st.set_page_config(page_title="Trading Dashboard", layout="wide")

    logs = list_log_files()
    if not logs:
        st.error("logs/ 디렉토리에 trade_*.log 파일이 없습니다.")
        return

    labels = [p.name for p in logs]
    selected = st.sidebar.selectbox("로그 선택", options=labels, index=0)
    market = st.sidebar.text_input("차트 마켓", value="KRW-BTC")
    unit = st.sidebar.selectbox("봉 단위(분)", options=[1, 3, 5, 15, 30, 60, 240], index=0)
    count = st.sidebar.slider("조회 캔들 수", min_value=50, max_value=500, value=200, step=10)
    render_controls(default_market=market)
    if st.sidebar.button("차트 새로고침"):
        st.cache_data.clear()
        st.rerun()
    auto_refresh = st.sidebar.checkbox("차트 자동 갱신", value=True)
    refresh_sec = st.sidebar.slider("자동 갱신(초)", min_value=1, max_value=30, value=1, step=1)

    log_path = next(p for p in logs if p.name == selected)

    def render_all() -> None:
        data = parse_log(log_path)
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
