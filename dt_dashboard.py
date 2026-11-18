"""
dt_dashboard.py - BTC 자율 에이전트(--mode btc-agent) 실시간 대시보드
=====================================================================
    ./venv/bin/python -m streamlit run dt_dashboard.py --server.port 8502

state/dt_live.json, logs/dt_*.{jsonl,csv,log} 를 읽어 5초마다 다시 그린다. 주문하지 않는다.
버튼은 state/dt_commands.txt 에 명령을 적고, 에이전트가 10초 안에 텔레그램 명령과 똑같이 처리한다.
다른 폴더(예: 가상 운전 결과)를 보려면 DT_DASH_STATE_DIR / DT_DASH_LOG_DIR 을 준다.

디자인: 인스타그램식 한 줄 피드. 카드 20px, 버튼·칩 완전 둥근 모양, 강조색은 파랑 하나, 그라데이션은 프로필·스토리 링에만.
표(st.dataframe)는 칸이 잘려서 쓰지 않고 줄바꿈되는 카드로 그린다.
"""

from __future__ import annotations

import html
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from bot import notifier
from bot.paths import PROJECT_ROOT
from bot.strategies import ko, ko_params, ko_sub
from bot.upbit_client import UpbitClient

KST = timezone(timedelta(hours=9))
STATE_DIR = os.getenv("DT_DASH_STATE_DIR", os.path.join(PROJECT_ROOT, "state"))
LOG_DIR = os.getenv("DT_DASH_LOG_DIR", os.path.join(PROJECT_ROOT, "logs"))
INBOX = os.path.join(STATE_DIR, "dt_commands.txt")
UP, DOWN = "#e8365d", "#3b82f6"            # 국내 관례: 상승 빨강, 하락 파랑
BUY_C, SELL_C = "#10b981", "#f97316"
LIB = ["sma_trend_daily", "rsi_momo_4h", "rsi_momo_1h", "breakout_vol_1h", "vb_daily", "vwap_revert",
       "discretionary", "none"]
EMOJI = {"rsi_momo_1h": "⚡", "rsi_momo_4h": "🔥", "breakout_vol_1h": "🚀", "sma_trend_daily": "📈",
         "vb_daily": "🎯", "vwap_revert": "🌊", "discretionary": "✋", "none": "💤", "hold": "🪙"}
DESC = {
    "rsi_momo_1h": "1시간봉에서 힘이 세지면(RSI 70 돌파) 올라타고, 힘이 빠지면(RSI 50 아래) 팝니다.",
    "rsi_momo_4h": "4시간봉에서 힘이 세지면(RSI 70 돌파) 올라타고, 힘이 빠지면(RSI 50 아래) 팝니다.",
    "breakout_vol_1h": "거래가 몰리면서 최근 24시간 최고가를 넘으면 사고, 최근 12시간 최저가를 깨면 팝니다.",
    "sma_trend_daily": "하루 종가가 50일 평균보다 위면 들고 있고, 아래로 내려가면 다음 날 아침 9시에 팝니다.",
    "vb_daily": "아침 9시 가격에서 전날 출렁인 폭의 절반 이상 오르면 사고, 다음 날 아침 9시에 팝니다.",
    "vwap_revert": "오늘 평균 거래가보다 1% 싸지면 지정가로 사고, 평균가로 돌아오면 팝니다.",
    "discretionary": "정해진 규칙 없이 AI가 근거를 대고 예산의 30% 안에서 직접 사고팝니다.",
    "none": "사지 않고 현금으로 기다립니다.",
    "hold": "아무것도 안 하고 BTC를 그냥 들고 있었을 때입니다. 비교 기준이에요.",
}
ACTION = {"follow_plan": "전략대로", "buy_now": "바로 매수", "sell_now": "바로 매도", "hold": "기다림"}
SOURCE = {"플랜": "전략 신호", "재량": "AI 판단", "손절": "손절", "최대보유": "보유 시간 초과", "킬": "전체 손실 한도",
          "수동": "직접 명령", "인계": "계좌에서 넘겨받음"}

