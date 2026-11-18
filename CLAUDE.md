# CLAUDE.md

> **답변 언어 규칙 (필수):** 사용자에게 보이는 모든 글은 **한국어로만** 쓴다. 최종 답변뿐 아니라 도구 호출 사이의 짧은 진행 문장, 긴급 경고, 표, 텔레그램·대시보드 문구까지 전부. 영어 등 다른 언어로 답하지 않는다. 코드·명령어·파일 경로만 원문 그대로 둔다.

업비트 현물 자동매매 봇. 구조와 전략 목록은 코드를 읽으면 되므로 여기 적지 않는다.
아래는 **코드만 봐서는 알 수 없는 것들**이다.

## 하이퍼리퀴드 롱·숏 추세 봇 (`--mode hl-agent`)

`bot/hl_live.py`·`bot/hl_signals.py`·`bot/hl_client.py`. 합격한 규칙들의 투표를 분석가별로 평균한 노출(−1~+1) × 변동성 맞춤 배수로 4시간봉 마감마다 재조정한다. **기능별 상세(투표단·주문 방식·명령·예약·단타·대화창·연습 계좌·기록 파일·대시보드)는 `docs/HL_AGENT.md` 에 있다 — 이 봇 코드나 대시보드 하이퍼리퀴드 탭을 손대기 전에 반드시 먼저 읽을 것.** 여기에는 항상 지켜야 할 것만 남긴다.

```bash
launchctl kickstart -k gui/$(id -u)/com.juno.hlagent   # 실계좌. 연습용 com.juno.hlpaper, AI 연습 com.juno.hlai 도 같은 방식
./venv/bin/python test_hl_agent.py
```

- **재시작은 `launchctl kickstart` 로만.** launchd(`deploy/*.plist`, KeepAlive)가 띄우므로 nohup 으로 따로 띄우면 중복이다. 4시간봉 마감(KST 1·5·9·13·17·21시) 직후 1분은 피하고, 지정가 주문이 걸려 있을 때(`state.pending`·미체결 주문)는 실계좌 봇을 재시작하지 않는다(분봉 단타의 손절 트리거 주문만 걸려 있으면 괜찮다). `hl_rules.py`·`hl_signals.py` 를 바꾸면 반드시 재시작한다 — 안 하면 옛 코드가 새 투표단 이름을 못 찾아 0표로 센다.
- **합격 기준은 고정이다**(OOS>0·PF>1.2·3구간 중 2개+·30회+, BTC·ETH 모두). 느슨하게 해서 통과시키지 말 것. 투표단은 이 기준으로 자동 재선정된다(사용자 요청으로 승인 없음, 바뀌면 텔레그램).
- **실계좌 매매 로직을 바꾸는 것은 측정 결과를 보여 주고 승인받은 뒤에만.** 사용자가 직접 지시한 운영 변경(멈춤·주문·모드)은 바로 한다.
- **사용자가 정한 것 — 다시 말리지 말고 사실만:** 배수 하한 5배(측정상 전멸 구간), 분봉 단타 20배(측정 32개 조합 전부 탈락). 고정 배수는 쓰지 않는다 — 배수 다이얼은 `HL_TARGET_VOL`.
- **시장가는 쓰지 않는다**(`HL_MAKER_ONLY=1`, 정리 주문 포함). 예외는 킬 드로다운 전량 청산과 분봉 단타의 거래소 손절 트리거 주문 둘뿐이다.
- **측정에서 탈락한 것 — 다시 제안하지 말 것**(수치는 `strategy-research` 스킬): 고정 2~3배, 성적 1등 분석가·규칙 따르기와 성적 가중, 익절 후 재진입, 여러 시간대 다수결 정리, 급반등 비상선, 1분~1시간봉 규칙, 굴림 일봉·단기 추세 분석가 분리·8/12시간봉 규칙 추가, RSI 역추세(70 숏·30 롱), 코인 추가.
- **매매 판단에 쓰지 않는 것(전부 참고용):** AI 운용역 그림자와 AI 연습 계좌(과거 재생 탈락 — 그림자 8주 성적이 규칙보다 낫고 사용자가 승인해야 옮긴다), 연습용 쌍둥이, 시장 심리·고래·코인니스 기록, 시간대별 방향판.
- **실주문으로 아직 확인 안 된 경로**(처음엔 소액으로): 배수 지정 주문(`100 20x`) 체결, 예약 주문이 가격에 닿아 실행되는 것, 분봉 단타 실가동, 대화창 '실행' 버튼으로 나가는 주문. 확인된 것: 지정가 정리(`/hl_close`), 손절 트리거 주문 걸기·조회·취소.
- **텔레그램 `/hl_*` 는 50일선 봇이 넘겨 준다** — 50일선 봇이 꺼져 있으면 안 먹는다. 클로드 코드 세션에서 사용자가 시킬 때는 명령함 `state/hl_commands.txt` 에 한 줄 쓰면 된다(포지션이 바뀌는 명령은 60초 안 `/hl_yes`).
- 대시보드 카드에 숫자를 새로 넣으면 용어 설명(`HlPanel.Help`)도 같이 넣는다.
- `test_hl_agent.py` 는 `hl_ai.shadow_async` 를 막아 둔다(안 막으면 검사가 실제 API 를 부른다).
- 업비트 봇들과 무관한 별도 계좌(달러). 2026-11-17 부터 실계좌는 ETH 멈춤(`/hl_hold`, 되돌리기 `/hl_auto ETH`)이고 BTC 가 계좌 전액 기준으로 돈다.

