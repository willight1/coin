# BTC 에이전트 LLM 호출·캐싱 추가 지시서 (Claude Code용)

- 작성: 2026-11-10 (KST)
- 대상 저장소: `/Users/juno/orca/coin` (같은 내용: `docs/BTC_DAYTRADE_LLM_CACHE.md`)
- 본 지시서 `docs/BTC_DAYTRADE.md`에 더하는 문서다. 이 문서의 내용이 본 지시서 1장·7장의 LLM 설정(`DT_LLM_PROVIDER`, `DT_LLM_MODEL`)과 6장 호출 일정(`next_check_min` 5~60분, 하루 200회)을 대체한다.
- 목표: Max 구독으로 비용 없이 판단을 호출하고, 구독 한도에 걸리면 API 키로 넘어가며, API 경로에서는 시스템 프롬프트를 캐싱해 비용을 줄인다.

---

## 1. 호출 경로

| 경로 | 쓰는 것 | 비용 | 언제 |
|---|---|---|---|
| 경로 1 (기본) | Max 구독으로 로그인한 `claude -p` | 구독 한도에서 차감 (추가 비용 없음) | 평소 |
| 경로 2 (예비) | `ANTHROPIC_API_KEY`로 Claude API 직접 호출 + 캐싱 | Max에 포함된 월 100달러 API 크레딧에서 차감 | 경로 1이 막혔을 때 |

---

## 2. 경로 1: Max 구독 `claude -p`

- 봇이 도는 맥에서 Claude Code에 Max 계정으로 로그인해 둔다. 구독으로 로그인한 `claude -p`는 구독 한도에서 차감된다(Anthropic 도움말, 2026-10-07 업데이트).
- 실행 폴더는 **빈 전용 폴더** `~/orca/dt_llm_empty`로 한다. 저장소 폴더에서 실행하면 CLAUDE.md, 스킬, 훅까지 매번 읽어서 토큰이 늘고 부작용이 생긴다.
- `--bare`는 쓰지 않는다. bare 모드는 구독 로그인을 쓰지 않고 API 키가 필요하다.
- 호출 형태

  ```bash
  cd ~/orca/dt_llm_empty
  cat snapshot.json | claude -p "아래 스냅샷으로 판단하고 스키마대로만 답해" \
    --append-system-prompt-file <저장소>/bot/prompts/dt_system.md \
    --output-format json --json-schema "<본 지시서 4장 출력 형식의 JSON Schema>" \
    --model <DT_CLI_MODEL>
  ```

- 도구(파일 읽기·쓰기, 셸, 웹)는 전부 쓰지 못하게 막는다. 맞는 플래그는 설치된 버전의 `claude --help`로 확인한다.
- 결과는 JSON 응답의 `structured_output`에서 읽는다. 시간 제한은 120초.
- 교훈 메모(`state/dt_lessons.md`)는 스냅샷 앞부분에 넣는다.
- 이 경로의 캐싱은 Claude Code가 알아서 한다. 따로 설정하지 않는다.
- 응답 JSON의 비용 추정값(`total_cost_usd`)과 사용량을 호출마다 기록한다.

---

## 3. 경로 2: Claude API + 시스템 프롬프트 캐싱

### 3-1. 넘어가는 조건
- 경로 1이 `rate_limit`, `authentication_failed`, `billing_error`, `overloaded` 오류를 내면 바로 넘어간다
- 시간 초과나 형식 오류가 2회 연속이면 넘어간다
- 넘어간 뒤 1시간 동안 경로 2를 쓰고, 그다음 다시 경로 1을 시도한다
- 전환할 때마다 텔레그램으로 알린다

### 3-2. 키 준비 (사용자가 직접)
1. claude.ai → 설정 → Billing → API credits에서 Console 조직을 연결한다 (Max 월 100달러 크레딧, 결제수단 등록 불필요, 매 결제 주기 끝에 소멸·이월 없음)
2. platform.claude.com Console → API Keys에서 봇 전용 키를 만든다
3. `.env`에 `ANTHROPIC_API_KEY=...`를 직접 넣는다 (채팅에 붙여 넣지 않는다)
4. 같은 크레딧을 다른 봇(예: 물어보살)과 나눠 쓴다는 점을 비용 계산에 넣는다

### 3-3. 요청 구조 (Messages API)

