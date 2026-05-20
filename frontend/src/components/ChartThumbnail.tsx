import type { ChartMode } from "../types";

/**
 * 极简 SVG 缩略图 — 让 ChartSwitcher 14 种 mode 一眼可辨。
 * 不依赖数据；纯静态形状。
 */
export function ChartThumbnail({ mode, active = false, disabled = false }: { mode: ChartMode; active?: boolean; disabled?: boolean }) {
  const stroke = disabled ? "#cbd5e1" : active ? "#3b82f6" : "#64748b";
  const fill   = disabled ? "#e2e8f0" : active ? "#bfdbfe" : "#cbd5e1";

  const w = 40, h = 22;
  const base = { width: w, height: h, viewBox: `0 0 ${w} ${h}`, fill: "none" } as const;

  switch (mode) {
    case "table":
      return (
        <svg {...base}>
          <rect x="1" y="3" width="38" height="16" rx="2" stroke={stroke} />
          <line x1="1" y1="9" x2="39" y2="9" stroke={stroke} />
          <line x1="13" y1="3" x2="13" y2="19" stroke={stroke} />
          <line x1="26" y1="3" x2="26" y2="19" stroke={stroke} />
        </svg>
      );
    case "kpi":
      return (
        <svg {...base}>
          <rect x="6" y="4" width="28" height="14" rx="2" stroke={stroke} />
          <text x="20" y="14" textAnchor="middle" fontSize="9" fontWeight="700" fill={stroke}>168</text>
        </svg>
      );
    case "bar":
      return (
        <svg {...base}>
          <rect x="4"  y="11" width="5" height="8" fill={fill} />
          <rect x="12" y="6"  width="5" height="13" fill={fill} />
          <rect x="20" y="9"  width="5" height="10" fill={fill} />
          <rect x="28" y="4"  width="5" height="15" fill={fill} />
          <line x1="2" y1="19" x2="38" y2="19" stroke={stroke} />
        </svg>
      );
    case "bar_horizontal":
      return (
        <svg {...base}>
          <rect x="6" y="3"  height="3" width="22" fill={fill} />
          <rect x="6" y="8"  height="3" width="32" fill={fill} />
          <rect x="6" y="13" height="3" width="14" fill={fill} />
          <line x1="6" y1="3" x2="6" y2="19" stroke={stroke} />
        </svg>
      );
    case "line":
      return (
        <svg {...base}>
          <polyline points="2,16 9,12 16,14 23,7 30,9 38,4" stroke={stroke} strokeWidth="1.8" fill="none" />
          <circle cx="9" cy="12" r="1.6" fill={stroke} />
          <circle cx="23" cy="7" r="1.6" fill={stroke} />
          <circle cx="38" cy="4" r="1.6" fill={stroke} />
        </svg>
      );
    case "area":
      return (
        <svg {...base}>
          <path d="M2 18 L9 13 L16 15 L23 8 L30 10 L38 5 L38 20 L2 20 Z" fill={fill} opacity="0.6" />
          <polyline points="2,18 9,13 16,15 23,8 30,10 38,5" stroke={stroke} strokeWidth="1.5" fill="none" />
        </svg>
      );
    case "stacked_bar":
      return (
        <svg {...base}>
          <rect x="6"  y="11" width="5" height="8" fill={stroke} />
          <rect x="6"  y="5"  width="5" height="6" fill={fill} />
          <rect x="15" y="9"  width="5" height="10" fill={stroke} />
          <rect x="15" y="4"  width="5" height="5" fill={fill} />
          <rect x="24" y="7"  width="5" height="12" fill={stroke} />
          <rect x="24" y="3"  width="5" height="4" fill={fill} />
          <line x1="2" y1="19" x2="38" y2="19" stroke={stroke} />
        </svg>
      );
    case "dual_axis":
      return (
        <svg {...base}>
          <rect x="4"  y="11" width="5" height="8" fill={fill} />
          <rect x="12" y="8"  width="5" height="11" fill={fill} />
          <rect x="20" y="13" width="5" height="6" fill={fill} />
          <polyline points="6,5 14,8 22,4 30,9 36,3" stroke={stroke} strokeWidth="1.6" fill="none" />
        </svg>
      );
    case "pie":
      return (
        <svg {...base}>
          <circle cx="20" cy="11" r="8" stroke={stroke} fill={fill} />
          <path d="M20 11 L20 3 A8 8 0 0 1 27 14 Z" fill={stroke} opacity="0.55" />
          <path d="M20 11 L27 14 A8 8 0 0 1 12 14 Z" fill={stroke} opacity="0.3" />
        </svg>
      );
    case "rose":
      return (
        <svg {...base}>
          <g transform="translate(20 11)">
            <path d="M0 0 L0 -6 A6 6 0 0 1 5 -3 Z" fill={stroke} opacity="0.85"/>
            <path d="M0 0 L5 -3 A7 7 0 0 1 6 3 Z" fill={stroke} opacity="0.6"/>
            <path d="M0 0 L6 3 A8 8 0 0 1 -3 7 Z" fill={stroke} opacity="0.4"/>
            <path d="M0 0 L-3 7 A4 4 0 0 1 -4 -2 Z" fill={fill}/>
            <path d="M0 0 L-4 -2 A5 5 0 0 1 0 -6 Z" fill={stroke} opacity="0.7"/>
          </g>
        </svg>
      );
    case "funnel":
      return (
        <svg {...base}>
          <path d="M5 4 L35 4 L29 9 L11 9 Z" fill={fill}/>
          <path d="M11 10 L29 10 L25 14 L15 14 Z" fill={stroke} opacity="0.55"/>
          <path d="M15 15 L25 15 L22 19 L18 19 Z" fill={stroke}/>
        </svg>
      );
    case "scatter":
      return (
        <svg {...base}>
          <line x1="4" y1="19" x2="38" y2="19" stroke={stroke} />
          <line x1="4" y1="3" x2="4" y2="19" stroke={stroke} />
          {[[9,15],[14,11],[18,14],[22,9],[27,12],[30,6],[34,8]].map(([x,y],i)=>(
            <circle key={i} cx={x} cy={y} r="1.6" fill={stroke} />
          ))}
        </svg>
      );
    case "heatmap":
      return (
        <svg {...base}>
          {[0,1,2,3].map(r => [0,1,2,3,4,5].map(c => {
            const op = ((r * 6 + c) % 5) / 5 + 0.15;
            return <rect key={`${r}-${c}`} x={6 + c*5} y={4 + r*4} width="4.5" height="3.5" fill={stroke} opacity={op}/>;
          }))}
        </svg>
      );
    case "map":
      return (
        <svg {...base}>
          <path d="M5 6 L14 4 L22 7 L30 5 L36 8 L33 16 L24 18 L15 16 L8 17 Z" stroke={stroke} fill={fill}/>
          <circle cx="14" cy="10" r="1.4" fill={stroke}/>
          <circle cx="24" cy="12" r="1.4" fill={stroke}/>
        </svg>
      );
    default:
      return <svg {...base}/>;
  }
}
