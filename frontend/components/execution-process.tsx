"use client";

import { useEffect, useId, useState } from "react";
import { Check, ChevronDown, CircleAlert, Clock3, GitBranch, Pause, SkipForward } from "lucide-react";
import type { ExecutionStep } from "@/lib/types";
import { formatStepElapsed } from "@/lib/execution";
import { Badge, Card, Spinner } from "./ui";

const STATES = { running: "进行中", completed: "已完成", failed: "失败", skipped: "无需执行", interrupted: "停止等待" };

export function ExecutionProcess({ steps, busy = false, elapsed = 0 }: { steps: ExecutionStep[]; busy?: boolean; elapsed?: number }) {
  const [open, setOpen] = useState(true), id = useId();
  useEffect(() => { if (busy) setOpen(true); }, [busy]);
  const current = steps.find(step => step.state === "running");
  const failed = steps.some(step => step.state === "failed");
  const last = steps.at(-1);
  return <Card className={`execution-process ${busy ? "is-running" : ""}`}>
    <button className="process-toggle" onClick={() => setOpen(value => !value)} aria-expanded={open} aria-controls={id}>
      <span className="process-mark"><GitBranch size={15} /></span><span className="process-heading"><strong>查询执行过程</strong><small>{current ? `当前：${current.label} · ${current.message}` : last?.message || "此历史记录没有执行过程数据，重新查询后将完整记录"}</small></span>
      <Badge tone={failed ? "amber" : busy ? "green" : "neutral"}>{busy ? "实时更新" : failed ? "保留失败步骤" : `${steps.length} 个步骤`}</Badge><ChevronDown size={14} style={{ transform: open ? "rotate(180deg)" : "none" }} />
    </button>
    {open && <div id={id} className="process-body"><p className="process-caption">实际执行记录 · 显示本体、筛选条件、SQL 与核验结果，不展示模型内部推理。</p>
      {steps.length ? <ol className="process-list">{steps.map((step, index) => <li key={step.id} className={`process-step ${step.state}`}>
        <span className="process-step-icon" aria-label={STATES[step.state]}>{step.state === "running" ? <Spinner /> : step.state === "failed" ? <CircleAlert size={13} /> : step.state === "skipped" ? <SkipForward size={12} /> : step.state === "interrupted" ? <Pause size={12} /> : <Check size={13} />}</span>
        <div className="process-step-content"><div className="process-step-heading"><strong><span>{String(index + 1).padStart(2, "0")}</span>{step.label}</strong><span className="process-step-time"><Clock3 size={10} />{formatStepElapsed(step.state === "running" && busy ? Math.max(0, elapsed - step.started_at) : step.elapsed)} · {STATES[step.state]}</span></div><p>{step.message}</p>
          {!!step.details?.length && <details className="process-details"><summary>查看执行信息 <ChevronDown size={11} /></summary><div>{step.details.map((detail, i) => <pre key={i}>{detail}</pre>)}</div></details>}
        </div>
      </li>)}</ol> : <p className="process-caption">旧记录没有保存这些步骤，不能事后伪造。请重新提交问题。</p>}
    </div>}
  </Card>;
}
