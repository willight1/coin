// 판단 하나의 전체 기록 (AI 원문 + AI 가 본 시장 자료). 펼쳤을 때만 부른다.
import { connection } from "next/server";
import path from "node:path";
import { coinDirs, readJsonl } from "@/lib/data";

export async function GET(request: Request) {
  await connection();
  const q = new URL(request.url).searchParams;
  const time = q.get("time");
  const rows = await readJsonl(path.join(coinDirs(q.get("coin")).log, "dt_decisions.jsonl"), 300);
  const hit = rows.find((r) => r.time === time);
  return hit ? Response.json(hit) : Response.json({ error: "기록을 찾지 못했어요" }, { status: 404 });
}
