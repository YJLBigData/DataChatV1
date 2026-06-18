/**
 * 轻量 markdown 渲染 —— 无依赖、纯 React 节点（避免 dangerouslySetInnerHTML 的 XSS 面）。
 * 支持：标题 / 无序+有序列表 / 表格 / **加粗** / 段落。专家团报告与专家产出共用。
 */
function inlineBold(text: string, keyPrefix: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) =>
    /^\*\*[^*]+\*\*$/.test(p)
      ? <strong key={`${keyPrefix}-b${i}`} className="font-semibold text-slate-800">{p.slice(2, -2)}</strong>
      : <span key={`${keyPrefix}-t${i}`}>{p}</span>,
  );
}

export function MiniMarkdown({ text }: { text: string }) {
  const lines = (text || "").replace(/\r/g, "").split("\n");
  const nodes: JSX.Element[] = [];
  let list: string[] = [];
  let tableRows: string[][] = [];
  const flushList = () => {
    if (!list.length) return;
    nodes.push(
      <ul key={`ul-${nodes.length}`} className="my-1 ml-4 list-disc space-y-0.5 text-[13px] text-slate-600">
        {list.map((it, i) => <li key={i}>{inlineBold(it, `li${nodes.length}-${i}`)}</li>)}
      </ul>,
    );
    list = [];
  };
  const flushTable = () => {
    if (!tableRows.length) return;
    const [head, ...body] = tableRows;
    nodes.push(
      <div key={`tb-${nodes.length}`} className="my-2 overflow-x-auto">
        <table className="w-full border-collapse text-[12px]">
          <thead><tr>{head.map((h, i) => <th key={i} className="border-b border-slate-200 px-2 py-1 text-left font-medium text-slate-500">{h}</th>)}</tr></thead>
          <tbody>{body.map((r, ri) => <tr key={ri}>{r.map((c, ci) => <td key={ci} className="border-b border-slate-100 px-2 py-1 text-slate-600">{c}</td>)}</tr>)}</tbody>
        </table>
      </div>,
    );
    tableRows = [];
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^\s*\|.*\|\s*$/.test(line)) {
      const cells = line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      if (cells.every((c) => /^-{2,}:?$|^:?-{2,}:?$/.test(c) || c === "")) continue;
      flushList();
      tableRows.push(cells);
      continue;
    }
    flushTable();
    if (/^#{1,6}\s/.test(line)) {
      flushList();
      const level = line.match(/^#+/)![0].length;
      const txt = line.replace(/^#+\s/, "");
      const cls = level <= 1 ? "text-[15px] font-semibold text-slate-800 mt-2"
        : level === 2 ? "text-[14px] font-semibold text-slate-800 mt-2"
        : "text-[13px] font-semibold text-slate-700 mt-1.5";
      nodes.push(<div key={`h-${nodes.length}`} className={cls}>{inlineBold(txt, `h${nodes.length}`)}</div>);
    } else if (/^\s*[-*]\s+/.test(line)) {
      list.push(line.replace(/^\s*[-*]\s+/, ""));
    } else if (/^\s*\d+\.\s+/.test(line)) {
      list.push(line.replace(/^\s*\d+\.\s+/, ""));
    } else if (line.trim() === "") {
      flushList();
    } else {
      flushList();
      nodes.push(<p key={`p-${nodes.length}`} className="my-1 text-[13px] leading-relaxed text-slate-600">{inlineBold(line, `p${nodes.length}`)}</p>);
    }
  }
  flushList(); flushTable();
  return <div className="qq-md">{nodes}</div>;
}
