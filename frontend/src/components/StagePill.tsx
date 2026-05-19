import type { StageEvent } from "../types";

const STAGE_LABEL: Record<string, string> = {
  session: "建立会话",
  cache: "缓存查询",
  retrieval: "语义召回",
  plan: "意图规划",
  compile: "编译 SQL",
  guard: "安全审查",
  execute: "执行查询",
  answer: "整理回复",
  clarify: "需要澄清",
};

const STAGES_ORDER = ["cache", "retrieval", "plan", "compile", "guard", "execute", "answer"];

interface Props {
  events: StageEvent[];
  pending: boolean;
}

export function StagePill({ events, pending }: Props) {
  const lastByStage: Record<string, StageEvent> = {};
  for (const e of events) lastByStage[e.stage] = e;
  const finished = !pending;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {pending && (
        <span className="qq-stage-pill qq-stage-active">
          <span className="qq-loading-dot" />
          <span className="qq-loading-dot" />
          <span className="qq-loading-dot" />
          <span className="ml-1">小Q 正在思考</span>
        </span>
      )}
      {STAGES_ORDER.map((s) => {
        const e = lastByStage[s];
        if (!e) return null;
        const cls =
          e.status === "error"
            ? "qq-stage-error"
            : finished
              ? "qq-stage-done"
              : "qq-stage-active";
        return (
          <span key={s} className={`qq-stage-pill ${cls}`}>
            <span className="qq-dot" style={{ background: "currentColor", opacity: 0.5 }} />
            {STAGE_LABEL[s] || s}
            {e.elapsed_ms ? ` · ${e.elapsed_ms}ms` : null}
          </span>
        );
      })}
    </div>
  );
}
