interface Props {
  suggestions: string[];
  onPick: (q: string) => void;
  dataRange: [string, string];
  metricsCount: number;
  tablesCount: number;
}

function greeting(): string {
  const h = new Date().getHours();
  if (h < 11) return "上午好";
  if (h < 14) return "中午好";
  if (h < 18) return "下午好";
  return "晚上好";
}

export function Hero({ suggestions, onPick, dataRange, metricsCount, tablesCount }: Props) {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col items-center px-6 pt-12">
      <div className="qq-avatar mb-5">Q</div>
      <h1 className="text-[28px] font-semibold tracking-tight text-slate-800">
        {greeting()}，我是飞鹤小Q
      </h1>
      <p className="mt-2 text-sm text-slate-500">交给小Q，你可以相信我。</p>

      <div className="mt-3 flex flex-wrap items-center justify-center gap-2 text-[11px] text-slate-400">
        <span className="qq-pill-grey">数据范围 {dataRange[0]} ~ {dataRange[1]}</span>
        <span className="qq-pill-grey">{metricsCount} 个指标 · {tablesCount} 张表</span>
      </div>

      <div className="mt-8 grid w-full grid-cols-1 gap-2 sm:grid-cols-2">
        {suggestions.slice(0, 8).map((q, i) => (
          <button
            key={i}
            className="qq-card group flex items-center justify-between px-4 py-3 text-left transition hover:border-blue-200"
            onClick={() => onPick(q)}
          >
            <span className="truncate text-sm text-slate-700 group-hover:text-blue-600">{q}</span>
            <span className="ml-3 text-xs text-slate-300 group-hover:text-blue-400">立即问 →</span>
          </button>
        ))}
      </div>
    </div>
  );
}