## 불변 규칙: 백테스트와 실거래는 같은 코드로 판단한다

`backtester.py`와 `trader.py`는 매수/매도 판단을 **직접 구현하지 않는다.** 둘 다 `RiskManager`를 호출한다.

- 손절/익절/추세매도/트레일링/쿨다운/포지션수/매수금액 로직을 바꿀 일이 생기면 **`risk_manager.py` 한 곳만** 고친다.
- 백테스터나 트레이더 안에 판단 로직을 복사해 넣지 말 것. 예전에 그래서 두 경로가 갈라졌고, 백테스트 숫자가 실거래와 무관해졌다.
- 체결 모델도 일치시켜 두었다: **직전 확정봉 신호 → 다음 봉 시가 체결**, 수수료·슬리피지는 양쪽 모두 반영(DRY_RUN 포함).

검증:

```bash
./venv/bin/python test_backtest_parity.py
```

리스크/체결/비용 쪽을 건드렸으면 **반드시 이걸 통과시킨 뒤** 끝낸다. 실패하면 두 경로가 갈라진 것이다.
허용 오차 1%는 트레이더가 업비트 제한 때문에 200봉만 보는 반면 백테스터는 전체 구간으로 지표를 계산해서 생기는 EMA/RSI 워밍업 차이다.

## 함정: `MIN_NET_PROFIT_PCT`는 손절만 남긴다

`check_min_net_profit_exit`은 순이익이 기준 미만이면 **전략의 SELL 신호를 차단한다.** 기본값 0.25%에 왕복비용 0.2%를 더하면 **+0.45% 이상일 때만 익절**되고, 손실 청산은 `STOP_LOSS_PCT`(-3%) 하나만 남는다. 손익비 1:7 → 승률 88%가 필요해진다.

이 값을 올릴 때는 반드시 `STOP_LOSS_PCT`와 같이 본다. 한쪽만 조정하면 조용히 비대칭이 커진다.

관련해서 `trader._tick`의 순서도 의도된 것이다: 추세매도가 순이익 기준으로 보류돼도 **트레일링 스탑 판정으로 내려간다.** 예전엔 여기서 `return` 해버려서, SELL 신호가 계속 뜨는 하락장에서 트레일링이 영원히 발동하지 않았다.

## `AIAutonomousStrategy`는 검증된 적이 없다

예전엔 `main.py`의 기본 전략이었다(지금 기본은 `DEFAULT_TRADE_STRATEGY` = 일봉 SMA50 추세). **백테스트가 불가능하다.** `generate_signals_series` 기본 구현이 봉마다 `generate_signal`을 부르는데 그게 매번 OpenAI API 호출이라, 20,000봉이면 20,000번 호출이다. 그래서 `get_all_strategies()`에 들어 있지 않고 `--mode backtest`/`validate` 결과에도 없다.

실거래에 쓰기 전에 먼저 백테스트 가능하게 만들어야 한다.

LLM 관련 모듈 3개의 실제 역할(코드가 이름만으로는 오해를 부른다):
- `ai_strategy.py` — 봉마다 BUY/SELL/HOLD **한 글자**만 결정. 전략을 바꾸거나 리스크 파라미터를 조정하지 않는다. 과거 거래 기억 없음
- `llm_gate.py` — **BUY만** 필터링. SELL/손절/트레일링은 절대 차단하지 않는다. API 실패 시 통과(`fail_open`)
- `review_agent.py` — 로그 사후 분석. 매매에 관여하지 않음

