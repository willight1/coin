# 명령어

전역 python 이 아니라 `./venv/bin/python` 을 씁니다. 설정값 설명은 `.env.example` 에 있습니다(기본값은 `bot/config.py`).

## 실행

```bash
./venv/bin/python main.py --mode backtest                 # 전략별 백테스트
./venv/bin/python main.py --mode validate                 # 인샘플/아웃오브샘플 검증
./venv/bin/python main.py --mode trade                    # 50일선 추세 봇 (BOT_DRY_RUN=true 면 가상)
./venv/bin/python main.py --mode review --date 2026-03-28 # 그날 로그 복기
BOT_LOG_PREFIX=vwap ./venv/bin/python main.py --mode vwap-paper
BOT_LOG_PREFIX=dt DT_BUDGET_KRW=300000 ./venv/bin/python main.py --mode btc-agent   # DT_LIVE_CONFIRM=yes 없으면 가상 체결
BOT_LOG_PREFIX=hl ./venv/bin/python main.py --mode hl-agent                         # HL_LIVE_CONFIRM=yes 없으면 가상 체결
```

## 상시 실행 (macOS launchd)

`deploy/*.plist` 를 `~/Library/LaunchAgents/` 에 복사하고 `launchctl load` 합니다. 경로는 자기 환경에 맞게 고칩니다.
launchd 가 띄운 봇은 **`nohup` 으로 따로 띄우지 않습니다**(중복 실행). 재시작은 아래 명령만 씁니다.

```bash
launchctl kickstart -k gui/$(id -u)/com.juno.coinbot    # 50일선 봇
launchctl kickstart -k gui/$(id -u)/com.juno.hlagent    # 하이퍼리퀴드 실계좌
launchctl kickstart -k gui/$(id -u)/com.juno.hlpaper    # 하이퍼리퀴드 연습용
launchctl kickstart -k gui/$(id -u)/com.juno.hlai       # 하이퍼리퀴드 AI 연습 계좌
```

| 서비스 | 하는 일 |
|---|---|
| `com.juno.dtagent` | BTC 자율 에이전트 |
| `com.juno.hlweekly` | 매주 월요일 투표단 재선정 |
| `com.juno.hlsentiment` | 매시 5분 시장 심리·고래 지갑 기록 |
| `com.juno.coinness` | 10분마다 코인니스 속보 기록 |

## 대시보드

```bash
cd dashboard-web && npm install && npm run build && npx next start -H 127.0.0.1 -p 3000
```

`/api/command` 가 주문 명령을 보낼 수 있으므로 **반드시 `-H 127.0.0.1`** 로 엽니다. 휴대폰에서는 Tailscale 로 봅니다(`tailscale serve --bg --http=80 http://127.0.0.1:3000`).
예전 Streamlit 화면: `./venv/bin/python -m streamlit run dashboard.py`, `dt_dashboard.py`.

## 텔레그램

텔레그램 메시지는 50일선 봇만 읽고, `/dt_*`·`/hl_*`·`/hlp_*` 는 각 봇의 명령함 파일(`state/*_commands.txt`)로 넘깁니다. **50일선 봇이 꺼져 있으면 다른 봇 명령도 안 먹습니다.** 전체 목록은 봇에게 `/help`.

| 봇 | 명령 |
|---|---|
| 50일선 봇 | `/status` `/market` `/buy` `/sell` `/auto` — 사고파는 것은 60초 안 `/yes` |
| BTC 에이전트 | `/dt_status` `/dt_plan` `/dt_stop` `/dt_go` `/dt_flat` — 확인은 `/dt_yes` |
| 하이퍼리퀴드 실계좌 | `/hl_status` `/hl_half` `/hl_wait` `/hl_stop` `/hl_go` `/hl_long` `/hl_short` `/hl_close` `/hl_hold` `/hl_auto` `/hl_at` `/hl_list` `/hl_cancel` `/hl_scalp` — 확인은 `/hl_yes` |
| 하이퍼리퀴드 연습용 | 위 명령을 `/hlp_` 로 |

명령 없이 말을 보내면 하이퍼리퀴드 데스크 AI 가 답합니다. 주문 부탁은 명령으로 바꿔 보여 주고 `/hl_yes` 확인을 받습니다.

## 멈추기

```bash
touch state/DT_STOP     # BTC 에이전트: 새 매수만 멈춤 (재개 /dt_go)
touch state/HL_STOP     # 하이퍼리퀴드: 전부 정리하고 멈춤 (재개 /hl_go)
touch state/HL_WAIT     # 전부 정리하고 새 추세까지 대기
touch state/HL_HALF     # 절반으로 줄이고 새 추세부터 전액
```

## 로그

```bash
tail -f logs/trade_$(date +%F).log   # 50일선 봇
tail -f logs/dt_$(date +%F).log      # BTC 에이전트
tail -f logs/hl_$(date +%F).log      # 하이퍼리퀴드
```

## 검사

```bash
./venv/bin/python test_backtest_parity.py
./venv/bin/python test_dt_agent.py
./venv/bin/python test_hl_agent.py
```