CSS = """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.min.css');
:root {
  --bg: #fafafa; --card: #ffffff; --line: #ececec; --ink: #1f1f1f; --sub: #737373; --chip: #f2f2f2;
  --accent: #0095f6; --accent-soft: #e6f4fe; --good: #0e9f6e; --bad: #e5484d; --warn-bg: #fff4e5; --warn: #b45309;
  --ring: conic-gradient(from 210deg, #feda75, #fa7e1e, #d62976, #962fbf, #4f5bd5, #feda75);
  --shadow: 0 1px 2px rgba(17, 17, 26, .04), 0 6px 20px rgba(17, 17, 26, .05);
}
html, body, .stApp, .stMarkdown, .stMarkdown *, button p, input, textarea, select, label p {
  font-family: 'Pretendard Variable', Pretendard, -apple-system, 'Apple SD Gothic Neo', sans-serif !important;
}
header[data-testid="stHeader"], div[data-testid="stToolbar"], div[data-testid="stDecoration"],
section[data-testid="stSidebar"], div[data-testid="collapsedControl"] { display: none !important; }
.stApp { background: var(--bg); }
.block-container { max-width: 1160px !important; padding: 28px 20px 64px !important; }
/* 오른쪽 기록 칸: 스크롤해도 따라온다 */
div[data-testid="stColumn"]:has(.rail-mark) { position: sticky; top: 16px; align-self: flex-start;
  max-height: calc(100dvh - 32px); overflow-y: auto; scrollbar-width: thin; }
.rail-title { font-size: 14px; font-weight: 700; color: var(--ink); margin: 6px 4px 10px; }
div[data-testid="stColumn"]:has(.rail-mark) .post { padding: 12px 14px; }
div[data-testid="stColumn"]:has(.rail-mark) .post .bd { font-size: 12.5px; }
.sec { font-size: 14px; font-weight: 700; color: var(--ink); margin: 18px 4px 10px; }
.score-row { display: grid; grid-template-columns: 1fr auto auto; gap: 4px 12px; align-items: center;
             font-size: 12.5px; padding: 5px 0; color: var(--ink); }
.score-row .h { color: var(--sub); font-size: 11px; }
.stMarkdown p { font-size: 13.5px; line-height: 1.6; }
.stMarkdown { word-break: keep-all; overflow-wrap: anywhere; }

/* 프로필 */
.pf { display: flex; gap: 22px; align-items: center; margin-bottom: 18px; }
.ring { border-radius: 50%; background: var(--ring); padding: 3px; flex: none; }
.ring.off { background: var(--line); }
.ava { width: 100%; height: 100%; border-radius: 50%; background: var(--card); border: 3px solid var(--card);
       display: flex; align-items: center; justify-content: center; }
.pf .ring { width: 72px; height: 72px; }
.pf .ava { font-size: 28px; color: #f7931a; font-weight: 800; }
.pf-name { font-size: 17px; font-weight: 700; color: var(--ink); }
.pf-pills { display: flex; gap: 6px; flex-wrap: wrap; margin: 6px 0 10px; }
.pill { font-size: 11.5px; font-weight: 600; padding: 4px 10px; border-radius: 999px; background: var(--chip); color: var(--ink); white-space: nowrap; }
.pill.on { background: #dcfce7; color: #166534; }
.pill.live { background: #ffe4ec; color: #be185d; }
.pill.warn { background: var(--warn-bg); color: var(--warn); }
.pf-stats { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 4px 14px; }
@media (max-width: 900px) { .pf-stats { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.pf-stats b { display: block; font-size: 15px; font-weight: 700; color: var(--ink); }
.pf-stats span { font-size: 11.5px; color: var(--sub); }
.pos { color: var(--good) !important; } .neg { color: var(--bad) !important; }

/* 스토리 */
.stories { display: flex; gap: 14px; overflow-x: auto; padding: 14px 16px 10px; background: var(--card);
           border: 1px solid var(--line); border-radius: 20px; box-shadow: var(--shadow); margin-bottom: 6px;
           scrollbar-width: none; }
.stories::-webkit-scrollbar { display: none; }
.story { width: 76px; flex: none; text-align: center; }
.story .ring { width: 62px; height: 62px; margin: 0 auto; padding: 2.5px; }
.story .ava { font-size: 24px; border-width: 2.5px; }
.story .t { font-size: 11px; line-height: 1.3; color: var(--ink); margin-top: 6px; word-break: keep-all; }
.story .r { font-size: 11px; font-weight: 600; margin-top: 2px; color: var(--sub); }
.story.cur .t { font-weight: 700; }
.hint { font-size: 11.5px; color: var(--sub); line-height: 1.55; margin: 4px 4px 16px; }

/* 카드 */
.card { background: var(--card); border: 1px solid var(--line); border-radius: 20px; padding: 16px 18px;
        box-shadow: var(--shadow); margin-bottom: 12px; }
.card h4 { font-size: 13.5px; font-weight: 700; margin: 0 0 10px; color: var(--ink); }
.say { font-size: 14px; line-height: 1.6; color: var(--ink); }
.say b { font-weight: 700; }
.muted { font-size: 12.5px; color: var(--sub); line-height: 1.6; }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 6px 16px; font-size: 12.5px; margin-top: 10px; }
.kv span:nth-child(odd) { color: var(--sub); white-space: nowrap; }
.kv span:nth-child(even) { color: var(--ink); text-align: right; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; }
.chip { font-size: 11.5px; padding: 4px 10px; border-radius: 999px; background: var(--chip); color: var(--ink); }
.chip.blue { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
.chip.warn { background: var(--warn-bg); color: var(--warn); }

/* 피드 글 */
.post { background: var(--card); border: 1px solid var(--line); border-radius: 20px; box-shadow: var(--shadow);
        padding: 14px 16px; margin-bottom: 4px; }
.post .hd { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; }
.post .ring { width: 36px; height: 36px; padding: 2px; }
.post .ava { font-size: 16px; border-width: 2px; }
.post .who { font-size: 13px; font-weight: 700; color: var(--ink); }
.post .when { font-size: 11.5px; color: var(--sub); }
.post .bd { font-size: 13.5px; line-height: 1.65; color: var(--ink); }

/* 위젯 둥글게 */
.stButton button, .stPopover button { border-radius: 999px !important; font-weight: 600 !important; font-size: 13px !important;
  padding: 6px 14px !important; border: 1px solid var(--line) !important; background: var(--card) !important; color: var(--ink) !important;
  white-space: nowrap; transition: transform .12s ease; }
.stButton button:active { transform: scale(.97); }
.stButton button[kind="primary"] { background: var(--accent) !important; color: #fff !important; border-color: var(--accent) !important; }
div[data-testid="stExpander"] details { border-radius: 16px !important; border: 1px solid var(--line) !important; background: var(--card); }
div[data-testid="stExpander"] summary p { font-size: 12.5px !important; color: var(--sub); }
div[data-testid="stSegmentedControl"] button, div[data-baseweb="button-group"] button { border-radius: 999px !important; font-size: 12.5px !important; }
div[data-testid="stCode"] pre { border-radius: 14px !important; font-size: 11.5px !important; }
.stPlotlyChart { background: var(--card); border: 1px solid var(--line); border-radius: 20px; padding: 6px; box-shadow: var(--shadow); }
</style>
"""
DARK = """<style>:root{--bg:#0c0c0d;--card:#161618;--line:#2a2a2d;--ink:#f2f2f2;--sub:#a1a1a6;--chip:#232326;
--accent:#3aa7ff;--accent-soft:#0d2a40;--warn-bg:#3a2306;--warn:#fbbf24;--shadow:none}
.pill.on{background:#0f2e1d;color:#4ade80}.pill.live{background:#3b0d22;color:#f472b6}</style>"""


