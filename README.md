# 코인 자동매매 봇

업비트 현물과 하이퍼리퀴드 무기한 선물에서 도는 자동매매 봇 모음입니다. 모든 전략은 **먼저 정한 합격 기준으로 측정한 뒤** 넣고, 탈락한 것은 기록만 남깁니다.

> 자동매매는 원금을 잃을 수 있습니다. 여기 있는 수치는 과거 측정값이고 수익을 보장하지 않습니다. 처음에는 반드시 가상 체결로 돌리세요.

## 봇

| 봇 | 실행 모드 | 하는 일 |
|---|---|---|
| 50일선 추세 봇 | `--mode trade` | 업비트 현물. 일봉 종가가 50일 평균선 위면 보유, 아래면 현금. 텔레그램 명령을 읽어 다른 봇에 넘겨 주는 역할도 한다 |
| BTC 자율 에이전트 | `--mode btc-agent` | 업비트 현물. LLM 이 전략 플랜을 고르는 단타 실험. 예산 한도 안에서만 돈다 ([지시서](docs/BTC_DAYTRADE.md)) |
| 하이퍼리퀴드 롱·숏 봇 | `--mode hl-agent` | BTC·ETH 무기한 선물. 합격한 규칙들의 투표 × 변동성 맞춤 배수로 4시간마다 재조정 ([운영 기록](docs/HL_AGENT.md)) |
| 하이퍼리퀴드 연습용 | `--mode hl-paper` · `hl-ai` | 같은 투표를 가상 돈으로 따르는 쌍둥이, LLM 이 15분마다 판단하는 가상 계좌 |
| VWAP 가상거래 | `--mode vwap-paper` | 주문 API 를 부르지 않는 가상 체결 실험 |

## 폴더

```text
bot/                 봇 코드 (업비트: trader·risk_manager·strategies / 에이전트: dt_* / 하이퍼리퀴드: hl_*)
bot/prompts/         LLM 지시문
dashboard-web/       실시간 대시보드 (Next.js). 봇이 쓰는 state/·logs/ 파일을 읽는다
deploy/              launchd 서비스 정의 (macOS 상시 실행)
research/            측정 스크립트와 데이터 수집기 (규칙 전수 측정은 research/leverage_2026-11/)
docs/                명령어, 봇별 운영 기록
main.py              실행 진입점
test_*.py            백테스트=실거래 일치 검사, 에이전트·하이퍼리퀴드 봇 검사 (네트워크 없음)
state/ logs/ reports/  실행 중 생기는 파일 (git 에 안 올림)
```

## 시작

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env        # 키와 설정을 채운다. 처음엔 BOT_DRY_RUN=true
./venv/bin/python main.py --mode backtest
./venv/bin/python main.py --mode validate
./venv/bin/python main.py --mode trade
```

봇별 실행·재시작·텔레그램 명령은 [docs/COMMANDS.md](docs/COMMANDS.md).

## 검사

```bash
./venv/bin/python test_backtest_parity.py   # 백테스트와 실거래가 같은 판단을 하는지
./venv/bin/python test_dt_agent.py
./venv/bin/python test_hl_agent.py
```

리스크·체결·비용 쪽을 고쳤으면 `test_backtest_parity.py` 를 반드시 통과시킵니다.

## 측정 원칙

- 백테스트와 실거래는 같은 `RiskManager` 코드, 같은 비용, 같은 체결 시점(직전 확정봉 신호 → 다음 봉 시가)을 씁니다.
- 검증은 인샘플/아웃오브샘플 7:3 으로 나누고 **아웃오브샘플 수익이 0 이하면 탈락**입니다. 기준을 느슨하게 해서 통과시키지 않습니다.
- 업비트 규칙 전략 7개(1~240분봉)와 단타 후보는 비용을 넘지 못했고, 통과한 구조는 일봉 20~50일 추세추종뿐입니다.
- 측정 기록 전체는 [.claude/skills/strategy-research/SKILL.md](.claude/skills/strategy-research/SKILL.md), 운영하면서 알게 된 함정은 [CLAUDE.md](CLAUDE.md) 에 있습니다.
