"""
notifier.py — 텔레그램 알림
===========================
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 없으면 아무것도 하지 않는다.
전송 실패는 봇을 멈추지 않는다 (알림 때문에 매매가 죽으면 안 된다).

- notify(text): 한 줄 알림 (체결, 시작/종료, 일일 요약)
- install_error_alerts(): 모든 ERROR 로그를 텔레그램으로 보낸다 (같은 메시지는 10분에 한 번)
- poll_commands(): 내 chat 에서 온 새 명령어(/status 등)를 가져온다. 다른 chat 은 무시
"""

import glob
import logging
import os
import time

import requests

from . import config  # noqa: F401  (.env 로드)
from .paths import PROJECT_ROOT

_log = logging.getLogger(__name__)  # 핸들러 없음 -> 여기서 나는 경고는 텔레그램으로 되돌아가지 않는다


def _creds() -> tuple[str, str]:
    return (os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            os.getenv("TELEGRAM_CHAT_ID", "").strip())


def enabled() -> bool:
    return all(_creds())


def notify(text: str) -> bool:
    """텔레그램으로 메시지를 보냅니다. 설정이 없거나 실패하면 False."""
    token, chat_id = _creds()
    if not (token and chat_id):
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text[:4000]},
            timeout=5,
        )
        if r.status_code != 200:
            _log.warning(f"텔레그램 전송 실패 [{r.status_code}]: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        _log.warning(f"텔레그램 전송 예외: {e}")
        return False


class TelegramErrorHandler(logging.Handler):
    """ERROR 이상 로그를 텔레그램으로 보냅니다. API 장애 때 10초마다 같은 에러가 오지 않게 막는다."""

    def __init__(self, repeat_after_sec: float = 600, send=notify):
        super().__init__(level=logging.ERROR)
        self.repeat_after_sec = repeat_after_sec
        self.send = send
        self._last_sent: dict[str, float] = {}

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        now = time.time()
        if now - self._last_sent.get(msg, 0) < self.repeat_after_sec:
            return
        self._last_sent[msg] = now
        self.send(f"⚠️ 오류 ({record.name})\n{msg}")


def install_error_alerts() -> None:
    """루트 로거에 에러 알림 핸들러를 한 번만 붙입니다 (모든 bot.* 로거가 전파된다)."""
    root = logging.getLogger()
    if enabled() and not any(isinstance(h, TelegramErrorHandler) for h in root.handlers):
        root.addHandler(TelegramErrorHandler())


HELP_TEXT = (
    "📖 명령어 안내\n"
    "\n"
    # 2026-11-16 사용자 요청: 도움말에는 하이퍼리퀴드 실계좌 봇·연습용 봇 명령만 보여 준다.
    # 아래 두 묶음은 안내에서만 뺀 것이고 명령 자체는 그대로 동작한다. 다시 보이게 하려면 주석을 푼다.
    # "■ 50일선 봇 (장기 추세 매매)\n"
    # "/status — 지금 상태: 현재가, 사라/팔라 신호, 들고 있는 코인, 총자산\n"
    # "/market — 시장 요약: 오르는 중인지, 최근 수익률, 출렁임 크기, 고점에서 얼마나 내려왔는지\n"
    # "/buy — 지금 가격으로 바로 사기 (60초 안에 /yes 를 보내야 실제로 삼)\n"
    # "/sell — 들고 있는 코인 전부 바로 팔기 (60초 안에 /yes 를 보내야 실제로 팖)\n"
    # "/yes — 방금 보낸 /buy·/sell 확정\n"
    # "/auto — 자동매매 다시 켜기 (손으로 사고판 뒤에는 신호가 바뀔 때까지 자동매매가 쉬는데, 바로 다시 켬)\n"
    # "\n"
    # "■ AI 매매 봇 (비트코인 단타)\n"
    # "/dt_status — AI 봇 상태: 보유 여부, 오늘·7일 손익, 평가금액, 오늘 산 횟수, 멈춤 여부\n"
    # "/dt_plan — AI가 지금 고른 전략과 그 이유\n"
    # "/dt_stop — 새로 사기만 멈춤 (들고 있는 코인의 손절·매도는 계속함)\n"
    # "/dt_go — 멈춘 것 다시 켜기 (/dt_stop, 손실 한도로 멈춘 것 모두 풀림)\n"
    # "/dt_flat — 전부 팔고 멈추기 (60초 안에 /dt_yes 를 보내야 실제로 팖)\n"
    # "/dt_yes — 방금 보낸 /dt_flat 확정\n"
    # "\n"
    "■ 하이퍼리퀴드 봇 (롱·숏 추세) — 실계좌\n"
    "/hl_status — 지금 상태: 평가금액, 포지션, 분석가별 의견, 대기·절반 여부, 연습용 봇 성적, 다음 점검 시각\n"
    "/hl_half — 지금 포지션을 절반으로 줄이고 새 추세부터 전액\n"
    "/hl_wait — 전부 정리하고 새 추세가 시작될 때까지 대기\n"
    "/hl_stop — 전부 정리하고 멈춤\n"
    "/hl_go — 멈춤·대기·절반을 풀고 다음 4시간 점검부터 투표대로\n"
    "/hl_long 코인 금액 — 직접 롱 주문. 금액은 달러(100)나 계좌의 몇 배(10x). 예: /hl_long BTC 100\n"
    "/hl_short 코인 금액 — 직접 숏 주문. 예: /hl_short BTC 10x\n"
    "/hl_close 코인 — 그 코인 포지션 전부 정리\n"
    "/hl_at 코인 가격 close — 예약 주문: 그 가격에 닿으면 그 코인 전부 정리. 가격 자리에 2% 를 쓰면 '진입가에서 2% 이익', -3% 는 '3% 손실'. 예: /hl_at ETH 2% close\n"
    "/hl_at 코인 가격 long 금액 · short 금액 — 예약 주문: 그 가격에 닿으면 그만큼 주문. 예: /hl_at BTC 63000 long 100\n"
    "/hl_list — 걸어 둔 예약 주문 보기 · /hl_cancel 번호 — 예약 취소 (전부: /hl_cancel all)\n"
    "/hl_long BTC 100 20x — 넣는 돈 100달러 × 20배(포지션 2,000달러) 롱. 숏은 /hl_short. 말로 '20배로 100달러 롱쳐'도 됨\n"
    "/hl_scalp on — 분봉 단타 켜기(1분봉 투표 60% 넘는 쪽으로 20배, 2% 손절) · /hl_scalp off — 끄고 정리\n"
    "/hl_hold 코인 — 그 코인 거래만 멈춤. 주문 없이 지금 포지션은 그대로 두고 봇이 더 사고팔지 않음. 예: /hl_hold ETH\n"
    "/hl_auto 코인 — 직접 주문했거나 멈춘 코인을 봇에게 돌려주기 (그 전까지 봇은 그 코인에 이익 보호만 검: 최고 이익에서 5% 되돌리면 75% 정리)\n"
    "/hl_yes — 방금 보낸 실계좌 명령 확정 (60초 안). 확정 전에 내용과 예상 청산가를 보여 줌\n"
    "그냥 말을 보내면 데스크 AI 가 차트 지표와 봇 상태를 보고 바로 판단해서 답해요 (주문 부탁은 /hl_yes 확인 뒤 실행)\n"
    "\n"
    "■ 하이퍼리퀴드 연습용 봇 (가상 돈, 대기 없이 투표대로)\n"
    "/hlp_status — 연습용 상태: 가상 평가금액, 포지션, 분석가별 의견\n"
    "/hlp_half — 연습용 포지션을 절반으로\n"
    "/hlp_wait — 연습용 포지션을 전부 정리하고 새 추세까지 대기\n"
    "/hlp_stop — 연습용을 전부 정리하고 멈춤\n"
    "/hlp_go — 연습용 멈춤·대기·절반 풀기\n"
    "/hlp_long 코인 금액 · /hlp_short 코인 금액 — 연습용 직접 주문 (가상 체결). 예: /hlp_short ETH 10x\n"
    "/hlp_close 코인 — 연습용 그 코인 정리\n"
    "/hlp_auto 코인 — 연습용 그 코인을 봇에게 돌려주기\n"
    "/hlp_yes — 방금 보낸 연습용 명령 확정 (60초 안)\n"
    "\n"
    "/help — 이 안내"
)

_update_offset = 0  # 확인한 마지막 update_id + 1 (같은 명령에 두 번 답하지 않게)


# 대시보드가 명령을 적어 두는 파일. 봇이 텔레그램 명령과 같이 읽어서 처리한다 (대시보드는 다른 프로세스)
COMMAND_INBOX = os.path.join(PROJECT_ROOT, "state", "commands.txt")


# BTC 자율 에이전트(--mode btc-agent) 명령함. 텔레그램 getUpdates 는 한 프로세스만 읽어야
# 명령을 서로 뺏지 않으므로, 50일선 봇이 /dt_* 명령을 여기로 넘기고 에이전트는 이 파일만 읽는다.
DT_COMMAND_INBOX = os.path.join(PROJECT_ROOT, "state", "dt_commands.txt")
# 하이퍼리퀴드 봇(--mode hl-agent) 명령함. 같은 이유로 50일선 봇이 /hl_* 를 여기로 넘긴다 (대시보드 버튼도 이 파일).
HL_COMMAND_INBOX = os.path.join(PROJECT_ROOT, "state", "hl_commands.txt")
HL_PAPER_INBOX = os.path.join(PROJECT_ROOT, "state", "hl_paper_commands.txt")   # 연습용 쌍둥이(--mode hl-paper): 텔레그램 /hlp_* 를 /hl_* 로 바꿔 넣는다


def dt_dirs() -> list[str]:
    """AI 매매 봇 상태 폴더: state/ (비트코인) + state/dt_<코인>/ (그 외 코인, main.py --market)."""
    st = os.path.join(PROJECT_ROOT, "state")
    return [st] + sorted(d for d in glob.glob(os.path.join(st, "dt_*")) if os.path.isdir(d))


def send_dt_command(cmd: str) -> None:
    """/dt_* 명령을 모든 코인 봇의 명령함에 넣는다."""
    for d in dt_dirs():
        send_command(cmd, path=os.path.join(d, "dt_commands.txt"))


def send_command(*cmds: str, path: str = COMMAND_INBOX) -> None:
    """대시보드 → 봇 명령 전달. 봇이 다음 주기(10초 안)에 텔레그램 명령과 똑같이 처리한다."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.writelines(c + "\n" for c in cmds)


def _drain_inbox(path: str = COMMAND_INBOX) -> list[str]:
    """파일 이름을 바꾼 뒤 읽는다: 읽는 사이 대시보드가 추가한 명령은 새 파일로 가서 지워지지 않는다."""
    tmp = path + ".reading"
    try:
        os.replace(path, tmp)
    except FileNotFoundError:
        return []
    with open(tmp, encoding="utf-8") as f:
        cmds = [line.strip().lower() for line in f if line.strip().startswith("/")]
    os.remove(tmp)
    return cmds


def poll_commands() -> list[str]:
    """대시보드 명령 + 텔레그램 명령."""
    return _drain_inbox() + _poll_telegram()


def _poll_telegram() -> list[str]:
    """
    등록된 chat 에서 온 새 명령어(예: '/status')를 반환합니다.
    다른 사람이 봇에게 보낸 메시지는 무시한다 (봇 사용자명은 누구나 검색 가능).
    실패하면 빈 목록 — 매매 루프를 막지 않는다.
    """
    global _update_offset
    token, chat_id = _creds()
    if not (token and chat_id):
        return []
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"offset": _update_offset, "timeout": 0},
            timeout=5,
        )
        updates = r.json().get("result", []) if r.status_code == 200 else []
    except Exception as e:
        _log.warning(f"텔레그램 명령 조회 예외: {e}")
        return []

    commands = []
    for u in updates:
        _update_offset = max(_update_offset, u.get("update_id", 0) + 1)
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != chat_id:
            continue
        text = " ".join((msg.get("text") or "").split())[:500]
        if not text:
            continue
        head = text.split()[0].split("@")[0].lower()
        if not text.startswith("/"):
            commands.append("/hl_ask " + text)                 # 그냥 쓴 말 = 하이퍼리퀴드 데스크에게 묻기
        elif head.startswith(("/hl_", "/hlp_")):
            commands.append(" ".join([head, *text.split()[1:]]))   # 직접 주문·질문은 인자까지 넘긴다
        else:
            # '/status@juno_coin_bot 인자' -> '/status'
            commands.append(head)
    return commands