```python
body = {
    "model": DT_API_MODEL,
    "max_tokens": 800,
    "system": [
        {   # 블록 1. 고정: 역할, 전략 라이브러리, 근거, 출력 형식 (bot/prompts/dt_system.md)
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
        {   # 블록 2. 하루 한 번(09:05 복기 후) 바뀜: 교훈 메모
            "type": "text",
            "text": LESSONS_MD,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
    ],
    # 매번 바뀜: 스냅샷. 캐시하지 않는다.
    "messages": [{"role": "user", "content": SNAPSHOT_JSON}],
}
```

- 헤더: `x-api-key: <ANTHROPIC_API_KEY>`, `anthropic-version: 2023-06-01`, `content-type: application/json`
- 응답 텍스트에서 JSON을 파싱하고, 형식이 깨지면 본 지시서 규칙대로 현재 플랜 유지 + hold

### 3-4. 캐싱 규칙
1. **`cache_control`은 바뀌지 않는 블록의 끝에만 단다.** 요청 최상위에 자동 캐싱(`cache_control` 하나만 최상위에 두는 방식)을 켜지 않는다. 자동 캐싱은 마지막 블록, 즉 매번 바뀌는 스냅샷에 캐시 지점을 붙여서 매번 새로 쓰기만 하고 재사용이 안 된다.
2. **블록 1에는 바뀌는 값을 절대 넣지 않는다.** 시각, 잔고, 가격, 포지션은 스냅샷에만 넣는다. 글자 하나만 달라져도 캐시가 새로 써진다.
3. **유효시간은 1시간(`"ttl": "1h"`).** 판단 간격이 5~60분이라 기본 5분 캐시는 자주 끊긴다. 캐시를 읽을 때마다 유효시간이 다시 연장된다.
4. **최소 길이.** Sonnet 5.5·Haiku 5.5는 512토큰 이상부터 캐시된다. 더 짧으면 오류 없이 그냥 캐시되지 않는다. 블록 1은 이보다 길게 유지한다(본 지시서 11장 프롬프트는 약 2~3천 토큰).
5. **블록 순서를 바꾸지 않는다.** 캐시는 앞에서부터 이어지는 내용이 같아야 재사용된다(`tools` → `system` → `messages` 순서).
6. 복기로 교훈 메모가 바뀌면 블록 2만 새로 써지고 블록 1 캐시는 그대로 쓴다.

### 3-5. 확인과 기록
- 매 API 호출의 `usage.cache_creation_input_tokens`, `usage.cache_read_input_tokens`, `usage.input_tokens`, `usage.output_tokens`를 `logs/dt_decisions.jsonl`에 기록한다.
  - `cache_creation_input_tokens` > 0: 캐시를 새로 쓴 호출 (첫 호출, 1시간 넘게 쉬었거나 프롬프트가 바뀐 경우)
  - `cache_read_input_tokens` > 0: 캐시를 재사용한 호출 (정상)
  - 둘 다 0: 캐시 안 됨 → 텔레그램 알림
- 일일 요약(09:05)에 경로별 호출 수, 캐시 적중률(캐시 읽은 호출 / API 호출), 추정 비용을 넣는다.

### 3-6. 비용 감각 (Sonnet 5.5, 하루 150회 모두 API 경로일 때, 2026-11-10 가격표 기준)
- 입력 단가 $2/MTok, 1시간 캐시 쓰기 $4/MTok, 캐시 읽기 $0.10/MTok, 출력 $10/MTok
- 시스템 프롬프트 약 3천 토큰: 캐시 없으면 월 약 27달러, 캐시하면 월 약 2~3달러
- 스냅샷(매번 바뀜)은 캐시되지 않으므로 입력 비용 전체로는 30~40% 정도 줄어든다
- 평소에는 경로 1(구독)을 쓰므로 실제 API 비용은 경로 2로 넘어간 시간만큼만 나온다

---

## 4. 환경변수 (`bot/config.py` 기본값과 `.env.example`에 같이 추가)

