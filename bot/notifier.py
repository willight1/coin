"""
notifier.py — 텔레그램 알림
===========================
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 없으면 아무것도 하지 않는다.
전송 실패는 봇을 멈추지 않는다 (알림 때문에 매매가 죽으면 안 된다).

- notify(text): 한 줄 알림 (체결, 시작/종료, 일일 요약)
- install_error_alerts(): 모든 ERROR 로그를 텔레그램으로 보낸다 (같은 메시지는 10분에 한 번)
- poll_commands(): 내 chat 에서 온 새 명령어(/status 등)를 가져온다. 조회 전용 — 주문 명령은 일부러 없다
"""

import logging
import os
import time

import requests

from . import config  # noqa: F401  (.env 로드)

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
    "조회 전용 명령어\n"
    "/status — 현재가, 신호, 보유, 총자산\n"
    "/help — 이 목록\n"
    "(매수·매도·중지 명령은 없다: 토큰이 새도 주문은 못 하게)"
)

_update_offset = 0  # 확인한 마지막 update_id + 1 (같은 명령에 두 번 답하지 않게)


def poll_commands() -> list[str]:
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
        text = (msg.get("text") or "").strip()
        if text.startswith("/"):
            # '/status@juno_coin_bot 인자' -> '/status'
            commands.append(text.split()[0].split("@")[0].lower())
    return commands
