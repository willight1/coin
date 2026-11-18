// 시간대별 방향판 (참고용, 봇 매매에 안 씀). 계산은 봇과 같은 규칙 코드(bot/hl_tfboard.py)를 파이썬으로 부른다. 60초 캐시.
import { execFile } from "node:child_process";
import path from "node:path";
import { connection } from "next/server";
import { ROOT } from "@/lib/data";

let cache: { at: number; body: unknown } | null = null;
let running: Promise<unknown> | null = null;

function compute(): Promise<unknown> {
  return new Promise((resolve, reject) =>
    execFile(path.join(ROOT, "venv", "bin", "python"), ["-m", "bot.hl_tfboard", "BTC", "ETH"], { cwd: ROOT, timeout: 60_000 },
      (err, stdout) => { if (err) return reject(err); try { resolve(JSON.parse(stdout)); } catch (e) { reject(e); } }));
}

export async function GET() {
  await connection();
  if (cache && Date.now() - cache.at < 60_000) return Response.json(cache.body);
  try {
    running ??= compute().finally(() => { running = null; });
    const body = await running;
    cache = { at: Date.now(), body };
    return Response.json(body);
  } catch (e) {
    return Response.json({ error: `방향판 계산 실패: ${String(e).slice(0, 160)}` }, { status: 502 });
  }
}
