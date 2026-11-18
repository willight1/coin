"""
config.py — 설정 관리 모듈
=========================
.env 파일에서 환경변수를 로드하고, 프로젝트 전체에서 사용하는
설정값을 dataclass 기반 객체로 제공합니다.

모든 설정은 이 파일에서 중앙 관리됩니다.
다른 모듈에서 직접 os.environ 을 읽지 마세요.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

# .env 파일 로드 (프로젝트 루트 기준)
load_dotenv()


# ============================================================
# 업비트 API 설정
# ============================================================
@dataclass(frozen=True)
class UpbitConfig:
    """업비트 API 접속에 필요한 설정"""
    access_key: str = field(default="", repr=False)      # repr=False: 오류 메시지·로그에 설정이 찍혀도 키는 안 나오게
    secret_key: str = field(default="", repr=False)
    market: str = "KRW-BTC"            # 거래 대상 마켓
    markets: tuple[str, ...] = ("KRW-BTC",)  # 거래 대상 마켓 목록
    base_url: str = "https://api.upbit.com"
    timeout: int = 10                   # 요청 타임아웃 (초)
    max_retries: int = 3                # 최대 재시도 횟수


# ============================================================
# 봇 기본 설정
# ============================================================
# 업비트가 지원하는 분봉 단위. 1440 은 일봉(/v1/candles/days) — 분 단위로 두면
# 봉 길이 계산(unit * 60초)이 모든 경로에서 그대로 맞는다.
DAY_CANDLE_UNIT = 1440

# --strategy 없이 trade 모드를 실행할 때 쓰는 전략 (대시보드 신호 표시도 이것)
DEFAULT_TRADE_STRATEGY = "일봉 SMA50 추세"
SUPPORTED_CANDLE_UNITS = (1, 3, 5, 10, 15, 30, 60, 240, DAY_CANDLE_UNIT)


@dataclass(frozen=True)
class BotConfig:
    """봇 실행에 필요한 기본 설정"""
    interval_seconds: int = 10          # 시세 조회 주기 (초)
    dry_run: bool = True                # True면 실제 주문 실행 안 함
    log_level: str = "INFO"             # 로그 레벨
    candle_unit: int = DAY_CANDLE_UNIT  # 분봉 단위(1440=일봉). 백테스트/검증/실거래가 같은 값을 쓴다.


# ============================================================
# 백테스트 설정
# ============================================================
@dataclass(frozen=True)
class BacktestConfig:
    """백테스트 시뮬레이션 설정"""
    initial_capital: float = 1_000_000  # 초기 자본 (KRW)
    fee_rate: float = 0.0005            # 수수료율 (0.05%)
    slippage_rate: float = 0.0005       # 슬리피지율 (0.05%)


# ============================================================
# 리스크 관리 설정
# ============================================================
@dataclass(frozen=True)
class RiskConfig:
    """리스크 관리에 필요한 설정"""
    buy_ratio_pct: float = 0.30         # 잔고 대비 매수 비율 (30%)
    min_krw_reserve: float = 5_000      # 최소 KRW 잔고 유지
    stop_loss_pct: float = 0.03         # 손절 퍼센트 (3%)
    take_profit_pct: float = 0.0        # 고정 익절 퍼센트 (기본 비활성)
    trailing_stop_pct: float = 0.02     # 트레일링 스탑 퍼센트 (2%)
    min_net_profit_pct: float = 0.25    # 추세매도 최소 순이익 기준 (%)
    max_chase_pct: float = 0.004        # 신호봉 대비 최대 추격매수 허용폭 (0.4%)
    max_positions: int = 1              # 최대 동시 포지션 수 (0 이하면 제한 없음)
    cooldown_seconds: int = 300         # 주문 후 쿨다운 (초)
    max_consecutive_buy_signals: int = 2  # 연속 BUY 허용 횟수(2면 3번째부터 차단)
    atr_reduce_mult: float = 2.0        # ATR 고변동 축소 진입 배수
    atr_block_mult: float = 2.8         # ATR 초고변동 차단 배수
    high_vol_buy_scale: float = 0.5     # 고변동 구간 매수금액 축소 비율


# ============================================================
# BTC 자율 에이전트 (--mode btc-agent, docs/BTC_DAYTRADE.md 7장). 에이전트(LLM)가 바꿀 수 없는 한도.
# ============================================================
@dataclass(frozen=True)
class DtConfig:
    budget_krw: float = 0.0             # 필수. 원화 잔고가 더 많아도 이 값까지만 쓴다
    live: bool = False                  # DT_LIVE_CONFIRM=yes 일 때만 실제 주문
    # LLM 경로 (docs/BTC_DAYTRADE_LLM_CACHE.md): 1 = Max 구독 claude -p, 2 = ANTHROPIC_API_KEY + 캐싱
    llm_primary: str = "claude_cli"     # claude_cli | api
    llm_fallback: str = "api"           # 반대쪽 경로로 넘어가기 | none
    quick_model: str = "claude-haiku-5-5"   # 빠른 점검 (keep / escalate)
    deep_model: str = "claude-sonnet-5-5"   # 깊은 판단 (4장 형식). 경로 1·2 모두 이 두 값을 쓴다
    cli_workdir: str = os.path.expanduser("~/orca/dt_llm_empty")
    cli_timeout_sec: int = 120
    fallback_hold_min: float = 60.0
    check_hot_min: float = 1.0          # 신호 임박·발생 후 30분
    check_holding_min: float = 2.0      # 보유 중
    check_idle_min: float = 5.0         # 현금이고 조용함
    max_quick_calls_per_day: int = 800
    max_deep_calls_per_day: int = 120   # 넘으면 현재 플랜만 실행 (보유 중 urgent_sell 은 예외)
    api_daily_budget_usd: float = 3.0   # 경로 2 하루 비용. 넘으면 빠른 점검 간격 2배
    api_total_budget_usd: float = 0.0   # 경로 2 누적 비용 상한. 다 쓰면 구독으로 (0 = 상한 없음)
    api_reset_day: int = 0              # 매달 이 날(1~28) 0시에 누적 비용을 0 으로 (월간 크레딧 충전일). 0 = 안 지움
    max_buys_per_day: int = 6           # 0 = 제한 없음 (청산 후 쿨다운은 그대로)
    daily_loss_pct: float = 3.0
    weekly_loss_pct: float = 8.0
    kill_drawdown_pct: float = 15.0
    stop_min_pct: float = 1.5
    stop_max_pct: float = 5.0
    max_hold_hours: float = 96.0
    cooldown_min: float = 30.0
    max_spread_bp: float = 5.0
    min_confidence: float = 0.6
    discretionary_max_pct: float = 30.0
    min_plan_hold_min: float = 60.0
    wake_move_pct: float = 1.0
    # 50일선 봇과 같은 계좌를 쓰므로, 봇 기록 없는 BTC 를 넘겨받으면 50일선 봇 물량을 가져간다. 기본 끔
    adopt_account_btc: bool = False


def load_dt_config() -> "DtConfig":
    g = os.getenv
    return DtConfig(
        budget_krw=_float_env("DT_BUDGET_KRW", 0.0),
        live=g("DT_LIVE_CONFIRM", "").strip().lower() == "yes",
        llm_primary=g("DT_LLM_PRIMARY", "claude_cli").strip() or "claude_cli",
        llm_fallback=g("DT_LLM_FALLBACK", "api").strip() or "api",
        quick_model=g("DT_QUICK_MODEL", "claude-haiku-5-5").strip() or "claude-haiku-5-5",
        deep_model=g("DT_DEEP_MODEL", "claude-sonnet-5-5").strip() or "claude-sonnet-5-5",
        cli_workdir=os.path.expanduser(g("DT_CLI_WORKDIR", "~/orca/dt_llm_empty").strip() or "~/orca/dt_llm_empty"),
        cli_timeout_sec=_int_env("DT_CLI_TIMEOUT_SEC", 120),
        fallback_hold_min=_float_env("DT_FALLBACK_HOLD_MIN", 60.0),
        check_hot_min=_float_env("DT_CHECK_HOT_MIN", 1.0),
        check_holding_min=_float_env("DT_CHECK_HOLDING_MIN", 2.0),
        check_idle_min=_float_env("DT_CHECK_IDLE_MIN", 5.0),
        max_quick_calls_per_day=_int_env("DT_MAX_QUICK_CALLS_PER_DAY", 800),
        max_deep_calls_per_day=_int_env("DT_MAX_DEEP_CALLS_PER_DAY", 120),
        api_daily_budget_usd=_float_env("DT_API_DAILY_BUDGET_USD", 3.0),
        api_total_budget_usd=_float_env("DT_API_TOTAL_BUDGET_USD", 0.0),
        api_reset_day=int(_float_env("DT_API_RESET_DAY", 0)),
        max_buys_per_day=_int_env("DT_MAX_BUYS_PER_DAY", 6),
        daily_loss_pct=_float_env("DT_DAILY_LOSS_PCT", 3.0),
        weekly_loss_pct=_float_env("DT_WEEKLY_LOSS_PCT", 8.0),
        kill_drawdown_pct=_float_env("DT_KILL_DRAWDOWN_PCT", 15.0),
        stop_min_pct=_float_env("DT_STOP_MIN_PCT", 1.5),
        stop_max_pct=_float_env("DT_STOP_MAX_PCT", 5.0),
        max_hold_hours=_float_env("DT_MAX_HOLD_HOURS", 96.0),
        cooldown_min=_float_env("DT_COOLDOWN_MIN", 30.0),
        max_spread_bp=_float_env("DT_MAX_SPREAD_BP", 5.0),
        min_confidence=_float_env("DT_MIN_CONFIDENCE", 0.6),
        discretionary_max_pct=_float_env("DT_DISCRETIONARY_MAX_PCT", 30.0),
        min_plan_hold_min=_float_env("DT_MIN_PLAN_HOLD_MIN", 60.0),
        wake_move_pct=_float_env("DT_WAKE_MOVE_PCT", 1.0),
        adopt_account_btc=_bool_env("DT_ADOPT_ACCOUNT_BTC", False),
    )



@dataclass
class HlConfig:
    """하이퍼리퀴드 추세 봇(--mode hl-agent). 신호·근거는 bot/hl_signals.py 와 strategy-research 스킬."""
    budget_usd: float = 1000.0          # 가상 모드 시작 자산(USD). 실거래는 계좌 평가금액을 쓴다
    coins: tuple[str, ...] = ("BTC", "ETH")   # 자산을 코인 수로 똑같이 나눠 각각 독립 운용
    target_vol: float = 0.60            # 목표 연변동성. 배수 = 이 값 ÷ 최근 20일 실현변동성
    max_lev: float = 3.0                # 배수 상한 (측정상 3→10 으로 올려도 결과 같음). 거래소 상한(BTC 40·ETH 25)을 넘으면 거기서 잘림
    min_lev: float = 0.0                # 배수 하한. 0 = 없음. 2 로 두면 MDD 47% → 89% (strategy-research)
    kill_drawdown_pct: float = 60.0     # 시작 자산 대비 평가금액 하락 → 전량 청산 후 정지. 0 = 끔
    maker_wait_sec: float = 300.0       # 지정가 우선: 재조정 주문을 지정가로 걸고 이 시간(초) 뒤 남은 건 시장가. 0 = 바로 시장가 (maker_fill.py: 5분이면 90% 체결)
    maker_only: bool = False            # True 면 지정가로 못 채운 '늘리는' 수량을 시장가로 잇지 않는다(줄이는 수량은 시장가). HL_MAKER_ONLY (환경 기본 1)
    ai_shadow: bool = True              # AI 운용역 그림자 기록(bot/hl_ai.py). 매매에는 안 쓴다. HL_AI_SHADOW=0 으로 끔
    private_key: str = field(default="", repr=False)   # HL_PRIVATE_KEY. 있어야 실거래 (API 지갑 키 권장: 출금 불가)
    wallet: str = ""                    # HL_WALLET_ADDRESS. API 지갑을 쓰면 본 지갑 주소
    live: bool = False                  # HL_LIVE_CONFIRM=yes + 키 있을 때만 실제 주문


def load_hl_config() -> "HlConfig":
    g = os.getenv
    key = g("HL_PRIVATE_KEY", "").strip()
    twin = bool(g("HL_INSTANCE", "").strip())                 # 연습용 쌍둥이(--mode hl-paper): 실주문·AI 그림자 없음
    return HlConfig(
        budget_usd=_float_env("HL_BUDGET_USD", 1000.0),
        coins=tuple(c.strip().upper() for c in g("HL_COINS", "BTC,ETH").split(",") if c.strip()),
        target_vol=_float_env("HL_TARGET_VOL", 0.60),
        max_lev=_float_env("HL_MAX_LEV", 3.0),
        min_lev=_float_env("HL_MIN_LEV", 0.0),
        kill_drawdown_pct=_float_env("HL_KILL_DRAWDOWN_PCT", 60.0),
        maker_wait_sec=_float_env("HL_MAKER_WAIT_SEC", 300.0),
        maker_only=g("HL_MAKER_ONLY", "1").strip() != "0",
        ai_shadow=g("HL_AI_SHADOW", "1").strip() != "0" and not twin,
        private_key=key, wallet=g("HL_WALLET_ADDRESS", "").strip(),
        live=bool(key) and g("HL_LIVE_CONFIRM", "").strip().lower() == "yes" and not twin,
    )

# ============================================================
# 환경변수에서 설정 로드
# ============================================================
def _bool_env(key: str, default: bool = True) -> bool:
    """환경변수를 bool로 변환 (true/false/1/0 지원)"""
    val = os.getenv(key, str(default)).strip().lower()
    return val in ("true", "1", "yes")


def _float_env(key: str, default: float) -> float:
    """환경변수를 float로 변환"""
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _int_env(key: str, default: int) -> int:
    """환경변수를 int로 변환"""
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _candle_unit_env(key: str, default: int) -> int:
    """분봉 단위 환경변수를 파싱합니다. 업비트가 지원하지 않는 값이면 기본값을 씁니다."""
    unit = _int_env(key, default)
    return unit if unit in SUPPORTED_CANDLE_UNITS else default


def _markets_env(key: str, default_market: str) -> tuple[str, ...]:
    """콤마 구분 마켓 목록 환경변수를 파싱합니다."""
    raw = os.getenv(key, "").strip()
    if not raw:
        return (default_market,)
    markets = tuple(
        m.strip().upper()
        for m in raw.split(",")
        if m.strip()
    )
    return markets or (default_market,)


def load_config():
    """
    .env 환경변수를 읽어서 설정 객체들을 생성합니다.

    Returns:
        tuple: (UpbitConfig, BotConfig, BacktestConfig, RiskConfig)
    """
    markets = _markets_env("UPBIT_MARKETS", "KRW-BTC")

    upbit_cfg = UpbitConfig(
        access_key=os.getenv("UPBIT_ACCESS_KEY", ""),
        secret_key=os.getenv("UPBIT_SECRET_KEY", ""),
        market=os.getenv("UPBIT_MARKET", markets[0]),
        markets=markets,
    )

    bot_cfg = BotConfig(
        interval_seconds=_int_env("BOT_INTERVAL_SECONDS", 10),
        dry_run=_bool_env("BOT_DRY_RUN", True),
        log_level=os.getenv("BOT_LOG_LEVEL", "INFO"),
        candle_unit=_candle_unit_env("BOT_CANDLE_UNIT", DAY_CANDLE_UNIT),
    )

    bt_cfg = BacktestConfig(
        initial_capital=_float_env("INITIAL_CAPITAL", 1_000_000),
        fee_rate=_float_env("FEE_RATE", 0.0005),
        slippage_rate=_float_env("SLIPPAGE_RATE", 0.0005),
    )

    risk_cfg = RiskConfig(
        buy_ratio_pct=_float_env("BUY_RATIO_PCT", 0.30),
        min_krw_reserve=_float_env("MIN_KRW_RESERVE", 5_000),
        stop_loss_pct=_float_env("STOP_LOSS_PCT", 0.03),
        take_profit_pct=_float_env("TAKE_PROFIT_PCT", 0.0),
        trailing_stop_pct=_float_env("TRAILING_STOP_PCT", 0.02),
        min_net_profit_pct=_float_env("MIN_NET_PROFIT_PCT", 0.25),
        max_chase_pct=_float_env("MAX_CHASE_PCT", 0.004),
        max_positions=_int_env("MAX_POSITIONS", 1),
        cooldown_seconds=_int_env("COOLDOWN_SECONDS", 300),
        max_consecutive_buy_signals=_int_env("MAX_CONSECUTIVE_BUY_SIGNALS", 2),
        atr_reduce_mult=_float_env("ATR_REDUCE_MULT", 2.0),
        atr_block_mult=_float_env("ATR_BLOCK_MULT", 2.8),
        high_vol_buy_scale=_float_env("HIGH_VOL_BUY_SCALE", 0.5),
    )

    return upbit_cfg, bot_cfg, bt_cfg, risk_cfg


# ============================================================
# 모듈 임포트 시 기본 설정 로드
# ============================================================
UPBIT_CFG, BOT_CFG, BT_CFG, RISK_CFG = load_config()