## 측정된 결론 (상세 수치는 `strategy-research` 스킬)

전략 연구 측정 기록 전체는 `.claude/skills/strategy-research/SKILL.md`에 있다. **전략을 실험·백테스트·검증하거나 추세 전략을 손대기 전에 반드시 먼저 읽을 것.** 항상 지켜야 할 결론만 여기 남긴다:

- 규칙 전략 7개(1/15/60/240분봉)는 **비용을 넘는 엣지가 없다.** 비용 0에서도 PF가 1 안팎이다. **파라미터 튜닝으로 해결되는 문제가 아니다.** "RSI + 이동평균 필터"는 거래 0회인 죽은 전략이다.
- 엣지가 확인된 구조는 **일봉 SMA 20~50 추세추종**뿐이다(`SmaTrendStrategy(50)`, `BOT_CANDLE_UNIT=1440`). 전략이 `risk_overrides`로 전액 투입과 손절·트레일링 끄기를 들고 다닌다. 기본 리스크값(15분봉용)으로 돌리면 추세 중간에 털려서 다른 전략이 된다.
- SMA period는 **200 미만**이어야 한다(트레이더는 200봉만 받는다).
- 추세 전략의 비중은 `TREND_BUY_RATIO_PCT`(기본 1.0)로 조절한다. 전역 `BUY_RATIO_PCT`(0.30)는 적용되지 않는다(`.env.example`을 복사하면 30%로 조용히 도는 것을 막으려고 분리했다). 멀티마켓은 KRW 잔고를 공유하므로, 비중 100%면 먼저 신호가 난 마켓이 전액을 가져간다.
- 추세 전략은 `--mode validate`에서 인샘플 MDD 56%, 승률 27%로 **탈락**이다. 기준을 완화하지 말고 MDD를 줄이는 쪽으로 풀 것.
- **매도 규칙은 '종가 이탈 → 다음 날 09시(다음 봉 시가) 매도' 하나로 둔다.** 장중 긴급 매도·트레일링·고정 손절·반등 재매수는 모두 측정상 개선이 없었다. 장중 상황은 알림(`_check_intraday_warning`, `TREND_WARN_PCT`)으로만 알린다.
- 데이트레이딩 후보 A~G 중 실거래를 통과한 전략은 없다. 텔레그램 `/day` 명령과 인스턴스별 예산 분리는 **통과 전략이 생길 때까지 만들지 않는다.**
- VWAP 가상거래(`--mode vwap-paper`)는 **실제 주문 API를 호출하지 않는다.** 실행법과 기록 위치는 스킬을 참고한다.
- 실험할 때 주의: zsh는 `E="A=0 B=0"; env $E ...`를 단어로 쪼개지 않는다. 변수를 직접 적을 것.

## 검증 기준

`--mode validate`는 인샘플/아웃오브샘플 7:3으로 나누고 **아웃오브샘플 수익이 0 이하면 탈락**시킨다. 인샘플 수익은 파라미터를 맞춰 넣은 결과일 수 있어 근거가 되지 못한다. 이 기준을 완화해서 "통과하는 전략"을 만들지 말 것 — 그러면 검증이 의미를 잃는다.

## 설정

- **DRY_RUN 가상 잔고는 `state/dry_run_<마켓>.json`에 저장된다**(gitignore 대상). 재시작해도 이어지며, 초기 금액을 바꾸거나 처음부터 다시 하려면 이 파일을 지운다. 실거래는 파일 없이 업비트 계좌에서 보유분을 동기화한다.
- **`.env`가 없으면 `bot/config.py`의 기본값이 진실이다.** `.env.example`은 거기에 맞춰 동기화해 둔 것이니, 기본값을 바꾸면 양쪽을 같이 고친다.
- **`BOT_CANDLE_UNIT`(기본 1440=일봉)이 봉 단위를 한 곳에서 결정한다.** 전략에 `required_candle_unit`이 있으면 실거래 모드는 값이 다를 때 시작을 거부한다(백테스트/검증은 실험용으로 허용). 백테스트·검증·실거래·대시보드가 모두 이 값을 쓴다. 어디에도 `unit=1` / `candle_unit=1`을 다시 하드코딩하지 말 것.
- `RiskManager(**overrides)`는 **0을 유효한 값으로 받는다**(손절 비활성 등). 예전의 `x or DEFAULT` 패턴은 0을 넘기면 설정값으로 되돌아가는 버그였다.