# ------------------------------------------------------------------ 읽기
def load_state() -> dict:
    try:
        with open(os.path.join(STATE_DIR, "dt_live.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def tail_lines(path: str, n: int) -> list[str]:
    """큰 jsonl 의 마지막 n 줄만 (판단 기록은 하루 수 MB 씩 쌓인다)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            end, buf = f.tell(), b""
            while end > 0 and buf.count(b"\n") <= n:
                step = min(1 << 20, end)
                end -= step
                f.seek(end)
                buf = f.read(step) + buf
    except OSError:
        return []
    return [x for x in buf.decode("utf-8", "ignore").splitlines() if x.strip()][-n:]


def load_decisions(n: int) -> list[dict]:
    out = []
    for line in tail_lines(os.path.join(LOG_DIR, "dt_decisions.jsonl"), n):
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def load_csv(name: str) -> pd.DataFrame:
    try:
        return pd.read_csv(os.path.join(LOG_DIR, name))
    except (OSError, pd.errors.EmptyDataError):
        return pd.DataFrame()


@st.cache_data(ttl=15)
def load_candles(unit: int, count: int) -> pd.DataFrame:
    c = UpbitClient(dry_run=True)
    ep = "/v1/candles/days" if unit == 1440 else f"/v1/candles/minutes/{unit}"
    df = pd.DataFrame(c._request("GET", ep, params={"market": "KRW-BTC", "count": count}))
    df["t"] = pd.to_datetime(df["candle_date_time_kst"])
    return df.sort_values("t")


def agent_running() -> bool:
    return subprocess.run(["pgrep", "-f", "main.py --mode btc-agent"], capture_output=True).returncode == 0


def won(x, sign=True) -> str:
    if x is None:
        return "-"
    return f"{x:+,.0f}원" if sign else f"{x:,.0f}원"


def tone(x) -> str:
    return "" if not x else ("pos" if x > 0 else "neg")


def esc(x) -> str:
    return html.escape(str(x or ""))


def ago(iso: str | None) -> str:
    if not iso:
        return "-"
    sec = (datetime.now(KST) - datetime.fromisoformat(iso)).total_seconds()
    if sec < 60:
        return "방금"
    if sec < 3600:
        return f"{sec / 60:.0f}분 전"
    if sec < 86400:
        return f"{sec / 3600:.0f}시간 전"
    return f"{sec / 86400:.0f}일 전"


def dark() -> bool:
    try:
        return st.context.theme.type == "dark"
    except Exception:
        return False


def ko_text(x) -> str:
    """AI 가 쓴 문장 속 전략 ID 를 한글 이름으로 바꾼 뒤 HTML 이스케이프."""
    return esc(ko_sub(str(x or "")))


def html_block(s: str) -> None:
    st.markdown(s, unsafe_allow_html=True)


# ------------------------------------------------------------------ 위쪽: 프로필, 버튼, 스토리, 지금
def profile(s: dict) -> None:
    running = agent_running()
    budget = s.get("budget_krw") or 0
    eq = s.get("equity") or 0
    total = (eq / budget - 1) * 100 if budget else 0
    l = s.get("llm", {})
    pills = [f'<span class="pill {"on" if running else ""}">{"실행 중" if running else "꺼져 있음"}</span>',
             f'<span class="pill {"live" if s.get("live") else ""}">{"실제 돈으로 거래" if s.get("live") else "연습 모드"}</span>']
    if s.get("llm_busy_since"):
        pills.append('<span class="pill">AI가 생각 중</span>')
    if str(s.get("llm_route_now", "")).startswith("api"):
        pills.append('<span class="pill warn">구독 한도 초과, API로 판단 중</span>')
    stops = [x for x in (s.get("halt"), s.get("weekly_stop") and "7일 손실 한도로 멈춤",
                         s.get("stop_file") and "새로 사지 않기 켜짐") if x]
    pills += [f'<span class="pill warn">{esc(x)}</span>' for x in stops]
    html_block(f"""
<div class="pf">
  <div class="ring {'' if running else 'off'}"><div class="ava">₿</div></div>
  <div style="flex:1;min-width:0">
    <div class="pf-name">BTC 자동매매 AI</div>
    <div class="pf-pills">{''.join(pills)}</div>
    <div class="pf-stats">
      <div><b>{eq:,.0f}원</b><span>지금 평가금액</span></div>
      <div><b class="{tone(total)}">{total:+.2f}%</b><span>처음 예산 대비</span></div>
      <div><b class="{tone(s.get('pnl_today'))}">{won(s.get('pnl_today'))}</b><span>오늘 손익</span></div>
      <div><b class="{tone(s.get('pnl_7d'))}">{won(s.get('pnl_7d'))}</b><span>7일 손익</span></div>
      <div><b>{(s.get('day') or {}).get('buys', 0)}번</b><span>오늘 매수{f" (최대 {s['max_buys_per_day']}번)" if s.get('max_buys_per_day') else " (제한 없음)"}</span></div>
      <div><b>{l.get('quick', 0) + l.get('deep', 0)}번</b><span>오늘 AI 판단</span></div>
    </div>
  </div>
</div>""")
    upd = s.get("updated_at")
    if running and upd and (datetime.now(KST) - datetime.fromisoformat(upd)).total_seconds() > 90:
        st.warning("화면 정보가 90초 넘게 새로 오지 않았어요. AI 판단이 오래 걸리거나 봇이 멈췄을 수 있어요.")


def actions() -> None:
    c = st.columns([1, 1, 1.25, 1])
    if c[0].button("새로 사지 않기", help="이미 산 BTC는 그대로 관리하고, 새 매수만 멈춥니다", width="stretch"):
        notifier.send_command("/dt_stop", path=INBOX); st.toast("10초 안에 새 매수를 멈춥니다")
    if c[1].button("다시 시작", help="멈춤 상태를 모두 풀고 다시 거래합니다", width="stretch"):
        notifier.send_command("/dt_go", path=INBOX); st.toast("10초 안에 다시 시작합니다")
    if c[2].button("텔레그램으로 상태", help="지금 상태를 텔레그램으로 보내 줍니다", width="stretch"):
        notifier.send_command("/dt_status", path=INBOX); st.toast("텔레그램으로 보냈어요")
    with c[3].popover("전부 팔기", width="stretch"):
        st.markdown("가진 BTC를 지금 시장가로 **전부 팔고**, 새 매수도 멈춥니다.")
        if st.button("네, 전부 팔게요", type="primary", width="stretch"):
            notifier.send_command("/dt_flat", "/dt_yes", path=INBOX); st.toast("10초 안에 전부 팝니다")
    if os.path.exists(INBOX) and not agent_running():
        st.caption("보낸 명령이 기다리는 중이에요. 봇이 꺼져 있어서 켜지면 처리됩니다.")


def stories(s: dict) -> None:
    sb = s.get("scoreboard") or {}
    cur = (s.get("plan") or {}).get("strategy")
    held = (s.get("position") or {}).get("strategy")
    items = []
    for sid in LIB + ["hold"]:
        v = sb.get(sid) or {}
        r = (v.get("d30") or {}).get("ret")
        lit = sid in (cur, held) or v.get("entry_signal_now")
        sub = f'<span class="{tone(r)}">{r:+.1f}%</span>' if r is not None else "&nbsp;"
        items.append(f'<div class="story {"cur" if sid in (cur, held) else ""}">'
                     f'<div class="ring {"" if lit else "off"}"><div class="ava">{EMOJI[sid]}</div></div>'
                     f'<div class="t">{ko(sid)}</div><div class="r">{sub}</div></div>')
    html_block(f'<div class="stories">{"".join(items)}</div>'
               '<div class="hint">색깔 링은 지금 쓰고 있거나 지금 살 신호가 난 전략이에요. '
               '숫자는 최근 30일 동안 그 전략대로 했다면 얻었을 수익률이에요 (실제 주문 없이 계산).</div>')


def now_card(s: dict, decs: list[dict]) -> None:
    p, pl = s.get("position"), s.get("plan") or {}
    last = next((r for r in reversed(decs) if r.get("decision")), None)
    reason = (last["decision"].get("reason") if last else "") or pl.get("reason")
    sid = pl.get("strategy")
    conf = (pl.get("confidence") or 0) * 100
    if p:
        px = s.get("price") or 0
        upct = (px * 0.9995 * p["volume"] / p["cost_krw"] - 1) * 100 if p.get("cost_krw") else 0
        held = (datetime.now(KST) - datetime.fromisoformat(p["entry_time"])).total_seconds() / 3600
        head = (f'BTC를 <b>{p["cost_krw"]:,.0f}원</b>어치 들고 있어요. 지금 '
                f'<b class="{tone(upct)}">{upct:+.2f}% ({won(s.get("unrealized"))})</b>, 산 지 {held:.1f}시간 됐어요.')
        kv = [("산 가격", f"{p['entry_price']:,.0f}원"), ("수량", f"{p['volume']:.8f} BTC"),
              ("관리 전략", ko(p["strategy"])), ("산 계기", SOURCE.get(p.get("source"), esc(p.get("source")))),
              ("손절가", f"{p['stop_price']:,.0f}원" if p.get("stop_price") else "없음 (전체 손실 한도만 적용)")]
    else:
        head = f'BTC는 없고 <b>현금 {s.get("equity") or 0:,.0f}원</b>으로 기다리는 중이에요.'
        kv = []
    if sid in ("none", None):
        plan = f"AI는 지금 <b>사지 않고 기다리기</b>로 정했어요. (확신 {conf:.0f}%)"
    else:
        plan = (f'AI는 지금 <b>{EMOJI.get(sid, "")} {ko(sid)}</b> 전략을 쓰고 있어요. (확신 {conf:.0f}%) '
                f'신호가 나면 예산의 {pl.get("size_pct", 0):.0f}%로 사고, {pl.get("stop_loss_pct")}% 떨어지면 손절해요.')
        kv.append(("전략 설정", esc(ko_params(pl.get("params")))))
    o = s.get("pending")
    if o:
        kv.append(("걸어 둔 주문", f"{o['price']:,}원에 {'매수' if o['side'] == 'bid' else '매도'} 대기 ({ago(o['placed_at'])})"))
    nxt = s.get("next_check_at")
    kv.append(("다음 AI 점검", f"{nxt[11:16] if nxt else '-'} ({s.get('next_interval_min') or '-'}분마다)"))
    kv.append(("BTC 지금 가격", f"{s.get('price', 0):,.0f}원"))
    why = (f'<div class="muted" style="margin-top:8px">마지막 판단 ({ago(last["time"]) if last else "-"}): '
           f'{ko_text(reason)}</div>') if reason else ""
    rows = "".join(f"<span>{k}</span><span>{v}</span>" for k, v in kv)
    html_block(f'<div class="card"><h4>지금 상황</h4><div class="say">{head}<br>{plan}</div>{why}'
               f'<div class="kv">{rows}</div></div>')


# ------------------------------------------------------------------ 피드
def post_html(r: dict) -> str:
    d, q = r.get("decision") or {}, r.get("quick") or {}
    deep = bool(d)
    calls = r.get("calls") or []
    when = f"{ago(r['time'])} · {r['time'][11:16]}"
    chips = []
    if deep:
        sid = (r.get("plan_after") or {}).get("strategy")
        who = "AI 깊은 판단"
        body = d.get("reason") or (r.get("notes") or ["판단을 읽지 못해 그대로 유지했어요"])[0]
        chips += [f'<span class="chip">{esc(d.get("regime") or "흐름 판단 없음")}</span>',
                  f'<span class="chip">{EMOJI.get(sid, "")} {ko(sid)}</span>',
                  f'<span class="chip {"blue" if d.get("action") in ("buy_now", "sell_now") else ""}">'
                  f'{ACTION.get(d.get("action"), esc(d.get("action")))}</span>',
                  f'<span class="chip">확신 {d.get("confidence", 0) * 100:.0f}%</span>']
    else:
        who = "AI 빠른 점검"
        body = q.get("reason") or "점검 결과를 읽지 못했어요"
        esc_up = q.get("verdict") == "escalate" or q.get("urgent_sell")
        chips.append(f'<span class="chip {"blue" if esc_up else ""}">'
                     f'{"깊은 판단 요청" if esc_up else "지금 전략 그대로"}</span>')
        if q.get("urgent_sell"):
            chips.append('<span class="chip warn">급하게 팔지 검토</span>')
    for rj in (r.get("rejected") or []):
        chips.append(f'<span class="chip warn">막힘: {esc(rj)}</span>')
    if r.get("deep_skipped"):
        chips.append(f'<span class="chip warn">{esc(r["deep_skipped"])}</span>')
    sec = sum(c.get("sec") or 0 for c in calls)
    route = "API" if any(c.get("route") == "api" for c in calls) else "구독"
    chips.append(f'<span class="chip">{route} {sec:.0f}초</span>')
    wake = ", ".join(r.get("wake") or [])
    return (f'<div class="post"><div class="hd"><div class="ring {"" if deep else "off"}"><div class="ava">'
            f'{"🧠" if deep else "👀"}</div></div><div><div class="who">{who}</div>'
            f'<div class="when">{when} · BTC {r.get("price", 0):,.0f}원 · 계기: {esc(wake)}</div></div></div>'
            f'<div class="bd">{ko_text(body)}</div><div class="chips">{"".join(chips)}</div></div>')


def feed(decs: list[dict]) -> None:
    if not decs:
        html_block('<div class="card"><div class="say">아직 AI 판단 기록이 없어요.</div>'
                   '<div class="muted">봇이 켜지면 바로 첫 판단을 하고, 그 뒤로 1~5분마다 점검해요.</div></div>')
        return
    only_deep = st.toggle("깊은 판단만 보기", value=False, key="only_deep")
    shown = [r for r in reversed(decs) if r.get("decision") or not only_deep][:25]
    for r in shown[:5]:
        _post_with_detail(r)
    if len(shown) > 5:
        with st.expander(f"이전 판단 {len(shown) - 5}개 더 보기"):
            for r in shown[5:]:
                html_block(post_html(r))     # 펼치기 안에는 펼치기를 못 넣는다 (Streamlit 제한)


def _post_with_detail(r: dict) -> None:
    html_block(post_html(r))
    with st.expander("AI 답변 원문과 본 자료"):
        st.code(r.get("raw") or json.dumps(r.get("quick"), ensure_ascii=False, indent=1),
                language="json", wrap_lines=True)
        if r.get("notes"):
            st.caption("범위를 벗어나 고친 값: " + ", ".join(map(str, r["notes"])))
        st.caption(f"AI가 본 시장 자료 {r.get('snapshot_chars', 0):,}자")
        st.json(r.get("snapshot") or {}, expanded=False)


# ------------------------------------------------------------------ 차트
def style_fig(fig: go.Figure, height: int) -> go.Figure:
    ink, grid = ("#a1a1a6", "#2a2a2d") if dark() else ("#8a8a8a", "#f1f1f1")
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=12, b=8), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Pretendard Variable, sans-serif", size=11, color=ink),
                      hoverlabel=dict(font_size=12), legend=dict(orientation="h", y=1.08, x=0, font=dict(size=11)),
                      xaxis_rangeslider_visible=False, hovermode="x unified")
    fig.update_xaxes(gridcolor=grid, zeroline=False, showline=False)
    fig.update_yaxes(gridcolor=grid, zeroline=False, showline=False, tickformat=",.0f", side="right")
    return fig


def chart(s: dict) -> None:
    unit = st.segmented_control("봉", [15, 60, 240, 1440], default=60, key="unit", label_visibility="collapsed",
                                format_func=lambda u: {15: "15분", 60: "1시간", 240: "4시간", 1440: "하루"}[u]) or 60
    try:
        c = load_candles(unit, 100)
    except Exception as e:
        st.error(f"가격을 불러오지 못했어요: {e}")
        return
    fig = go.Figure(go.Candlestick(
        x=c["t"], open=c["opening_price"], high=c["high_price"], low=c["low_price"], close=c["trade_price"],
        name="가격", showlegend=False,
        increasing=dict(line=dict(color=UP, width=1), fillcolor=UP),
        decreasing=dict(line=dict(color=DOWN, width=1), fillcolor=DOWN)))
    tr = load_csv("dt_trades.csv")
    if not tr.empty:
        tr["t"] = pd.to_datetime(tr["time"]).dt.tz_localize(None)
        tr = tr[tr["t"] >= c["t"].min()]
        for side, sym, col in (("매수", "triangle-up", BUY_C), ("매도", "triangle-down", SELL_C)):
            d = tr[tr["side"] == side]
            if len(d):
                fig.add_trace(go.Scatter(
                    x=d["t"], y=d["avg_price"], mode="markers+text", name=side, text=[side] * len(d),
                    textposition="bottom center" if side == "매수" else "top center", textfont=dict(size=10),
                    marker=dict(symbol=sym, size=12, color=col, line=dict(width=2, color="white")),
                    customdata=d.assign(st=d["strategy"].map(ko))[["st", "pnl_krw"]],
                    hovertemplate=side + " %{y:,.0f}원<br>%{customdata[0]} · 손익 %{customdata[1]:,}원<extra></extra>"))
    p = s.get("position")
    if p:
        fig.add_hline(y=p["entry_price"], line=dict(color=BUY_C, width=1, dash="dot"),
                      annotation_text=f"산 가격 {p['entry_price']:,.0f}", annotation_font_size=10)
        if p.get("stop_price"):
            fig.add_hline(y=p["stop_price"], line=dict(color=SELL_C, width=1, dash="dot"),
                          annotation_text=f"손절가 {p['stop_price']:,.0f}", annotation_font_size=10,
                          annotation_position="bottom left")
    st.plotly_chart(style_fig(fig, 280), width="stretch", config={"displayModeBar": False})
    st.caption("빨간 봉은 오른 구간, 파란 봉은 내린 구간이에요. 삼각형은 AI가 실제로 사고판 곳이에요.")

    eq = load_csv("dt_equity.csv")
    if len(eq) > 1:
        eq["t"] = pd.to_datetime(eq["time"]).dt.tz_localize(None)
        hold = eq["equity"].iloc[0] * eq["price"] / eq["price"].iloc[0]
        f2 = go.Figure()
        f2.add_trace(go.Scatter(x=eq["t"], y=eq["equity"], name="AI", line=dict(width=2.2, color="#d62976"),
                                hovertemplate="%{y:,.0f}원<extra>AI</extra>"))
        f2.add_trace(go.Scatter(x=eq["t"], y=hold, name="그냥 BTC를 샀다면", line=dict(width=1.5, color="#a1a1a6", dash="dot"),
                                hovertemplate="%{y:,.0f}원<extra>그냥 샀다면</extra>"))
        lo, hi = min(eq["equity"].min(), hold.min()), max(eq["equity"].max(), hold.max())
        pad = max((hi - lo) * 0.2, hi * 0.002)
        f2.update_yaxes(range=[lo - pad, hi + pad])
        html_block('<div class="muted" style="margin:14px 4px 6px">내 돈은 어떻게 변했나 (AI vs 그냥 BTC를 샀다면)</div>')
        st.plotly_chart(style_fig(f2, 170), width="stretch", config={"displayModeBar": False})


# ------------------------------------------------------------------ 거래, 전략 성적, 교훈
def trades(s: dict) -> None:
    tr = load_csv("dt_trades.csv")
    if tr.empty:
        html_block('<div class="card"><div class="say">아직 사고판 기록이 없어요.</div>'
                   '<div class="muted">AI가 살 만한 신호를 기다리는 중이에요. 하락장에서는 며칠 동안 안 살 수도 있어요.</div></div>')
    else:
        sells = tr[tr["side"] == "매도"]
        win = f"{(sells['pnl_krw'] > 0).mean() * 100:.0f}%" if len(sells) else "-"
        html_block(f'<div class="card"><h4>지금까지</h4><div class="chips">'
                   f'<span class="chip">판 횟수 {len(sells)}번</span><span class="chip">이긴 비율 {win}</span>'
                   f'<span class="chip">확정 손익 {sells["pnl_krw"].sum():+,.0f}원</span>'
                   f'<span class="chip">수수료 {tr["fee"].sum():,.0f}원</span></div></div>')
        for t in tr.iloc[::-1].head(30).itertuples():
            buy = t.side == "매수"
            pnl = "" if buy else f' · <b class="{tone(t.pnl_krw)}">{t.pnl_krw:+,.0f}원</b>'
            html_block(f'<div class="post"><div class="hd"><div class="ring {"" if buy else "off"}"><div class="ava">'
                       f'{"🟢" if buy else "🔵"}</div></div><div><div class="who">{t.side} {t.krw:,.0f}원{pnl}</div>'
                       f'<div class="when">{t.time[5:16].replace("T", " ")} · 평균 {t.avg_price:,.0f}원 · '
                       f'{ko(t.strategy)} · {SOURCE.get(t.source, esc(t.source))}</div></div></div>'
                       f'<div class="bd">{ko_text(t.reason)}</div></div>')
    rc = s.get("reject_counts") or {}
    if rc:
        rows = "".join(f"<span>{esc(k)}</span><span>{v}번</span>" for k, v in sorted(rc.items(), key=lambda x: -x[1])[:8])
        html_block(f'<div class="card" style="margin-top:12px"><h4>안전장치가 막은 매수</h4><div class="kv">{rows}</div></div>')


def scores(s: dict) -> None:
    sb = s.get("scoreboard") or {}
    if not sb:
        html_block('<div class="card"><div class="say">전략 성적을 아직 계산하지 못했어요.</div></div>')
        return
    html_block('<div class="hint">각 전략을 기본 설정 그대로 따랐다면 냈을 성적이에요. 실제 주문 없이 계산했고, '
               '한 번 사고팔 때마다 수수료와 체결 차이로 0.1%씩 뺐어요.</div>')
    cur = (s.get("plan") or {}).get("strategy")
    for sid in LIB + ["hold"]:
        v = sb.get(sid)
        if not v:
            continue
        chips = []
        for w, label in (("d7", "7일"), ("d30", "30일"), ("d90", "90일")):
            x = v.get(w) or {}
            if x.get("ret") is not None:
                n = f", {x['trades']}번 거래" if sid != "hold" and x.get("trades") is not None else ""
                chips.append(f'<span class="chip">{label} <b class="{tone(x["ret"])}">{x["ret"]:+.1f}%</b>{n}</span>')
        d90 = v.get("d90") or {}
        if d90.get("mdd") is not None:
            chips.append(f'<span class="chip">90일 중 가장 크게 빠진 폭 {d90["mdd"]:.1f}%</span>')
        if v.get("entry_signal_now"):
            chips.append('<span class="chip blue">지금 살 신호</span>')
        if sid == cur:
            chips.append('<span class="chip blue">AI가 지금 쓰는 중</span>')
        html_block(f'<div class="post"><div class="hd"><div class="ring {"" if sid == cur else "off"}"><div class="ava">'
                   f'{EMOJI[sid]}</div></div><div><div class="who">{ko(sid)}</div></div></div>'
                   f'<div class="bd">{DESC[sid]}</div><div class="chips">{"".join(chips)}</div></div>')


def scores_compact(s: dict) -> None:
    sb = s.get("scoreboard") or {}
    if not sb:
        return
    cur = (s.get("plan") or {}).get("strategy")
    rows = ['<div class="score-row"><span class="h">각 전략대로 했다면</span><span class="h">30일</span>'
            '<span class="h">90일</span></div>']
    for sid in LIB + ["hold"]:
        v = sb.get(sid)
        if not v:
            continue
        a, b = (v.get("d30") or {}).get("ret"), (v.get("d90") or {}).get("ret")
        name = f'{EMOJI[sid]} {ko(sid)}' + (' <span class="chip blue">사용 중</span>' if sid == cur else "") + \
            (' <span class="chip blue">살 신호</span>' if v.get("entry_signal_now") else "")
        cell = lambda x: f'<b class="{tone(x)}">{x:+.1f}%</b>' if x is not None else "-"
        rows.append(f'<div class="score-row"><span>{name}</span><span>{cell(a)}</span><span>{cell(b)}</span></div>')
    html_block(f'<div class="card"><h4>전략 성적</h4>{"".join(rows)}'
               '<div class="muted" style="margin-top:6px">실제 주문 없이 계산. 수수료·체결 차이로 한 번에 0.1% 뺌.</div></div>')


def lessons() -> None:
    try:
        txt = open(os.path.join(STATE_DIR, "dt_lessons.md"), encoding="utf-8").read().strip()
    except OSError:
        txt = ""
    body = esc(txt).replace("\n", "<br>") if txt else "아직 없어요. 매일 아침 9시 5분에 AI가 어제 판단을 돌아보고 여기에 적어요."
    html_block(f'<div class="card"><h4>AI가 배운 점</h4><div class="muted">{body}</div></div>')
    rv = [json.loads(x) for x in tail_lines(os.path.join(LOG_DIR, "dt_reviews.jsonl"), 1)]
    if rv:
        r = rv[-1]["review"]
        li = lambda xs: "".join(f"<li>{esc(x)}</li>" for x in xs)
        html_block(f'<div class="card"><h4>어제 돌아보기 ({rv[-1]["time"][5:10]})</h4><div class="muted">'
                   f'<b>잘한 판단</b><ul>{li(r.get("good", []))}</ul><b>아쉬운 판단</b><ul>{li(r.get("bad", []))}</ul>'
                   f'<b>흐름별로 통한 전략</b><br>{esc(r.get("strategy_notes", ""))}</div></div>')
    with st.expander("개발자용 로그"):
        day = datetime.now(KST).strftime("%Y-%m-%d")
        st.code("\n".join(reversed(tail_lines(os.path.join(LOG_DIR, f"dt_{day}.log"), 120))) or "(로그 없음)",
                language="log")


# ------------------------------------------------------------------ 페이지
def main() -> None:
    st.set_page_config(page_title="BTC 자동매매 AI", page_icon="₿", layout="wide",
                       initial_sidebar_state="collapsed")
    html_block(CSS + (DARK if dark() else ""))

    @st.fragment(run_every="5s")
    def live() -> None:
        s = load_state()
        if not s:
            html_block('<div class="card"><div class="say">봇이 아직 한 번도 실행되지 않았어요.</div>'
                       '<div class="muted">터미널에서 <code>BOT_LOG_PREFIX=dt ./venv/bin/python main.py --mode btc-agent</code> '
                       '로 켜면 여기에 바로 나타나요.</div></div>')
            return
        decs = load_decisions(120)
        main_col, rail = st.columns([1.75, 1], gap="large")
        with main_col:
            profile(s)
            actions()
            stories(s)
            now_card(s, decs)
            html_block('<div class="sec">가격과 매매 위치</div>')
            chart(s)
            html_block('<div class="sec">AI 판단</div>')
            feed(decs)
        with rail:
            html_block('<span class="rail-mark"></span><div class="sec" style="margin-top:6px">사고판 기록</div>')
            trades(s)
            scores_compact(s)
            lessons()

    live()


if __name__ == "__main__":
    main()
