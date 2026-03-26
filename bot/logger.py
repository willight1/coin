"""
logger.py — 로깅 설정 모듈
==========================
프로젝트 전체에서 사용하는 로거를 설정합니다.
- 콘솔 출력 (컬러 포맷)
- 파일 출력 (logs/ 디렉토리, 날짜별 파일)

사용법:
    from logger import get_logger
    logger = get_logger(__name__)
    logger.info("매수 신호 발생")
"""

import os
import logging
from datetime import datetime
from .config import BOT_CFG
from .paths import PROJECT_ROOT


def get_logger(name: str) -> logging.Logger:
    """
    이름 기반 로거를 생성합니다.
    같은 이름으로 여러 번 호출해도 핸들러가 중복 추가되지 않습니다.

    Args:
        name: 로거 이름 (보통 __name__ 사용)

    Returns:
        logging.Logger: 설정된 로거 인스턴스
    """
    logger = logging.getLogger(name)

    # 이미 핸들러가 설정되어 있으면 중복 추가 방지
    if logger.handlers:
        return logger

    # 로그 레벨 설정
    log_level = getattr(logging, BOT_CFG.log_level.upper(), logging.INFO)
    logger.setLevel(log_level)

    # ---- 콘솔 핸들러 ----
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(console_fmt)
    logger.addHandler(console_handler)

    # ---- 파일 핸들러 ----
    # logs/ 디렉토리가 없으면 생성
    log_dir = os.path.join(PROJECT_ROOT, "logs")
    os.makedirs(log_dir, exist_ok=True)

    # 날짜별 로그 파일
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = os.path.join(log_dir, f"trade_{today}.log")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(log_level)
    file_fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_fmt)
    logger.addHandler(file_handler)

    return logger