## 실행

```bash
./venv/bin/python main.py --mode backtest    # 전략별 백테스트
./venv/bin/python main.py --mode validate    # IS/OOS 분리 검증
./venv/bin/python main.py --mode trade        # 기본: 일봉 SMA50 추세 (BOT_CANDLE_UNIT=1440)
./venv/bin/python -m streamlit run dashboard.py
```

전역 python이 아니라 **`./venv/bin/python`**을 쓴다.

`dashboard.py`는 잔고·손익·차단사유·AI 로그를 이미 다 보여준다. 비어 보인다면 대시보드 문제가 아니라 **데이터가 없는 것**이다 — `.env`에 업비트 키가 있어야 잔고가 뜨고, 봇이 한 번은 돌아서 `logs/trade_*.log`가 쌓여야 손익 추이가 나온다.

## BTC 자율 에이전트 운영 (`--mode btc-agent`)

지시서: `docs/BTC_DAYTRADE.md`, LLM 호출: `docs/BTC_DAYTRADE_LLM_CACHE.md`. 검증 기준(OOS)을 통과한 전략이 아니다 — 예산 한도(`DT_BUDGET_KRW`) 안의 실험으로 돌린다.

```bash
BOT_LOG_PREFIX=dt DT_BUDGET_KRW=300000 ./venv/bin/python main.py --mode btc-agent   # DT_LIVE_CONFIRM=yes 없으면 가상 체결
./venv/bin/python test_dt_agent.py                                                  # 가짜 클라이언트 테스트 (네트워크 없음)
cd dashboard-web && npm run build && npx next start -H 127.0.0.1 -p 3000             # 실시간 대시보드 (Next.js)
```

- 대시보드는 `dashboard-web/`(Next.js 16, App Router). 봇 파일(`state/`, `logs/`)을 서버 쪽 라우트(`app/api/*`)가 직접 읽는다. **반드시 `-H 127.0.0.1` 로 연다** — `/api/command` 가 '전부 팔기'를 보낼 수 있다(JSON 본문·같은 출처만 받게 막아 두었다). 한글 전략 이름은 `dashboard-web/lib/ko.ts` 와 `bot/strategies.py` 의 `KO_NAME` 두 곳에 있다.
- 아이폰에서 보기: Tailscale(맥·아이폰 같은 계정)로 `tailscale serve --bg --http=80 http://127.0.0.1:3000` → `http://<맥 이름>.<테일넷>.ts.net`(주소는 `tailscale serve status` 로 확인). 내 기기끼리만 열린다. 끄기는 `tailscale serve --http=80 off`. Next 서버는 여전히 127.0.0.1 로만 띄운다.
- 예전 Streamlit 판 `dt_dashboard.py` 도 남아 있다(`./venv/bin/python -m streamlit run dt_dashboard.py`).

