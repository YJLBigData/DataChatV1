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

// 哪些阶段实际等待大模型反馈（高亮显示）。
// plan = LLM 规划 QueryPlan；answer = LLM 生成中文 narrative。
const LLM_STAGES = new Set(["plan", "answer"]);

const STAGES_ORDER = ["cache", "retrieval", "plan", "compile", "guard", "execute", "answer"];

interface Props {
  events: StageEvent[];
  pending: boolean;
}

/** 把毫秒格式化成"秒"。<1s 显示一位小数，≥1s 显示两位有效数字。 */
function fmtSec(ms: number): string {
  if (!ms || ms <= 0) return "";
  const s = ms / 1000;
  if (s < 1) return `${s.toFixed(2)}s`;
  if (s < 10) return `${s.toFixed(2)}s`;
  return `${s.toFixed(1)}s`;
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
        const isLLM = LLM_STAGES.has(s);
        const sec = fmtSec(e.elapsed_ms);
        // cache 命中要把哪一层标出来 —— L1 精确 / L2(q2p) 同问题秒返 / L2(plan) 同 plan 复用
        let cacheTag = "";
        if (s === "cache" && e.status === "hit") {
          const layer = (e.payload && (e.payload as any).layer) || "";
          cacheTag = layer ? ` · ${layer} 命中` : " · 命中";
        } else if (s === "cache" && e.status === "miss") {
          cacheTag = " · 未命中";
        }
        return (
          <span key={s} className={`qq-stage-pill ${cls}`} title={isLLM ? "等待大模型反馈" : undefined}>
            <span className="qq-dot" style={{ background: "currentColor", opacity: 0.5 }} />
            {STAGE_LABEL[s] || s}
            {isLLM && <span className="ml-1 text-[10px] opacity-75">🤖</span>}
            {cacheTag}
            {sec ? ` · ${sec}` : null}
          </span>
        );
      })}
    </div>
  );
}
