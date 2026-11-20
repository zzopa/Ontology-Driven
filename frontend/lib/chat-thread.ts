import type { Message } from "./admin";
import { stopSteps, updateStep } from "./execution.ts";
import type { ExecutionStep, QueryResult, StreamEvent } from "./types";

export type ClarificationRef = { clarification_of: string; choice_id?: string };

export type ChatTurn = {
  id: string;
  question: string;
  displayQuestion: string;
  result: QueryResult | null;
  error?: string;
  steps: ExecutionStep[];
  delta: string;
  elapsed: number;
  firstOutputElapsed?: number;
  progress: { stage: string; percent: number; message: string };
  clarification?: ClarificationRef;
  createdAt?: string;
  isHistorical?: boolean;
};

export function createChatTurn(
  id: string,
  question: string,
  displayQuestion: string,
  clarification?: ClarificationRef,
): ChatTurn {
  return {
    id,
    question,
    displayQuestion,
    result: null,
    error: "",
    steps: [{
      id: "connect",
      label: "连接业务数据库",
      state: "running",
      message: "请求已提交，等待服务器建立只读连接",
      details: [],
      started_at: 0,
      elapsed: 0,
    }],
    delta: "",
    elapsed: 0,
    progress: { stage: "connect", percent: 3, message: "正在准备查询…" },
    clarification,
  };
}

export function applyTurnEvent(turn: ChatTurn, event: StreamEvent, elapsed?: number): ChatTurn {
  if (event.type === "progress") {
    return {
      ...turn,
      progress: {
        stage: event.stage || "connect",
        percent: event.percent || 3,
        message: event.message || "正在处理…",
      },
    };
  }
  if (event.type === "step" && event.step) {
    return { ...turn, steps: updateStep(turn.steps, event.step) };
  }
  if (event.type === "answer_delta") {
    const delta = event.delta || "";
    return {
      ...turn,
      delta: turn.delta + delta,
      firstOutputElapsed: turn.firstOutputElapsed ?? (delta.trim() ? elapsed : undefined),
    };
  }
  if (event.type === "query_result" && event.section) {
    const item = event.section;
    const text = item.value == null ? item.result.answer : `${item.value} ${item.unit}`;
    return {
      ...turn,
      delta: turn.delta + `${item.label}：${text}${item.is_simulated ? "（模拟测试数据）" : ""}\n`,
      firstOutputElapsed: turn.firstOutputElapsed ?? elapsed,
    };
  }
  if (event.type === "answer_error") {
    return {
      ...turn,
      progress: { ...turn.progress, message: event.message || "正在生成事实摘要…" },
    };
  }
  if (event.type === "result" && event.data) {
    return {
      ...turn,
      result: event.data,
      steps: event.data.execution_trace || turn.steps,
      elapsed: elapsed ?? turn.elapsed,
      firstOutputElapsed: turn.firstOutputElapsed ?? elapsed,
    };
  }
  return turn;
}

export function failChatTurn(turn: ChatTurn, message: string, interrupted: boolean): ChatTurn {
  return {
    ...turn,
    error: message,
    steps: stopSteps(turn.steps, message, interrupted, turn.elapsed),
  };
}

export function resolvedChoiceId(turns: ChatTurn[], turnIndex: number): string | undefined {
  const turn = turns[turnIndex];
  const next = turns[turnIndex + 1];
  if (!turn?.result?.message_id || !next) return undefined;
  if (next.clarification?.clarification_of === turn.result.message_id) {
    return next.clarification.choice_id;
  }
  const matched = turn.result.clarification?.options.find(
    opt => opt.label === next.question || opt.label === next.displayQuestion,
  );
  return matched?.id;
}

export function turnsFromMessages(messages: Message[]): ChatTurn[] {
  return messages.map((m, idx) => ({
    id: m.id || `hist-${idx}`,
    question: m.question,
    displayQuestion: m.question,
    result: m.result || null,
    error: m.error || (m.status === "running" ? "此查询仍在后台执行，请稍后重新打开会话。" : ""),
    steps: m.execution_trace || m.result?.execution_trace || [],
    delta: "",
    elapsed: 0,
    progress: { stage: "done", percent: 100, message: m.result ? "查询已完成" : "查询未完成" },
    createdAt: m.created_at,
    isHistorical: true,
  }));
}