| 변수 | 기본값 | 내용 |
|---|---|---|
| `DT_LLM_PRIMARY` | claude_cli | claude_cli / api |
| `DT_LLM_FALLBACK` | api | api / none |
| `DT_CLI_MODEL` | (설정) | `claude -p`에 줄 모델 |
| `DT_API_MODEL` | (설정) | API 경로 모델 (예: Sonnet 5.5 모델 ID) |
| `DT_CLI_WORKDIR` | ~/orca/dt_llm_empty | `claude -p` 실행 폴더 |
| `DT_CLI_TIMEOUT_SEC` | 120 | `claude -p` 시간 제한 |
| `DT_FALLBACK_HOLD_MIN` | 60 | 경로 2로 넘어간 뒤 경로 1 재시도까지 대기 |
| `ANTHROPIC_API_KEY` | 없음 | 경로 2 키 |

9장 복기 호출도 같은 경로 규칙을 따른다.

---

## 4-2. 호출 빈도 (자주 호출, 본 지시서 6장 대체)

### 두 단계 판단
| 단계 | 모델 | 하는 일 |
|---|---|---|
| 빠른 점검 | 가벼운 모델 (`DT_QUICK_MODEL`, 예: Haiku 5.5) | 스냅샷을 보고 「현재 플랜 유지」인지 「다시 판단 필요」인지만 정하고, 보유 중 급변이면 sell_now 제안 |
| 깊은 판단 | 큰 모델 (`DT_DEEP_MODEL`, 예: Sonnet 5.5) | 본 지시서 4장 형식으로 국면·전략·파라미터·매매 결정 |

- 빠른 점검 출력: `{"verdict":"keep|escalate","urgent_sell":false,"reason":"한 문장"}`
- `escalate`이거나 `urgent_sell=true`면 같은 스냅샷으로 바로 깊은 판단을 부른다. 빠른 점검은 직접 주문하지 않는다.
- 아래 경우는 빠른 점검 없이 바로 깊은 판단: 새 진입 신호, 체결 직후, 1시간·4시간·일봉 마감, 플랜 최소 유지시간(60분) 종료, FOMC·CPI 전후, 매일 09:05 복기
- 빠른 점검용 시스템 프롬프트는 `bot/prompts/dt_quick.md`로 따로 두고, API 경로에서는 이것도 3-4 규칙대로 캐싱한다

### 점검 간격
| 상황 | 빠른 점검 간격 |
|---|---|
| 신호 임박·발생 후 30분 (라이브러리 전략 중 하나가 진입 가격의 0.3% 이내, 또는 1시간 RSI가 65~70에서 오르는 중) | 1분 (`DT_CHECK_HOT_MIN`) |
| 보유 중 | 2분 (`DT_CHECK_HOLDING_MIN`) |
| 현금이고 조용함 | 5분 (`DT_CHECK_IDLE_MIN`) |

- 깊은 판단의 `next_check_min`은 1~15분으로 받는다. 위 표보다 짧게는 요청할 수 있고, 길게는 못 한다.
- 본 지시서 6장의 이벤트 깨우기(봉 마감, 새 신호, 1% 급변, 거래량 3배, 체결, FOMC·CPI)는 그대로 둔다.
- 이전 호출이 끝나지 않았으면 다음 호출을 건너뛴다(중복 실행 금지). `claude -p`는 한 번에 5~30초 걸릴 수 있다.
- 플랜 최소 유지시간(60분), 하루 매수 6회, 손실 한도는 그대로다. 자주 부른다고 거래가 늘지는 않는다.

### 상한과 예상치
| 변수 | 기본값 | 내용 |
|---|---|---|
| `DT_MAX_QUICK_CALLS_PER_DAY` | 800 | 빠른 점검 하루 상한 |
| `DT_MAX_DEEP_CALLS_PER_DAY` | 120 | 깊은 판단 하루 상한 (넘으면 현재 플랜만 실행, 보유 중 urgent_sell은 예외로 허용) |
| `DT_API_DAILY_BUDGET_USD` | 3 | 경로 2(API) 하루 비용 상한. 넘으면 빠른 점검 간격을 2배로 늘린다 |

