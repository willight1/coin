"""
upbit_client.py — 업비트 API 클라이언트
=======================================
업비트 Open API를 이용한 시세 조회, 계좌 조회, 주문 기능을 제공합니다.

주요 특징:
- JWT 인증 (PyJWT + uuid + hashlib)
- DRY_RUN 모드에서 실제 주문 차단
- 자동 재시도 로직 (최대 3회)
- 타임아웃 처리
- 모든 API 호출에 예외처리 포함

참고: 업비트 API 문서 — https://docs.upbit.com
"""

import hashlib
import uuid
import time
from urllib.parse import urlencode, unquote

import jwt
import requests

from .config import UPBIT_CFG
from .logger import get_logger

logger = get_logger(__name__)


class UpbitClientError(Exception):
    """업비트 API 관련 커스텀 예외"""
    pass


class UpbitClient:
    """
    업비트 API 클라이언트

    DRY_RUN 모드에서는 주문 관련 메서드가 로그만 기록하고
    실제 API 호출은 하지 않습니다.
    """

    def __init__(self, access_key: str = "", secret_key: str = "",
                 dry_run: bool = True):
        """
        Args:
            access_key: 업비트 API 액세스 키
            secret_key: 업비트 API 시크릿 키
            dry_run: True이면 주문 실행 안 함
        """
        self.access_key = access_key or UPBIT_CFG.access_key
        self.secret_key = secret_key or UPBIT_CFG.secret_key
        self.base_url = UPBIT_CFG.base_url
        self.timeout = UPBIT_CFG.timeout
        self.max_retries = UPBIT_CFG.max_retries
        self.dry_run = dry_run

        if not self.access_key or not self.secret_key:
            logger.warning("업비트 API 키가 설정되지 않았습니다. 시세 조회만 가능합니다.")

    # ============================================================
    # JWT 인증 토큰 생성
    # ============================================================
    def _create_token(self, query: dict | None = None) -> str:
        """
        JWT 인증 토큰을 생성합니다.

        Args:
            query: API 요청 파라미터 (주문 등에서 사용)

        Returns:
            str: Bearer 토큰 문자열
        """
        payload = {
            "access_key": self.access_key,
            "nonce": str(uuid.uuid4()),
        }

        # 쿼리 파라미터가 있으면 해시 추가 (주문 시 필요)
        if query:
            query_string = unquote(urlencode(query, doseq=True))
            query_hash = hashlib.sha512(query_string.encode()).hexdigest()
            payload["query_hash"] = query_hash
            payload["query_hash_alg"] = "SHA512"

        token = jwt.encode(payload, self.secret_key, algorithm="HS256")
        return f"Bearer {token}"

    # ============================================================
    # HTTP 요청 공통 메서드
    # ============================================================
    def _request(self, method: str, endpoint: str,
                 params: dict | None = None,
                 data: dict | None = None,
                 auth: bool = False) -> dict | list:
        """
        HTTP 요청을 보내고 응답을 반환합니다.
        재시도 로직 포함.

        Args:
            method: HTTP 메서드 ("GET", "POST", "DELETE")
            endpoint: API 엔드포인트 (예: "/v1/ticker")
            params: GET 파라미터
            data: POST 바디
            auth: 인증 필요 여부

        Returns:
            dict 또는 list: API 응답 JSON

        Raises:
            UpbitClientError: API 호출 실패 시
        """
        url = f"{self.base_url}{endpoint}"
        headers = {}

        if auth:
            query = params or data
            headers["Authorization"] = self._create_token(query)

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    params=params,
                    json=data,
                    headers=headers,
                    timeout=self.timeout,
                )

                # HTTP 에러 확인 (업비트 주문 생성은 201 Created 응답 가능)
                if not (200 <= response.status_code < 300):
                    error_msg = f"API 에러 [{response.status_code}]: {response.text}"
                    logger.error(error_msg)
                    raise UpbitClientError(error_msg)

                return response.json()

            except requests.exceptions.Timeout:
                last_error = f"타임아웃 발생 (시도 {attempt}/{self.max_retries})"
                logger.warning(last_error)
            except requests.exceptions.ConnectionError as e:
                last_error = f"연결 오류 (시도 {attempt}/{self.max_retries}): {e}"
                logger.warning(last_error)
            except UpbitClientError:
                raise  # API 에러는 재시도하지 않음
            except Exception as e:
                last_error = f"예상치 못한 오류 (시도 {attempt}/{self.max_retries}): {e}"
                logger.warning(last_error)

            # 재시도 대기 (지수 백오프)
            if attempt < self.max_retries:
                wait = 2 ** attempt
                logger.info(f"{wait}초 후 재시도합니다...")
                time.sleep(wait)

        raise UpbitClientError(f"최대 재시도 횟수 초과: {last_error}")

    # ============================================================
    # 시세 조회 API
    # ============================================================
    def get_ticker(self, market: str | None = None) -> dict:
        """
        현재가 정보를 조회합니다.

        Args:
            market: 마켓 코드 (예: "KRW-BTC"), None이면 설정값 사용

        Returns:
            dict: 현재가 정보
        """
        market = market or UPBIT_CFG.market
        result = self._request("GET", "/v1/ticker", params={"markets": market})
        if isinstance(result, list) and len(result) > 0:
            return result[0]
        return result

    def get_candles_minutes(self, unit: int = 1, market: str | None = None,
                           count: int = 200) -> list[dict]:
        """
        분봉 캔들 데이터를 조회합니다.

        Args:
            unit: 분 단위 (1, 3, 5, 15, 30, 60, 240)
            market: 마켓 코드
            count: 조회할 캔들 수 (최대 200)

        Returns:
            list[dict]: 캔들 데이터 리스트 (최신순)
        """
        market = market or UPBIT_CFG.market
        params = {"market": market, "count": min(count, 200)}
        return self._request("GET", f"/v1/candles/minutes/{unit}", params=params)

    def get_candles_days(self, market: str | None = None,
                        count: int = 200) -> list[dict]:
        """
        일봉 캔들 데이터를 조회합니다.

        Args:
            market: 마켓 코드
            count: 조회할 캔들 수 (최대 200)

        Returns:
            list[dict]: 일봉 데이터 리스트 (최신순)
        """
        market = market or UPBIT_CFG.market
        params = {"market": market, "count": min(count, 200)}
        return self._request("GET", "/v1/candles/days", params=params)

    def get_orderbook(self, market: str | None = None) -> dict:
        """
        호가 정보를 조회합니다.

        Args:
            market: 마켓 코드

        Returns:
            dict: 호가 정보
        """
        market = market or UPBIT_CFG.market
        result = self._request("GET", "/v1/orderbook",
                               params={"markets": market})
        if isinstance(result, list) and len(result) > 0:
            return result[0]
        return result

    # ============================================================
    # 계좌 조회 API (인증 필요)
    # ============================================================
    def get_accounts(self) -> list[dict]:
        """
        전체 계좌 잔고를 조회합니다.

        Returns:
            list[dict]: 보유 자산 목록
        """
        return self._request("GET", "/v1/accounts", auth=True)

    def get_balance(self, currency: str = "KRW") -> float:
        """
        특정 자산의 잔고를 조회합니다.

        Args:
            currency: 화폐 코드 (예: "KRW", "BTC")

        Returns:
            float: 잔고 수량
        """
        accounts = self.get_accounts()
        for account in accounts:
            if account.get("currency") == currency:
                return float(account.get("balance", 0))
        return 0.0

    def get_avg_buy_price(self, currency: str = "BTC") -> float:
        """
        특정 자산의 평균 매수가를 조회합니다.

        Args:
            currency: 화폐 코드

        Returns:
            float: 평균 매수가 (보유하지 않으면 0.0)
        """
        accounts = self.get_accounts()
        for account in accounts:
            if account.get("currency") == currency:
                return float(account.get("avg_buy_price", 0))
        return 0.0

    # ============================================================
    # 주문 API (인증 필요)
    # ============================================================
    def _check_dry_run(self, action: str, **kwargs) -> dict | None:
        """
        DRY_RUN 모드에서 실제 주문을 차단합니다.

        Args:
            action: 주문 동작 설명
            **kwargs: 주문 파라미터

        Returns:
            dict: DRY_RUN 모드이면 가짜 응답 반환, 아니면 None
        """
        if self.dry_run:
            logger.info(f"[DRY_RUN] {action} — 실제 주문 실행 안 함: {kwargs}")
            return {
                "uuid": f"dry_run_{uuid.uuid4().hex[:8]}",
                "side": kwargs.get("side", ""),
                "ord_type": kwargs.get("ord_type", ""),
                "price": kwargs.get("price", 0),
                "volume": kwargs.get("volume", 0),
                "state": "dry_run",
            }
        return None

    def order_market_buy(self, market: str | None = None,
                         price: float = 0) -> dict:
        """
        시장가 매수 주문

        Args:
            market: 마켓 코드
            price: 매수 금액 (KRW)

        Returns:
            dict: 주문 결과
        """
        market = market or UPBIT_CFG.market

        # DRY_RUN 확인
        dry = self._check_dry_run("시장가 매수", market=market, price=price,
                                   side="bid", ord_type="price")
        if dry is not None:
            return dry

        data = {
            "market": market,
            "side": "bid",
            "price": str(price),
            "ord_type": "price",  # 시장가 매수
        }
        logger.info(f"시장가 매수 주문: {market}, 금액={price} KRW")
        return self._request("POST", "/v1/orders", data=data, auth=True)

    def order_market_sell(self, market: str | None = None,
                          volume: float = 0) -> dict:
        """
        시장가 매도 주문

        Args:
            market: 마켓 코드
            volume: 매도 수량

        Returns:
            dict: 주문 결과
        """
        market = market or UPBIT_CFG.market

        dry = self._check_dry_run("시장가 매도", market=market, volume=volume,
                                   side="ask", ord_type="market")
        if dry is not None:
            return dry

        data = {
            "market": market,
            "side": "ask",
            "volume": str(volume),
            "ord_type": "market",  # 시장가 매도
        }
        logger.info(f"시장가 매도 주문: {market}, 수량={volume}")
        return self._request("POST", "/v1/orders", data=data, auth=True)

    def order_limit_buy(self, market: str | None = None,
                         price: float = 0, volume: float = 0) -> dict:
        """
        지정가 매수 주문

        Args:
            market: 마켓 코드
            price: 지정 가격
            volume: 매수 수량

        Returns:
            dict: 주문 결과
        """
        market = market or UPBIT_CFG.market

        dry = self._check_dry_run("지정가 매수", market=market, price=price,
                                   volume=volume, side="bid", ord_type="limit")
        if dry is not None:
            return dry

        data = {
            "market": market,
            "side": "bid",
            "price": str(price),
            "volume": str(volume),
            "ord_type": "limit",
        }
        logger.info(f"지정가 매수 주문: {market}, 가격={price}, 수량={volume}")
        return self._request("POST", "/v1/orders", data=data, auth=True)

    def order_limit_sell(self, market: str | None = None,
                          price: float = 0, volume: float = 0) -> dict:
        """
        지정가 매도 주문

        Args:
            market: 마켓 코드
            price: 지정 가격
            volume: 매도 수량

        Returns:
            dict: 주문 결과
        """
        market = market or UPBIT_CFG.market

        dry = self._check_dry_run("지정가 매도", market=market, price=price,
                                   volume=volume, side="ask", ord_type="limit")
        if dry is not None:
            return dry

        data = {
            "market": market,
            "side": "ask",
            "price": str(price),
            "volume": str(volume),
            "ord_type": "limit",
        }
        logger.info(f"지정가 매도 주문: {market}, 가격={price}, 수량={volume}")
        return self._request("POST", "/v1/orders", data=data, auth=True)

    def get_order(self, uuid_str: str) -> dict:
        """
        개별 주문 조회

        Args:
            uuid_str: 주문 UUID

        Returns:
            dict: 주문 상세 정보
        """
        params = {"uuid": uuid_str}
        return self._request("GET", "/v1/order", params=params, auth=True)

    def cancel_order(self, uuid_str: str) -> dict:
        """
        주문 취소

        Args:
            uuid_str: 취소할 주문 UUID

        Returns:
            dict: 취소 결과
        """
        if self.dry_run:
            logger.info(f"[DRY_RUN] 주문 취소 — 실행 안 함: uuid={uuid_str}")
            return {"uuid": uuid_str, "state": "dry_run_cancel"}

        params = {"uuid": uuid_str}
        logger.info(f"주문 취소: uuid={uuid_str}")
        return self._request("DELETE", "/v1/order", params=params, auth=True)