- **코인마다 프로세스 하나** (`--market KRW-ETH`, 기본 KRW-BTC). 비트코인은 `state/`·`logs/`, 다른 코인은 `state/dt_<코인>/`·`logs/dt_<코인>/` 에 따로 쓴다. `DT_BUDGET_KRW` 는 **모든 코인 봇이 같이 쓰는 돈 하나**다(먼저 사는 코인이 쓰고 남은 돈을 다른 코인이 씀). 각 봇이 `pool_krw`·`pool_used` 를 쓰고 `DtBot.cash()` 가 다른 봇 `pool_used` 를 뺀다. 매수는 `state/dt_pool.lock` 으로 한 봇씩. **하루·7일 손실 한도와 킬 드로다운도 모든 코인 손익 합계로 본다**(`day_pnl_all` 등). 2026-11-13 부터 비트코인만 돈다(ETH·XRP·SOL 상태는 `state/off_dt_<코인>/` 로 치워 둠 — 폴더 이름을 `dt_<코인>` 으로 되돌리고 띄우면 재개). 띄우기: `BOT_LOG_PREFIX=dt_eth nohup caffeinate -i ./venv/bin/python main.py --mode btc-agent --market KRW-ETH >> logs/dt_eth_console.out 2>&1 &` (동시에 켜면 시작 시 캔들 조회가 몰리니 20초 간격). 호가 단위는 시작할 때 `/v1/orderbook/instruments` 에서 받는다. `/dt_*` 명령·대시보드 버튼·`state/DT_STOP` 은 모든 코인에 적용, API 예산 `DT_API_TOTAL_BUDGET_USD` 도 모든 코인 합산. 대시보드는 위쪽 탭으로 코인 선택.
- 상시 실행: `deploy/com.juno.dtagent.plist` 를 `~/Library/LaunchAgents/` 에 복사 후 `launchctl load`. 예산·실거래 확인은 `.env`.
- **50일선 봇과 같은 계좌다.** 에이전트 몫(`reserve_krw`, `btc_volume`=그 코인 수량, `market`)을 각 `dt_live.json` 에 쓰고 `trader._dt_share()` 가 원화는 모든 코인 몫 합, 코인은 같은 마켓 몫만 빼고 매매한다. **이 코드를 바꾸면 50일선 봇도 다시 띄워야 반영된다.** 가상 체결이면 몫은 0. 에이전트는 계좌 BTC 를 넘겨받지 않는다(`DT_ADOPT_ACCOUNT_BTC`).
- **텔레그램 getUpdates 는 50일선 봇만 읽는다.** `/dt_*` 명령은 50일선 봇이 `state/dt_commands.txt` 로 넘긴다(대시보드 버튼도 이 파일). 50일선 봇이 꺼져 있으면 텔레그램 `/dt_*` 가 안 먹는다. `/dt_flat` 확인은 `/yes` 가 아니라 `/dt_yes`(50일선 봇 `/yes` 와 충돌).
- LLM: 경로 1 = Max 구독 `claude -p`(빈 폴더 `~/orca/dt_llm_empty`, 도구·MCP·설정 끔). **subprocess 환경에서 `ANTHROPIC_API_KEY` 를 지운다** — 남아 있으면 구독 대신 API 키로 과금된다. 경로 2 = `ANTHROPIC_API_KEY` + system 두 블록 1시간 캐싱.
- AI 답에는 기술 근거 `reason` 과 별도로 초보자용 `easy`(ELI5) 칸이 있다. 텔레그램·대시보드는 `easy` 를 먼저 보여 준다. 남은 지표 이름은 `EASY_WORDS`(`bot/strategies.py`, `dashboard-web/lib/ko.ts` 두 곳)가 쉬운 말로 바꾼다.
- `DT_LLM_PRIMARY=api` 면 API 를 먼저 쓰고, 누적 비용(`state/dt_live.json` 의 `llm.api_total_usd`, 날이 바뀌어도 안 지움)이 `DT_API_TOTAL_BUDGET_USD` 에 닿으면 구독(`claude -p`)만 쓴다. API 오류(키 없음·크레딧 소진 등)면 60분 구독 후 재시도. `DT_API_RESET_DAY`(지금 16 = 월간 크레딧 충전일)에 모든 코인 봇이 0 으로 되돌린다. 손으로 채우려면 `api_total_usd` 를 0 으로.
- 구독 사용률: `claude -p --output-format stream-json --verbose` 의 `rate_limit_event` 에 5시간·7일 사용률이 온다 → `state/dt_live.json` 의 `sub_limits`. 사용자 본인의 Claude Code 사용분이 같이 들어간 값이다.
- 빠른 점검(Haiku)이 1·2·5분마다 keep/escalate 만 정하고, 깊은 판단(Sonnet)이 플랜을 정한다. LLM 호출은 워커 스레드에서 돌고 결과 적용은 메인 루프에서 한다 — 호출 중에도 손절·킬 드로다운은 10초마다 확인된다.
- 기록: 상태 `state/dt_live.json`, 교훈 `state/dt_lessons.md`, 체결 `logs/dt_trades.csv`, 판단 `logs/dt_decisions.jsonl`(스냅샷 포함, 하루 수 MB), 평가금액 `logs/dt_equity.csv`, 복기 `logs/dt_reviews.jsonl`, 로그 `logs/dt_<날짜>.log`.
- 정지: 신규 매수만 멈춤 = `/dt_stop` 또는 `touch state/DT_STOP`. 재개 = `/dt_go`. 전량 매도 후 정지 = `/dt_flat` → 60초 안 `/dt_yes`. 프로세스 종료(SIGTERM)는 포지션을 두고 상태만 저장한다.
