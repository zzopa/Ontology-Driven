import { parseEventLines } from "./query";
import type { StreamEvent } from "./types";

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { "Content-Type": "application/json", "X-Hbask-Request": "1", ...init?.headers } });
  const data = await response.json();
  if (!response.ok || data.ok === false) throw new Error(data.error || `请求失败 (${response.status})`);
  return data as T;
}

export async function streamQuestion(question: string, signal: AbortSignal, onEvent: (event: StreamEvent) => void, conversation_id?: string) {
  const response = await fetch("/api/ask/stream", { method: "POST", headers: { "Content-Type": "application/json", "X-Hbask-Request": "1" }, body: JSON.stringify({ question, conversation_id }), signal });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `请求失败 (${response.status})`);
  }
  if (!response.body) throw new Error("浏览器不支持流式响应");
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffer = "", resultReceived = false;
  try {
    while (true) {
      const chunk = await reader.read();
      buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !chunk.done });
      const parsed = parseEventLines(buffer, chunk.done);
      buffer = parsed.rest;
      for (const line of parsed.lines) {
        const event = JSON.parse(line) as StreamEvent;
        if (event.type === "error") throw new Error(event.message || "查询失败");
        if (event.type === "result") {
          if (!event.data?.ok) throw new Error("未收到有效查询结果");
          resultReceived = true;
        }
        onEvent(event);
      }
      if (chunk.done) break;
    }
    if (!resultReceived) throw new Error("连接中断，未收到完整查询结果。请重新查询。");
  } finally { reader.releaseLock(); }
}
