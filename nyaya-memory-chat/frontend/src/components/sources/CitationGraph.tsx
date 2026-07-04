import { useJudgment } from "@/store/judgment";
import { useUI } from "@/store/ui";
import type { CorpusGraph, GraphNode } from "@/lib/types";

function palette(dark: boolean) {
  return dark
    ? {
        jFill: "#1F1B15",
        jStroke: "#B7A9F1",
        actFill: "#4A3C9A",
        overFill: "#3A1E1C",
        overStroke: "#F08A82",
        factFill: "#2A2440",
        factStroke: "#B7A9F1",
        youFill: "#B7A9F1",
        youText: "#16130E",
        edge: "#403930",
        label: "#C7BEAE",
        sub: "#8C8474",
      }
    : {
        jFill: "#FFFFFF",
        jStroke: "#1E2A4A",
        actFill: "#1E2A4A",
        overFill: "#FBE7E5",
        overStroke: "#B11717",
        factFill: "#ECEAF5",
        factStroke: "#1E2A4A",
        youFill: "#1E2A4A",
        youText: "#fff",
        edge: "#CFC8DC",
        label: "#5C5447",
        sub: "#928A7B",
      };
}

function layout(g: CorpusGraph) {
  const nodes = g.nodes;
  if (!nodes.length) return null;
  const deg: Record<string, number> = {};
  nodes.forEach((n) => (deg[n.id] = 0));
  g.edges.forEach((e) => {
    deg[e.src] = (deg[e.src] || 0) + 1;
    deg[e.dst] = (deg[e.dst] || 0) + 1;
  });
  const center =
    nodes.find((n) => n.kind === "you" || n.kind === "act") ??
    nodes.reduce((a, b) => (deg[b.id] > deg[a.id] ? b : a), nodes[0]);
  const others = nodes.filter((n) => n.id !== center.id);
  const cx = 180;
  const cy = 150;
  const R = 108;
  const pos: Record<string, { x: number; y: number }> = { [center.id]: { x: cx, y: cy } };
  others.forEach((n, i) => {
    const ang = (2 * Math.PI * i) / Math.max(1, others.length) - Math.PI / 2;
    pos[n.id] = { x: cx + R * Math.cos(ang), y: cy + R * Math.sin(ang) };
  });
  return { pos, center };
}

export function CitationGraphSvg() {
  const graph = useJudgment((s) => s.graph);
  const dark = useUI((u) => u.theme === "dark");
  const L = layout(graph);
  if (!L) {
    return (
      <div className="px-3 py-8 text-center text-[13px] leading-relaxed text-ink-3">
        Select a source to see its citation graph.
      </div>
    );
  }
  const P = palette(dark);

  const nodeStyle = (n: GraphNode) => {
    const you = n.kind === "you";
    const act = n.kind === "act";
    const over = n.kind === "over";
    const fact = n.kind === "fact";
    const r = you ? 22 : act ? 20 : 15;
    let fill = P.jFill;
    let stroke = P.jStroke;
    if (act) fill = P.actFill;
    else if (over) {
      fill = P.overFill;
      stroke = P.overStroke;
    } else if (fact) {
      fill = P.factFill;
      stroke = P.factStroke;
    } else if (you) fill = P.youFill;
    return { r, fill, stroke, you };
  };

  return (
    <svg viewBox="0 0 360 300" className="block h-auto w-full">
      {graph.edges.map((e, i) => {
        const a = L.pos[e.src];
        const b = L.pos[e.dst];
        if (!a || !b) return null;
        return (
          <line
            key={i}
            x1={a.x}
            y1={a.y}
            x2={b.x}
            y2={b.y}
            stroke={P.edge}
            strokeWidth={1.3}
            strokeOpacity={0.85}
          />
        );
      })}
      {graph.nodes.map((n) => {
        const p = L.pos[n.id];
        if (!p) return null;
        const st = nodeStyle(n);
        return (
          <g key={n.id}>
            <circle cx={p.x} cy={p.y} r={st.r} fill={st.fill} stroke={st.stroke} strokeWidth={1.6} />
            <text
              x={p.x}
              y={st.you ? p.y + 4 : p.y + st.r + 12}
              textAnchor="middle"
              fontFamily="Outfit, sans-serif"
              fontSize={9.5}
              fontWeight={700}
              fill={st.you ? P.youText : P.label}
            >
              {n.label}
            </text>
            {n.sub && (
              <text
                x={p.x}
                y={p.y + st.r + 22}
                textAnchor="middle"
                fontFamily="Outfit, sans-serif"
                fontSize={8}
                fill={P.sub}
              >
                {n.sub}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

export function GraphLegend() {
  const dark = useUI((u) => u.theme === "dark");
  const P = palette(dark);
  const items: Array<[string, string, string]> = [
    ["Root statute/case", P.actFill, P.actFill],
    ["Cited authority", P.jFill, P.jStroke],
    ["Overruled", P.overFill, P.overStroke],
  ];
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-3.5 border-t border-divider pt-3">
      {items.map(([label, fill, stroke]) => (
        <div key={label} className="flex items-center gap-1.5 text-[11px] text-ink-2">
          <span
            className="inline-block h-[11px] w-[11px] rounded-full"
            style={{ background: fill, border: `1.5px solid ${stroke}` }}
          />
          {label}
        </div>
      ))}
    </div>
  );
}
