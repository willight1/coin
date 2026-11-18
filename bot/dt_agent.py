"""
dt_agent.py — BTC 자율 에이전트 LLM 호출·출력 파싱·복기 (docs/BTC_DAYTRADE.md 4·9·11장)
========================================================================================
- 출력은 JSON 하나. 범위 밖 값은 잘라내고, 형식이 깨지거나 호출이 실패하면 플랜 유지(keep) + hold.
- 교훈 메모에서 한도를 늘리자는 문장은 걸러낸다(교훈은 리스크 가드 값을 바꿀 수 없다).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

from .config import DtConfig
from .dt_guard import clamp_stop
from .paths import BOT_DIR
from .strategies import LIBRARY, clamp_params

PROMPT_DIR = os.path.join(BOT_DIR, "prompts")
STRATEGIES = set(LIBRARY) | {"none", "keep"}
ACTIONS = {"follow_plan", "buy_now", "sell_now", "hold"}
REGIMES = {"상승추세", "하락추세", "횡보", "고변동", "이벤트대기"}


def load_prompt(name: str) -> str:
    with open(os.path.join(PROMPT_DIR, name), encoding="utf-8") as f:
        return f.read()


# 4장 출력 형식. claude -p --json-schema 와 API output_config 가 같은 스키마를 강제한다
_PARAM_KEYS = sorted({k for c in LIBRARY.values() for k in c.RANGES})
DECISION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["regime", "plan", "action", "action_size_pct", "confidence", "next_check_min", "reason", "easy"],
    "properties": {
        "regime": {"type": "string", "enum": sorted(REGIMES)},
        "plan": {"type": "object", "additionalProperties": False,
                 "required": ["strategy", "params", "size_pct", "stop_loss_pct", "reason"],
                 "properties": {
                     "strategy": {"type": "string", "enum": sorted(STRATEGIES)},
                     "params": {"type": "object", "additionalProperties": False,
                                "properties": {k: {"type": "number"} for k in _PARAM_KEYS}},
                     "size_pct": {"type": "number"}, "stop_loss_pct": {"type": "number"},
                     "reason": {"type": "string"}}},
        "action": {"type": "string", "enum": sorted(ACTIONS)},
        "action_size_pct": {"type": "number"}, "confidence": {"type": "number"},
        "next_check_min": {"type": "number"}, "reason": {"type": "string"}, "easy": {"type": "string"}}}
REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["good", "bad", "lessons_md", "strategy_notes"],
    "properties": {"good": {"type": "array", "items": {"type": "string"}},
                   "bad": {"type": "array", "items": {"type": "string"}},
                   "lessons_md": {"type": "string"}, "strategy_notes": {"type": "string"}}}
QUICK_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["verdict", "urgent_sell", "reason", "easy"],
    "properties": {"verdict": {"type": "string", "enum": ["keep", "escalate"]},
                   "urgent_sell": {"type": "boolean"}, "reason": {"type": "string"}, "easy": {"type": "string"}}}
# 하이퍼리퀴드 AI 운용역 그림자(bot/hl_ai.py). 매매에 쓰지 않는다
HL_MAIN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["calls", "easy"],
    "properties": {"calls": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["coin", "exposure", "reason"],
        "properties": {"coin": {"type": "string"}, "exposure": {"type": "number"}, "reason": {"type": "string"}}}},
        "easy": {"type": "string"}}}
# 하이퍼리퀴드 텔레그램 대화(bot/hl_ai.chat). command 는 봇이 형식을 다시 검사하고 /hl_yes 확인을 받는다
HL_CHAT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["reply", "command"],
                  "properties": {"reply": {"type": "string"}, "command": {"type": "string"}}}
SCHEMAS = {"dt_system.md": DECISION_SCHEMA, "dt_review.md": REVIEW_SCHEMA, "dt_quick.md": QUICK_SCHEMA, "hl_main.md": HL_MAIN_SCHEMA,
           "hl_chat.md": HL_CHAT_SCHEMA, "hl_trader.md": HL_MAIN_SCHEMA}
WITH_LESSONS = ("dt_system.md", "dt_quick.md")

# 경로 2 추정 비용 ($/MTok, 2026-11 가격표). 다른 모델은 비용 None
PRICES = {"claude-sonnet-5-5": {"in": 2.0, "cache_write_1h": 4.0, "cache_read": 0.20, "out": 10.0},
          "claude-haiku-5-5": {"in": 0.10, "cache_write_1h": 0.20, "cache_read": 0.01, "out": 0.50}}
# 서버 쪽 거절 대체(fallbacks)를 받는 모델. Haiku 5.5 는 없다
FALLBACK_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1")
HARD_FAIL = re.compile(r"rate.?limit|usage limit|429|authentication|unauthori[sz]ed|401|not logged in|login|"
                       r"billing|credit|payment|overloaded|529", re.I)
# claude -p 가 구독 대신 API 키로 과금되거나 다른 백엔드로 가지 않게 지우는 환경변수
_CLI_ENV_DROP = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                 "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                 "ANTHROPIC_WORKSPACE_ID")


class LlmError(Exception):
    def __init__(self, msg: str, hard: bool = False):
        super().__init__(msg)
        self.hard = hard          # rate_limit·인증·결제·과부하 → 바로 경로 2


def _claude_bin() -> str:
    return shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")


def call_cli(cfg: DtConfig, prompt_name: str, lessons: str, user: str, model: str,
             run=subprocess.run) -> tuple[str, dict]:
    """경로 1: Max 구독 로그인 claude -p. 빈 폴더에서, 도구·MCP·설정·스킬 없이."""
    os.makedirs(cfg.cli_workdir, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in _CLI_ENV_DROP}
    stdin = (f"[교훈 메모]\n{lessons.strip() or '(없음)'}\n\n" if prompt_name in WITH_LESSONS else "") + \
        f"[입력]\n{user}"
    cmd = [_claude_bin(), "-p", "아래 입력으로 판단하고 스키마대로만 답해",
           "--append-system-prompt-file", os.path.join(PROMPT_DIR, prompt_name),
           "--output-format", "stream-json", "--verbose", "--json-schema", json.dumps(SCHEMAS[prompt_name]),
           "--model", model, "--tools", "", "--strict-mcp-config", "--setting-sources", "",
           "--disable-slash-commands", "--no-session-persistence"]
    try:
        r = run(cmd, input=stdin, capture_output=True, text=True, cwd=cfg.cli_workdir, env=env,
                timeout=cfg.cli_timeout_sec)
    except subprocess.TimeoutExpired as e:
        raise LlmError(f"claude -p 시간 초과 {cfg.cli_timeout_sec}초") from e
    out = (r.stdout or "").strip()
    try:
        # stream-json: 줄마다 이벤트. 마지막 result 가 답, rate_limit_event 에 구독 사용률(5시간·7일)이 온다
        events = [json.loads(x) for x in out.splitlines() if x.strip().startswith("{")]
        j = next((e for e in reversed(events) if e.get("type") == "result"), events[-1])
    except (ValueError, IndexError):
        msg = (out + " " + (r.stderr or ""))[:300]
        raise LlmError(f"claude -p 응답 아님 (코드 {r.returncode}): {msg}", hard=bool(HARD_FAIL.search(msg)))
    if j.get("is_error") or j.get("subtype") != "success":
        msg = f"{j.get('subtype')} {j.get('api_error_status')} {str(j.get('result'))[:200]}"
        raise LlmError(f"claude -p 오류: {msg}", hard=bool(HARD_FAIL.search(msg)))
    so = j.get("structured_output")
    if so is None:
        raise LlmError("claude -p structured_output 없음")
    u = j.get("usage") or {}
    limits = {}
    for e in events:
        for name, w in ((e.get("rate_limit_info") or {}).get("unifiedWindows") or {}).items():
            if w.get("utilization") is not None:
                limits[name] = {"pct": round(w["utilization"] * 100, 1), "resets_at": w.get("resetsAt"),
                                "status": w.get("status")}
    return json.dumps(so, ensure_ascii=False), {"limits": limits,
        "route": "cli", "model": model, "in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0),
        "cache_creation": u.get("cache_creation_input_tokens", 0), "cache_read": u.get("cache_read_input_tokens", 0),
        "cost_usd": j.get("total_cost_usd"), "duration_ms": j.get("duration_ms")}


def call_api(cfg: DtConfig, prompt_name: str, lessons: str, user: str, model: str,
             client=None) -> tuple[str, dict]:
    """
    경로 2: Messages API. system = [고정 프롬프트, 교훈 메모] 두 블록, 각 끝에 1시간 캐시 지점.
    최상위 자동 캐싱은 쓰지 않는다 (매번 바뀌는 스냅샷에 지점이 붙어 재사용이 안 된다).
    고정 프롬프트에는 시각·가격·잔고를 넣지 않는다 — 글자 하나만 달라도 캐시가 새로 써진다.
    """
    import anthropic
    if client is None and not os.getenv("ANTHROPIC_API_KEY", "").strip():
        raise LlmError("ANTHROPIC_API_KEY 없음 (.env) — 경로 2 를 쓸 수 없다")
    # 워크스페이스에 묶이지 않은 조직 키는 요청마다 워크스페이스 ID 헤더가 필요하다 (없으면 400)
    ws = os.getenv("ANTHROPIC_WORKSPACE_ID", "").strip()
    client = client or anthropic.Anthropic(max_retries=1, timeout=120,
                                           default_headers={"anthropic-workspace-id": ws} if ws else None)
    system = [{"type": "text", "text": load_prompt(prompt_name), "cache_control": {"type": "ephemeral", "ttl": "1h"}}]
    if prompt_name in WITH_LESSONS:
        system.append({"type": "text", "text": "[교훈 메모]\n" + (lessons.strip() or "(없음)"),
                       "cache_control": {"type": "ephemeral", "ttl": "1h"}})
    try:
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} \
            if model in FALLBACK_MODELS else {}
        r = client.beta.messages.create(
            model=model, max_tokens=16000, system=system, messages=[{"role": "user", "content": user}],
            output_config={"effort": "low" if prompt_name == "dt_quick.md" else "medium",
                           "format": {"type": "json_schema", "schema": SCHEMAS[prompt_name]}}, **extra)
    except anthropic.APIStatusError as e:
        raise LlmError(f"API {e.status_code}: {str(e.message)[:200]}") from e
    except anthropic.APIConnectionError as e:
        raise LlmError(f"API 연결 실패: {e}") from e
    if r.stop_reason == "refusal":
        raise LlmError(f"API 거절 ({getattr(r.stop_details, 'category', None)})")
    text = "".join(b.text for b in r.content if b.type == "text")
    u = r.usage
    cw, cr = u.cache_creation_input_tokens or 0, u.cache_read_input_tokens or 0
    p = PRICES.get(model)
    cost = (u.input_tokens * p["in"] + cw * p["cache_write_1h"] + cr * p["cache_read"]
            + u.output_tokens * p["out"]) / 1e6 if p else None
    return text, {"route": "api", "model": r.model, "in": u.input_tokens, "out": u.output_tokens,
                  "cache_creation": cw, "cache_read": cr, "cost_usd": cost, "request_id": r._request_id}


class LlmRouter:
    """주 경로 → (조건이 되면) 반대 경로로 넘어가고, DT_FALLBACK_HOLD_MIN 뒤 주 경로 재시도.
    주 경로가 API 면 누적 비용이 DT_API_TOTAL_BUDGET_USD 에 닿는 순간부터 구독만 쓴다."""

    def __init__(self, cfg: DtConfig, notify=lambda t: None, now=None, cli=call_cli, api=call_api,
                 spent=lambda: 0.0):
        from datetime import datetime
        self.cfg, self.notify, self.cli, self.api, self.spent = cfg, notify, cli, api, spent
        self.budget_notified = False
        self.now = now or datetime.now
        self.fallback_until = None
        self.soft_fails = 0
        self.limit_errors = 0     # 경로 1 한도·인증 오류 누적 (일일 요약용)

    def _to_api(self, why: str) -> None:
        from datetime import timedelta
        self.fallback_until = self.now() + timedelta(minutes=self.cfg.fallback_hold_min)
        self.soft_fails = 0
        self.notify(f"LLM 경로 전환: 구독 claude -p → API ({why[:150]}). "
                    f"{self.cfg.fallback_hold_min:.0f}분 뒤 구독 재시도")

    def budget_over(self) -> bool:
        c = self.cfg
        return c.llm_primary == "api" and c.api_total_budget_usd > 0 and self.spent() >= c.api_total_budget_usd

    def route_now(self) -> str:
        """지금 쓰는 경로: api | claude_cli"""
        fb = bool(self.fallback_until and self.now() < self.fallback_until)
        if self.cfg.llm_primary == "api":
            return "claude_cli" if fb or self.budget_over() else "api"
        return "api" if fb else "claude_cli"

    def _api_first(self, prompt_name, lessons, user, model) -> tuple[str, dict]:
        from datetime import timedelta
        cfg = self.cfg
        if self.budget_over():
            if not self.budget_notified:
                self.budget_notified = True
                self.notify(f"API 예산 ${cfg.api_total_budget_usd:.0f}를 다 썼습니다. 지금부터 Claude 구독으로 판단합니다")
        elif not (self.fallback_until and self.now() < self.fallback_until):
            try:
                text, meta = self.api(cfg, prompt_name, lessons, user, model)
                if self.fallback_until:
                    self.fallback_until = None
                    self.notify("AI 연결 복귀: 구독 → API")
                return text, meta
            except LlmError as e:
                if cfg.llm_fallback == "none":
                    raise
                self.limit_errors += int(e.hard)
                self.fallback_until = self.now() + timedelta(minutes=cfg.fallback_hold_min)
                self.notify(f"AI 연결 전환: API → 구독 ({str(e)[:150]}). {cfg.fallback_hold_min:.0f}분 뒤 API 재시도")
        return self.cli(cfg, prompt_name, lessons, user, model)

    def __call__(self, prompt_name: str, lessons: str, user: str, model: str) -> tuple[str, dict]:
        cfg = self.cfg
        if cfg.llm_primary == "api":
            return self._api_first(prompt_name, lessons, user, model)
        use_api = (self.fallback_until and self.now() < self.fallback_until)
        if not use_api:
            try:
                text, meta = self.cli(cfg, prompt_name, lessons, user, model)
                if self.fallback_until:
                    self.fallback_until = None
                    self.notify("LLM 경로 복귀: API → 구독 claude -p")
                self.soft_fails = 0
                return text, meta
            except LlmError as e:
                if cfg.llm_fallback != "api":
                    raise
                self.limit_errors += int(e.hard)
                if not e.hard:
                    self.soft_fails += 1
                    if self.soft_fails < 2:
                        raise
                self._to_api(str(e))
        return self.api(cfg, prompt_name, lessons, user, model)


def _json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?|```$", "", text).strip()
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def _num(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return min(max(v, lo), hi)


HOLD_DECISION = {"regime": "", "plan": {"strategy": "keep"}, "action": "hold", "action_size_pct": 0,
                 "confidence": 0.0, "next_check_min": 15, "reason": "", "easy": ""}


def parse_decision(cfg: DtConfig, text: str | None) -> tuple[dict, list[str]]:
    """LLM 원문 → 정리된 판단, 손댄 것 목록. 깨지면 keep + hold."""
    notes: list[str] = []
    try:
        d = _json(text or "")
        if not isinstance(d, dict):
            raise ValueError("JSON 객체 아님")
    except Exception as e:
        return {**HOLD_DECISION, "reason": f"파싱 실패 → 플랜 유지: {e}",
                "easy": "AI 답을 읽지 못해서 하던 대로 유지해요."}, ["parse_error"]
    plan = d.get("plan") if isinstance(d.get("plan"), dict) else {}
    sid = str(plan.get("strategy", "keep"))
    if sid not in STRATEGIES:
        notes.append(f"strategy {sid!r} → keep")
        sid = "keep"
    out_plan = {"strategy": sid, "reason": str(plan.get("reason", ""))[:600]}
    if sid not in ("keep", "none"):
        params, clipped = clamp_params(LIBRARY[sid].RANGES, plan.get("params") if isinstance(plan.get("params"), dict) else {})
        notes += clipped
        out_plan["params"] = params
        out_plan["size_pct"] = _num(plan.get("size_pct"), 25, 100, 50)
        out_plan["stop_loss_pct"] = clamp_stop(cfg, plan.get("stop_loss_pct", 3.0))
    action = str(d.get("action", "hold"))
    if action not in ACTIONS:
        notes.append(f"action {action!r} → hold")
        action = "hold"
    regime = str(d.get("regime", ""))
    out = {"regime": regime if regime in REGIMES else "", "plan": out_plan, "action": action,
           "action_size_pct": _num(d.get("action_size_pct"), 0, 100, 0),
           "confidence": _num(d.get("confidence"), 0, 1, 0.0),
           "next_check_min": _num(d.get("next_check_min"), 1, 15, 15),
           "reason": str(d.get("reason", ""))[:800], "easy": str(d.get("easy", ""))[:300]}
    for k, lo, hi in (("action_size_pct", 0, 100), ("confidence", 0, 1), ("next_check_min", 1, 15)):
        raw = d.get(k)
        if isinstance(raw, (int, float)) and not lo <= raw <= hi:
            notes.append(f"{k}={raw}→{out[k]}")
    return out, notes


# ---------------------------------------------------------------- 교훈
_LOOSEN = re.compile(
    r"(예산|한도|손실\s*한도|손절\s*(폭|범위)?|거래\s*횟수|매수\s*횟수|비중|킬|드로다운|쿨다운|"
    r"budget|limit|stop|cap|DT_[A-Z_]+)"
    r".{0,25}(늘|올리|올려|높이|높여|키우|키워|완화|확대|풀|해제|무시|increase|raise|loosen|relax|disable)",
    re.I)


def filter_lessons(md: str, max_lines: int = 30) -> tuple[str, list[str]]:
    """한도 완화 문장을 지우고 30줄로 자른다. (정리된 메모, 지운 줄)"""
    keep, dropped = [], []
    for line in (md or "").splitlines():
        if not line.strip():
            continue
        (dropped if _LOOSEN.search(line) else keep).append(line.rstrip())
    return "\n".join(keep[:max_lines]), dropped


def parse_quick(text: str | None) -> dict:
    """빠른 점검 → {"verdict": keep|escalate, "urgent_sell": bool, "reason"}. 깨지면 keep."""
    try:
        d = _json(text or "")
        v = d.get("verdict")
        return {"verdict": v if v in ("keep", "escalate") else "keep",
                "urgent_sell": d.get("urgent_sell") is True, "reason": str(d.get("reason", ""))[:300],
                "easy": str(d.get("easy", ""))[:200]}
    except Exception as e:
        return {"verdict": "keep", "urgent_sell": False, "reason": f"파싱 실패 → keep: {e}",
                "easy": "점검 답을 읽지 못해서 하던 대로 둬요."}


def parse_review(text: str) -> dict:
    d = _json(text)
    return {"good": list(d.get("good", []))[:3], "bad": list(d.get("bad", []))[:3],
            "lessons_md": str(d.get("lessons_md", "")), "strategy_notes": str(d.get("strategy_notes", ""))}
