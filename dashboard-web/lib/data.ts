// 서버 전용: 봇이 쓰는 파일(state/, logs/)을 읽는다. 봇(파이썬)과 같은 맥에서만 돈다.
import "server-only";
import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import path from "node:path";

// dashboard-web 은 coin 저장소 안에 있다. 다른 폴더(가상 운전 결과 등)는 DT_ROOT 로.
export const ROOT = process.env.DT_ROOT ?? path.resolve(process.cwd(), "..");
export const STATE_DIR = process.env.DT_STATE_DIR ?? path.join(ROOT, "state");
export const LOG_DIR = process.env.DT_LOG_DIR ?? path.join(ROOT, "logs");

// 코인마다 AI 봇이 따로 돈다: 비트코인은 state/·logs/, 다른 코인은 state/dt_<코인>/·logs/dt_<코인>/ (main.py --market)
export const COIN_KO: Record<string, string> = { BTC: "비트코인", ETH: "이더리움", XRP: "리플", SOL: "솔라나" };

export async function coins(): Promise<string[]> {
  const subs = await fs.readdir(/*turbopackIgnore: true*/ STATE_DIR, { withFileTypes: true }).catch(() => []);
  return ["BTC", ...subs.filter((e) => e.isDirectory() && /^dt_[A-Z0-9]+$/.test(e.name)).map((e) => e.name.slice(3)).sort()];
}

/** ?coin= 값 → 그 코인 봇의 폴더. 이상한 값은 비트코인으로. */
export function coinDirs(raw: string | null) {
  const coin = raw && /^[A-Z0-9]{1,12}$/.test(raw) ? raw : "BTC";
  const sub = coin === "BTC" ? "" : `dt_${coin}`;
  return { coin, coinKo: COIN_KO[coin] ?? coin, state: path.join(STATE_DIR, sub), log: path.join(LOG_DIR, sub) };
}

export async function readJson<T>(file: string, fallback: T): Promise<T> {
  try {
    return JSON.parse(await fs.readFile(file, "utf8")) as T;
  } catch {
    return fallback;
  }
}

export async function readText(file: string): Promise<string> {
  try {
    return await fs.readFile(file, "utf8");
  } catch {
    return "";
  }
}

/** 큰 jsonl 의 마지막 n 줄 (판단 기록은 하루 수 MB). 끝에서부터 1MB 씩 읽는다. */
export async function tailLines(file: string, n: number): Promise<string[]> {
  let fh;
  try {
    fh = await fs.open(file, "r");
  } catch {
    return [];
  }
  try {
    const size = (await fh.stat()).size;
    let end = size;
    let buf = Buffer.alloc(0);
    while (end > 0 && buf.toString("utf8").split("\n").length <= n + 1) {
      const step = Math.min(1 << 20, end);
      end -= step;
      const chunk = Buffer.alloc(step);
      await fh.read(chunk, 0, step, end);
      buf = Buffer.concat([chunk, buf]);
    }
    return buf.toString("utf8").split("\n").filter((l) => l.trim()).slice(-n);
  } finally {
    await fh.close();
  }
}

export async function readJsonl(file: string, n: number): Promise<Record<string, any>[]> {
  const out: Record<string, any>[] = [];
  for (const line of await tailLines(file, n)) {
    try {
      out.push(JSON.parse(line));
    } catch {
      /* 쓰는 중인 마지막 줄 */
    }
  }
  return out;
}

/** 파이썬 csv 모듈이 쓴 CSV. 따옴표 안의 쉼표·줄바꿈(AI 이유 문장)도 처리한다. */
export async function readCsv(file: string): Promise<Record<string, string>[]> {
  const text = await readText(file);
  const rows: string[][] = [];
  let row: string[] = [], cur = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"' && text[i + 1] === '"') { cur += '"'; i++; }
      else if (c === '"') q = false;
      else cur += c;
    } else if (c === '"') q = true;
    else if (c === ",") { row.push(cur); cur = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cur); cur = "";
      if (row.some((x) => x !== "")) rows.push(row);
      row = [];
    } else cur += c;
  }
  if (cur || row.length) { row.push(cur); rows.push(row); }
  if (rows.length < 2) return [];
  const [head, ...body] = rows;
  return body.map((r) => Object.fromEntries(head.map((h, i) => [h, r[i] ?? ""])));
}

// 봇 프로세스 목록. 화면이 5초마다 여러 라우트에서 묻기 때문에 pgrep 을 물을 때마다 띄우지 않고 2초 동안 같이 쓴다.
let procs: { at: number; lines: Promise<string[]> } | null = null;
export async function procRunning(pattern: string): Promise<boolean> {
  if (!procs || Date.now() - procs.at > 2000)
    procs = { at: Date.now(), lines: new Promise((resolve) => execFile("pgrep", ["-fl", "main.py --mode"], (_err, out) => resolve(String(out ?? "").split("\n").map((l) => l.trim())))) };
  const rx = new RegExp(pattern);
  return (await procs.lines).some((l) => rx.test(l));
}

export function agentRunning(coin = "BTC"): Promise<boolean> {
  // 비트코인 봇은 --market 없이 띄운 것도 센다
  return procRunning(coin === "BTC" ? "main.py --mode btc-agent( --market KRW-BTC)?$" : `main.py --mode btc-agent --market KRW-${coin}$`);
}

/** 50일선 봇(--mode trade)의 마지막 계좌 스냅샷: 로그의 "[계좌] TICK market=… equity=… krw=… coin=…" 줄. */
export async function tradeTick(): Promise<{ time: string; equity: number; krw: number; coin: number; market: string } | null> {
  const files = (await fs.readdir(/*turbopackIgnore: true*/ LOG_DIR).catch(() => []))
    .filter((f) => /^trade_\d{4}-\d{2}-\d{2}\.log$/.test(f)).sort().slice(-2).reverse();
  for (const f of files) {
    const lines = await tailLines(path.join(LOG_DIR, f), 400);
    for (let i = lines.length - 1; i >= 0; i--) {
      const m = lines[i].match(/^(\S+ \S+) .*\[계좌\] TICK market=(\S+) equity=([\d.]+) krw=([\d.]+) coin=([\d.]+)/);
      if (m) return { time: m[1], market: m[2], equity: +m[3], krw: +m[4], coin: +m[5] };
    }
  }
  return null;
}
