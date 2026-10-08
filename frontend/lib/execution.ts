import type { ExecutionStep } from "./types.ts";

export function formatStepElapsed(seconds: number): string {
  return seconds < 1 ? `${Math.round(Math.max(0, seconds) * 1000)} ms` : `${seconds.toFixed(1)} s`;
}

export function updateStep(steps: ExecutionStep[], step: ExecutionStep): ExecutionStep[] {
  const index = steps.findIndex(item => item.id === step.id);
  if (index < 0) return [...steps, step];
  return steps.map((item, i) => i === index ? step : item);
}

export function stopSteps(steps: ExecutionStep[], message: string, interrupted = false): ExecutionStep[] {
  return steps.map(step => step.state === "running" ? {
    ...step, state: interrupted ? "interrupted" : "failed", message,
  } : step);
}
