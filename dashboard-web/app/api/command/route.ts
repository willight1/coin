// 봇에게 명령 보내기: 코인별 dt_commands.txt 에 한 줄 적으면 봇이 10초 안에 텔레그램 명령과 똑같이 처리한다.
// 정해 둔 명령만 받는다. 이 서버는 이 맥(127.0.0.1)에서만 열어야 한다 (전부 팔기가 있다).
import { promises as fs } from "node:fs";
import path from "node:path";
import { LOG_DIR, STATE_DIR, coinDirs, coins, readJsonl } from "@/lib/data";

const ALLOWED: Record<string, string[]> = {
  stop: ["/dt_stop"],
  go: ["/dt_go"],
  status: ["/dt_status"],
  flat: ["/dt_flat", "/dt_yes"],
};
// 하이퍼리퀴드 봇: state/hl_commands.txt (bot/hl_live.py 가 30초 안에 읽는다). 화면에서 확인 창을 거쳤으므로 /hl_yes 를 같이 보낸다
const HL_ALLOWED: Record<string, string[]> = {
  hl_status: ["/hl_status"],
  hl_half: ["/hl_half", "/hl_yes"],
  hl_wait: ["/hl_wait", "/hl_yes"],
  hl_stop: ["/hl_stop", "/hl_yes"],
  hl_go: ["/hl_go", "/hl_yes"],
};

export async function POST(request: Request) {
  // 다른 사이트가 브라우저를 통해 몰래 '전부 팔기'를 보내지 못하게: JSON 본문(사전 확인 요청이 붙는다) +
  // 요청 출처가 이 대시보드 주소와 같아야 한다.
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  if (!request.headers.get("content-type")?.startsWith("application/json") ||
      (origin && new URL(origin).host !== host)) {
    return Response.json({ error: "대시보드 화면에서만 보낼 수 있어요" }, { status: 403 });
  }
  let body: { action?: string; text?: string } = {};
  try {
    body = await request.json();
  } catch {
    /* 빈 본문 */
  }
  // 주문 대화창: 텔레그램에 쓰는 것과 같은 길. /hl_ 로 시작하면 그 명령 그대로, 아니면 /hl_ask 로 넘겨 봇의 AI 가 답한다.
  // 명령함은 한 줄 = 한 명령이라 줄바꿈을 지운다. 주문은 봇이 확인 문구를 보낸 뒤 화면의 '실행' 버튼(/hl_yes)을 눌러야 나간다.
  if (body.action === "hl_chat") {
    const text = String(body.text ?? "").replace(/\s+/g, " ").trim().slice(0, 300);
    if (!text) return Response.json({ error: "빈 메시지예요" }, { status: 400 });
    const chat = path.join(LOG_DIR, "hl_chat.jsonl");
    await fs.mkdir(STATE_DIR, { recursive: true });
    await fs.appendFile(chat, JSON.stringify({ t: Date.now() / 1000, who: "me", text }) + "\n", "utf8");
    await fs.appendFile(path.join(STATE_DIR, "hl_commands.txt"), (text.startsWith("/hl_") ? text : `/hl_ask ${text}`) + "\n", "utf8");
    return Response.json({ ok: true, chat: await readJsonl(chat, 60) });
  }
  const hl = HL_ALLOWED[body.action ?? ""];
  if (hl) {
    await fs.mkdir(STATE_DIR, { recursive: true });
    await fs.appendFile(path.join(STATE_DIR, "hl_commands.txt"), hl.map((c) => c + "\n").join(""), "utf8");
    return Response.json({ ok: true });
  }
  const cmds = ALLOWED[body.action ?? ""];
  if (!cmds) return Response.json({ error: "모르는 명령" }, { status: 400 });
  // 모든 코인 봇에 같은 명령 (멈추기·전부 팔기는 코인 전체)
  for (const k of await coins()) {
    const dir = coinDirs(k).state;
    await fs.mkdir(dir, { recursive: true });
    await fs.appendFile(path.join(dir, "dt_commands.txt"), cmds.map((c) => c + "\n").join(""), "utf8");
  }
  return Response.json({ ok: true });
}