- 예상 호출 수: 하루 대부분 현금이면 빠른 점검 약 300회 + 깊은 판단 약 40회, 8시간 보유하는 날은 빠른 점검 약 500회 + 깊은 판단 약 50회
- 전부 API 경로라고 해도 (2026-11-10 가격표, 호출당 입력 약 8천 토큰 중 시스템 3천 캐시): Haiku 5.5 빠른 점검 호출당 약 0.1센트, Sonnet 5.5 깊은 판단 호출당 약 1.4센트 → 월 약 30~35달러로 Max 월 크레딧(100달러) 안
- 참고: Sonnet 5.5만으로 1분마다 부르면 월 약 600달러, Haiku 5.5만으로 1분마다면 월 약 28달러
- 구독 경로(`claude -p`)에서도 빠른 점검은 가벼운 모델로 부른다. Max 한도는 정확한 숫자가 공개돼 있지 않으므로, 첫 1주일은 하루 호출 수와 한도 오류 횟수를 일일 요약에 넣고 간격을 조정한다
- 모델 변수: `DT_QUICK_MODEL`(빠른 점검), `DT_DEEP_MODEL`(깊은 판단). 경로 1·2 모두 이 두 값을 쓴다(4장의 `DT_CLI_MODEL`, `DT_API_MODEL`은 이 두 값으로 대체)

---

## 5. 하지 말 것
- 요청 최상위 자동 캐싱 사용
- 블록 1(시스템 프롬프트)에 시각·가격·잔고 넣기
- 저장소 폴더에서 `claude -p` 실행
- 경로 1에서 도구 허용
- API 키를 로그·채팅·커밋에 출력

---

## 6. Claude Code 요청문

```text
docs/BTC_DAYTRADE_LLM_CACHE.md 를 읽고 btc-agent 의 LLM 호출을 이 문서대로 만들어줘. 본 지시서 docs/BTC_DAYTRADE.md 의 DT_LLM_PROVIDER/DT_LLM_MODEL 설정은 이 문서의 설정으로 대체해.
- 경로 1: Max 구독 로그인 claude -p, 빈 폴더 ~/orca/dt_llm_empty 에서 실행(없으면 만들기), --output-format json + --json-schema 로 본 지시서 4장 형식 강제, 도구는 전부 금지(설치된 claude --help 로 플래그 확인), 결과는 structured_output 에서 읽기.
- 경로 2: 3-1 조건이면 ANTHROPIC_API_KEY 로 Messages API 호출. system 을 [고정 프롬프트, 교훈 메모] 두 블록으로 나누고 각 끝에 cache_control ephemeral ttl 1h. 최상위 자동 캐싱은 쓰지 마.
- 3-5 대로 usage 캐시 필드를 호출마다 기록하고, 캐시가 안 되면 알림, 일일 요약에 경로별 호출 수·캐시 적중률·추정 비용.
- 4장 환경변수를 bot/config.py 기본값과 .env.example 에 같이 추가.
- 4-2 호출 빈도대로: 빠른 점검(가벼운 모델, bot/prompts/dt_quick.md) 1·2·5분 간격 + 깊은 판단(큰 모델) 승격 규칙, 중복 실행 금지, 하루 상한과 API 비용 상한. 빠른 점검은 주문하지 못하게.
- 테스트:
  1) 경로 1 정상 호출 1회 (실제 claude -p 로, 판단 결과와 소요 시간 보여주기)
  2) 경로 1이 rate_limit 을 냈다고 가정한 가짜 테스트로 경로 2 전환, 1시간 뒤 경로 1 복귀
  3) 같은 시스템 프롬프트로 API 를 실제로 2번 불러서 첫 번째 cache_creation_input_tokens, 두 번째 cache_read_input_tokens 숫자를 보여주기
  4) 빠른 점검 keep/escalate 분기, 상황별 간격(1·2·5분) 전환, 이전 호출이 안 끝났을 때 건너뛰기, 하루 상한·API 비용 상한 도달 시 동작
  5) 실제 시세로 30분 동안 주문 없이 돌려서 빠른 점검·깊은 판단 횟수, 호출당 소요 시간, 추정 비용 보여주기
- 키 값은 출력하지 마. 커밋은 하지 말고 바뀐 파일 목록만 알려줘.
```

---

## 7. 출처
- 구독 로그인 `claude -p`·Agent SDK 사용 (2026-10-07 업데이트): https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan
- Claude Code 인증·이용 범위: https://code.claude.com/docs/en/legal-and-compliance
- `claude -p` 사용법(bare 모드, `--output-format json`, `--json-schema`, `--append-system-prompt-file`): https://code.claude.com/docs/en/headless
- Max·Ultra·Team 월 API 크레딧: https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers
- 프롬프트 캐싱(명시적 캐시 지점, 1시간 TTL, 최소 길이, usage 필드): https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- 가격표: https://platform.claude.com/docs/en/about-claude/pricing
