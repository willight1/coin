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
    access_key: str = ""
    secret_key: str = ""
    market: str = "KRW-BTC"            # 거래 대상 마켓
    markets: tuple[str, ...] = ("KRW-BTC",)  # 거래 대상 마켓 목록
    base_url: str = "https://api.upbit.com"
    timeout: int = 10                   # 요청 타임아웃 (초)
    max_retries: int = 3                # 최대 재시도 횟수


# ============================================================
# 봇 기본 설정
# ============================================================
@dataclass(frozen=True)
class BotConfig:
    """봇 실행에 필요한 기본 설정"""
    interval_seconds: int = 10          # 시세 조회 주기 (초)
    dry_run: bool = True                # True면 실제 주문 실행 안 함
    log_level: str = "INFO"             # 로그 레벨


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
