import { AnimatePresence, motion } from "framer-motion";
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type Simulation, type SimulationNodeDatum } from "d3-force";
import { Eye, EyeOff, Maximize2, Minus, Plus, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphEntity, GraphEvent, StoryDiff, RetrievalTrace, StoryGraph } from "../api/types";
import { Badge } from "./ui";

/** What the graph is currently explaining. */
export type Overlay =
  | { kind: "trace"; trace: RetrievalTrace }
  | { kind: "diff"; diff: StoryDiff; trace?: RetrievalTrace }
  | null;

interface Node extends SimulationNodeDatum {
  key: string;
  label: string;
  kind: "entity" | "event";
  r: number;
  community: number | null;
  sid: string | null;
  hidden: boolean;
  data: GraphEntity | GraphEvent;
}
interface Link {
  id: string;
  source: string | Node;
  target: string | Node;
  label: string;
  kind: "rel" | "involves";
  sid: string | null;
}

// Muted hues that stay clear of the overlay colours (sage, rose, amber, violet).
const COMMUNITY_HUES = [32, 205, 345, 60, 185, 15];
const W = 800;
const H = 600;

export function GraphView({ graph, overlay, compact }: { graph: StoryGraph; overlay: Overlay; compact?: boolean }) {
  const [showEvents, setShowEvents] = useState(true);
  const [showHidden, setShowHidden] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [, setFrame] = useState(0);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const positions = useRef(new Map<string, { x: number; y: number }>());
  const sim = useRef<Simulation<Node, Link> | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const fitted = useRef(false);
  const drag = useRef<{ node?: Node; panFrom?: { x: number; y: number; vx: number; vy: number } } | null>(null);

  // Highlight sets derived from the overlay.
  const hl = useMemo(() => {
    const trace = overlay?.kind === "trace" ? overlay.trace : overlay?.trace;
    const diff = overlay?.kind === "diff" ? overlay.diff : null;
    const communityMembers = new Set(trace?.communities.length ? graph.entities.filter((e) => trace.communities.includes(e.community_id ?? -1)).map((e) => e.key) : []);
    // An answer with reasoning lights up just its chain: the entities and relations it names, and its events.
    const chain = trace?.reasoning?.chain ?? [];
    if (!diff && chain.length) {
      return {
        active: true,
        seeds: new Set(chain.flatMap((l) => (l.kind === "relation" ? [l.src_key, l.dst_key] : l.involves))),
        expanded: new Set(chain.flatMap((l) => (l.kind === "event" ? [l.key] : []))),
        edges: new Set(chain.flatMap((l) => (l.kind === "relation" ? [l.id] : []))),
        added: new Set<string>(),
        addedEdges: new Set<string>(),
        hidden: new Set<string>(),
        modified: new Set<string>(),
      };
    }
    return {
      active: !!overlay,
      seeds: new Set(trace?.seeds ?? []),
      expanded: new Set([...(trace?.expanded ?? []), ...communityMembers]),
      edges: new Set(trace?.edges ?? []),
      added: new Set(diff?.added ?? []),
      addedEdges: new Set(diff?.added_edges ?? []),
      hidden: new Set(diff?.hidden ?? []),
      modified: new Set(diff?.modified ?? []),
    };
  }, [overlay, graph]);

  const { nodes, links } = useMemo(() => {
    const hiddenSet = new Set(graph.hidden);
    const nodes: Node[] = [];
    for (const e of graph.entities) {
      const hidden = hiddenSet.has(e.key);
      if (hidden && !showHidden) continue;
      nodes.push({ key: e.key, label: e.name, kind: "entity", r: 7 + Math.min(14, Math.sqrt(e.mention_count) * 0.9), community: e.community_id, sid: e.sid, hidden, data: e });
    }
    if (showEvents) {
      for (const ev of graph.events) {
        const hidden = hiddenSet.has(ev.key);
        if (hidden && !showHidden && !hl.hidden.has(ev.key)) continue;
        nodes.push({ key: ev.key, label: ev.chapter ?? "", kind: "event", r: 5.5, community: null, sid: ev.sid, hidden, data: ev });
      }
    }
    const present = new Set(nodes.map((n) => n.key));
    const links: Link[] = graph.relations
      .filter((r) => present.has(r.src) && present.has(r.dst))
      .map((r) => ({ id: r.id, source: r.src, target: r.dst, label: r.rel, kind: "rel", sid: r.sid }));
    if (showEvents) {
      for (const ev of graph.events) {
        if (!present.has(ev.key)) continue;
        for (const k of ev.involves) if (present.has(k)) links.push({ id: `${ev.key}>${k}`, source: ev.key, target: k, label: "", kind: "involves", sid: ev.sid });
      }
    }
    // Reuse previous positions; place new nodes beside a neighbour so they "grow" out of it.
    const byKey = new Map(nodes.map((n) => [n.key, n]));
    for (const n of nodes) {
      const p = positions.current.get(n.key);
      if (p) Object.assign(n, p);
    }
    for (const n of nodes) {
      if (n.x !== undefined) continue;
      const l = links.find((l) => l.source === n.key || l.target === n.key);
      const other = l && byKey.get((l.source === n.key ? l.target : l.source) as string);
      const base = other?.x !== undefined ? other : { x: W / 2, y: H / 2 };
      n.x = base.x! + (Math.random() - 0.5) * 40;
      n.y = base.y! + (Math.random() - 0.5) * 40;
    }
    return { nodes, links };
  }, [graph, showEvents, showHidden, hl.hidden]);

  useEffect(() => {
    const fresh = nodes.some((n) => !positions.current.has(n.key));
    const s = forceSimulation<Node, Link>(nodes)
      .force("link", forceLink<Node, Link>(links).id((n) => n.key).distance((l) => (l.kind === "involves" ? 55 : 120)).strength((l) => (l.kind === "involves" ? 0.5 : 0.35)))
      .force("charge", forceManyBody<Node>().strength((n) => (n.kind === "event" ? -90 : -420)))
      .force("collide", forceCollide<Node>().radius((n) => n.r + (n.kind === "entity" ? 18 : 6)))
      .force("x", forceX(W / 2).strength(0.03))
      .force("y", forceY(H / 2).strength(0.045))
      .alpha(fresh ? 0.9 : 0.25)
      .alphaDecay(0.035)
      .on("tick", () => {
        for (const n of nodes) positions.current.set(n.key, { x: n.x!, y: n.y! });
        setFrame((f) => f + 1);
        // Frame the graph once the layout has mostly settled.
        if (!fitted.current && s.alpha() < 0.25) {
          fitted.current = true;
          fit();
        }
      });
    // New nodes (first load, an edit, a storyline switch) get the view re-framed around them.
    if (fresh) fitted.current = false;
    sim.current = s;
    return () => void s.stop();
  }, [nodes, links]);

  // Wheel zoom around the cursor.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const pt = toSvg(svg, e.clientX, e.clientY);
      setView((v) => {
        const k = clamp(v.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12), 0.4, 3);
        return { k, x: pt.x - ((pt.x - v.x) * k) / v.k, y: pt.y - ((pt.y - v.y) * k) / v.k };
      });
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, []);

  const onPointerDown = (e: React.PointerEvent, node?: Node) => {
    e.stopPropagation();
    (e.target as Element).setPointerCapture?.(e.pointerId);
    if (node) {
      drag.current = { node };
      node.fx = node.x;
      node.fy = node.y;
      sim.current?.alphaTarget(0.2).restart();
    } else {
      const pt = toSvg(svgRef.current!, e.clientX, e.clientY);
      drag.current = { panFrom: { x: pt.x, y: pt.y, vx: view.x, vy: view.y } };
    }
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const pt = toSvg(svgRef.current!, e.clientX, e.clientY);
    if (d.node) {
      d.node.fx = (pt.x - view.x) / view.k;
      d.node.fy = (pt.y - view.y) / view.k;
    } else if (d.panFrom) {
      setView((v) => ({ ...v, x: d.panFrom!.vx + pt.x - d.panFrom!.x, y: d.panFrom!.vy + pt.y - d.panFrom!.y }));
    }
  };
  const onPointerUp = () => {
    const n = drag.current?.node;
    if (n) {
      n.fx = null;
      n.fy = null;
      sim.current?.alphaTarget(0);
    }
    drag.current = null;
  };

  const fit = () => {
    const ns = sim.current?.nodes() ?? [];
    if (!ns.length) return;
    const xs = ns.map((n) => n.x!), ys = ns.map((n) => n.y!);
    const [x0, x1, y0, y1] = [Math.min(...xs) - 50, Math.max(...xs) + 50, Math.min(...ys) - 40, Math.max(...ys) + 50];
    const k = clamp(Math.min(W / (x1 - x0), H / (y1 - y0)), 0.4, 1.8);
    setView({ k, x: W / 2 - ((x0 + x1) / 2) * k, y: H / 2 - ((y0 + y1) / 2) * k });
  };

  const zoom = (f: number) => setView((v) => {
    const k = clamp(v.k * f, 0.4, 3);
    return { k, x: W / 2 - ((W / 2 - v.x) * k) / v.k, y: H / 2 - ((H / 2 - v.y) * k) / v.k };
  });

  const state = (key: string) => {
    if (hl.added.has(key)) return "added";
    if (hl.hidden.has(key)) return "stale";
    if (hl.modified.has(key)) return "modified";
    if (hl.seeds.has(key)) return "seed";
    if (hl.expanded.has(key)) return "expanded";
    return hl.active ? "dim" : "normal";
  };

  const sel = nodes.find((n) => n.key === selected);
  const nodeByKey = new Map(nodes.map((n) => [n.key, n]));
  const L = (x: string | Node) => (typeof x === "string" ? nodeByKey.get(x) : x);

  return (
    <div className="relative h-full w-full overflow-hidden">
      {/* faint star-chart grid */}
      <svg className="pointer-events-none absolute inset-0 h-full w-full opacity-[0.35]" aria-hidden>
        <defs>
          <pattern id="dots" width="28" height="28" patternUnits="userSpaceOnUse">
            <circle cx="1" cy="1" r="0.8" fill="var(--line)" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#dots)" />
      </svg>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="relative h-full w-full touch-none select-none"
        onPointerDown={(e) => onPointerDown(e)}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onClick={() => setSelected(null)}
        role="img"
        aria-label="Story knowledge graph"
      >
        <defs>
          <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="4" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        <g style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})`, transformOrigin: "0 0", transition: drag.current ? "none" : "transform 0.6s cubic-bezier(0.22, 1, 0.36, 1)" }}>
          {links.map((l) => {
            const s = L(l.source), t = L(l.target);
            if (!s || !t || s.x === undefined || t.x === undefined) return null;
            const walked = hl.edges.has(l.id) || (l.kind === "involves" && (hl.seeds.has(t.key) || hl.seeds.has(s.key)) && (hl.expanded.has(s.key) || hl.expanded.has(t.key)));
            const added = hl.addedEdges.has(l.id) || (l.kind === "involves" && hl.added.has(s.key));
            const stale = s.hidden || t.hidden;
            const color = added ? "var(--sage)" : walked ? "var(--violet)" : stale ? "var(--rose)" : "var(--line)";
            const opacity = hl.active && !walked && !added ? (stale ? 0.35 : 0.18) : stale ? 0.4 : l.kind === "involves" ? 0.55 : 0.9;
            return (
              <g key={l.id}>
                <line x1={s.x} y1={s.y} x2={t.x!} y2={t.y!} stroke={color} strokeOpacity={opacity} strokeWidth={walked || added ? 2 : l.kind === "involves" ? 0.8 : 1.2} strokeDasharray={l.kind === "involves" || stale ? "3 4" : undefined} style={{ transition: "stroke 0.4s, stroke-opacity 0.4s" }} />
                {(walked || added) && (
                  <line x1={s.x} y1={s.y} x2={t.x!} y2={t.y!} stroke={color} strokeWidth={2} strokeDasharray="2 14" strokeLinecap="round" className="animate-[flow_1.2s_linear_infinite]" />
                )}
                {l.kind === "rel" && (added || selected === s.key || selected === t.key || (walked && hl.edges.size <= 10)) && (
                  <text x={(s.x + t.x!) / 2} y={(s.y! + t.y!) / 2 - 4} textAnchor="middle" className="fill-ink-soft font-sans text-[8px] tracking-wider" style={{ paintOrder: "stroke", stroke: "var(--bg)", strokeWidth: 3 }}>
                    {l.label}
                  </text>
                )}
              </g>
            );
          })}

          {nodes.map((n) => (
            <GraphNode key={n.key} n={n} state={state(n.key)} selected={selected === n.key} onDown={(e) => onPointerDown(e, n)} onSelect={() => setSelected(n.key)} compact={compact} />
          ))}
        </g>
      </svg>

      {/* controls */}
      <div className="absolute right-3 top-3 flex flex-col gap-1.5">
        {[
          { icon: Plus, label: "Zoom in", on: () => zoom(1.25) },
          { icon: Minus, label: "Zoom out", on: () => zoom(0.8) },
          { icon: Maximize2, label: "Fit to view", on: fit },
        ].map(({ icon: I, label, on }) => (
          <button key={label} onClick={on} aria-label={label} title={label} className="rounded-lg border border-line bg-surface/80 p-1.5 text-ink-soft backdrop-blur hover:text-gold">
            <I className="size-3.5" />
          </button>
        ))}
      </div>
      <div className="absolute left-3 top-3 flex flex-wrap gap-1.5 text-[11px]">
        <Toggle on={showEvents} set={setShowEvents} label="Events" />
        <Toggle on={showHidden} set={setShowHidden} label="Hidden" icon={showHidden ? Eye : EyeOff} />
      </div>

      <Legend overlay={overlay} />

      <AnimatePresence>
        {sel && (
          <motion.div
            key={sel.key}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 10 }}
            className="card absolute bottom-3 right-3 w-[min(320px,calc(100%-24px))] p-4 text-sm"
            onClick={(e) => e.stopPropagation()}
          >
            <button onClick={() => setSelected(null)} className="absolute right-3 top-3 text-ink-mute hover:text-ink" aria-label="Close details">
              <X className="size-3.5" />
            </button>
            <NodeDetails n={sel} state={state(sel.key)} />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function GraphNode({ n, state, selected, onDown, onSelect, compact }: { n: Node; state: string; selected: boolean; onDown: (e: React.PointerEvent) => void; onSelect: () => void; compact?: boolean }) {
  if (n.x === undefined) return null;
  const hue = COMMUNITY_HUES[((n.community ?? 1) - 1) % COMMUNITY_HUES.length];
  const base = n.kind === "event" ? "var(--gold)" : `hsl(${hue} 24% 60%)`;
  const ring =
    state === "added" ? "var(--sage)" : state === "stale" ? "var(--rose)" : state === "modified" ? "var(--amber)" : state === "seed" || state === "expanded" ? "var(--violet)" : n.sid ? "var(--sage)" : "transparent";
  const dim = state === "dim" || (n.hidden && state !== "stale");
  const showLabel = n.kind === "entity" && (!compact || state !== "dim" || selected);
  return (
    <motion.g
      initial={{ opacity: 0, scale: 0 }}
      animate={{ opacity: dim ? 0.28 : 1, scale: 1 }}
      transition={{ type: "spring", stiffness: 200, damping: 16 }}
      style={{ x: n.x, y: n.y, cursor: "grab" }}
      onPointerDown={onDown}
      onClick={(e) => (e.stopPropagation(), onSelect())}
    >
      {(state === "seed" || state === "added") && (
        <motion.circle fill="none" stroke={ring} strokeWidth={1.5} initial={{ r: n.r + 4, opacity: 0.7 }} animate={{ r: [n.r + 4, n.r + 16], opacity: [0.7, 0] }} transition={{ duration: 1.8, repeat: Infinity, ease: "easeOut" }} />
      )}
      {n.kind === "entity" ? (
        <circle r={n.r} fill={base} fillOpacity={n.hidden ? 0.25 : 0.9} stroke={selected ? "var(--ink)" : ring} strokeWidth={selected ? 2 : 2.5} strokeDasharray={n.hidden ? "3 3" : undefined} filter={state === "seed" || state === "added" ? "url(#glow)" : undefined} />
      ) : (
        <rect x={-n.r} y={-n.r} width={n.r * 2} height={n.r * 2} transform="rotate(45)" fill={n.sid ? "var(--sage)" : base} fillOpacity={n.hidden ? 0.2 : 0.85} stroke={selected ? "var(--ink)" : ring} strokeWidth={1.8} strokeDasharray={n.hidden ? "2 2" : undefined} filter={state === "added" ? "url(#glow)" : undefined} />
      )}
      {n.hidden && <line x1={-n.r - 3} y1={0} x2={n.r + 3} y2={0} stroke="var(--rose)" strokeWidth={1.5} />}
      {showLabel && (
        <text y={n.r + 13} textAnchor="middle" className={`font-sans text-[10px] ${n.hidden ? "fill-rose line-through" : "fill-ink"}`} style={{ paintOrder: "stroke", stroke: "var(--bg)", strokeWidth: 3.5 }}>
          {n.label}
        </text>
      )}
    </motion.g>
  );
}

function NodeDetails({ n, state }: { n: Node; state: string }) {
  const stateBadge = {
    added: <Badge tone="sage">added in storyline</Badge>,
    stale: <Badge tone="rose">hidden as stale</Badge>,
    modified: <Badge tone="amber">modified</Badge>,
    seed: <Badge tone="violet">seed match</Badge>,
    expanded: <Badge tone="violet">reached by expansion</Badge>,
  }[state];
  if (n.kind === "entity") {
    const e = n.data as GraphEntity;
    return (
      <div className="space-y-2 pr-4">
        <div className="flex flex-wrap items-center gap-1.5">
          <h4 className="font-display text-xl font-semibold">{e.name}</h4>
          <Badge tone="mute">{e.type}</Badge>
          {stateBadge}
          {e.sid && state !== "added" && <Badge tone="sage">storyline</Badge>}
        </div>
        {e.aliases.length > 0 && <p className="text-xs text-ink-mute">Also: {e.aliases.join(", ")}</p>}
        <p className="font-serif text-ink-soft">{e.description}</p>
        {e.first_page !== null && (
          <p className="text-xs text-ink-mute">
            pp. {e.first_page}–{e.last_page ?? "?"} · {e.mention_count} mentions
          </p>
        )}
      </div>
    );
  }
  const ev = n.data as GraphEvent;
  return (
    <div className="space-y-2 pr-4">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone="gold">event</Badge>
        {ev.chapter && <Badge tone="mute">{ev.chapter}</Badge>}
        {stateBadge}
      </div>
      <p className={`font-serif ${n.hidden ? "text-ink-mute line-through" : "text-ink"}`}>{ev.summary}</p>
      <p className="text-xs text-ink-mute">
        story position {ev.story_pos}
        {ev.first_page ? ` · p. ${ev.first_page}` : " · generated passage"}
      </p>
    </div>
  );
}

function Legend({ overlay }: { overlay: Overlay }) {
  const items =
    overlay?.kind === "diff"
      ? [["var(--sage)", "added"], ["var(--rose)", "hidden (stale)"], ["var(--amber)", "modified"], ["var(--violet)", "used to place the edit"]]
      : overlay?.kind === "trace"
        ? overlay.trace.communities.length
          ? [["var(--violet)", "communities summarised"]]
          : [["var(--violet)", "seed + path walked"], ["var(--line)", "not used"]]
        : [["hsl(38 30% 62%)", "entity"], ["var(--gold)", "event"], ["var(--sage)", "storyline"]];
  return (
    <div className="absolute bottom-3 left-3 flex flex-wrap gap-x-3 gap-y-1 rounded-lg border border-line bg-surface/80 px-2.5 py-1.5 text-[11px] text-ink-soft backdrop-blur">
      {items.map(([c, l]) => (
        <span key={l} className="flex items-center gap-1.5">
          <span className="size-2 rounded-full" style={{ background: c }} />
          {l}
        </span>
      ))}
    </div>
  );
}

function Toggle({ on, set, label, icon: I }: { on: boolean; set: (v: boolean) => void; label: string; icon?: typeof Eye }) {
  return (
    <button onClick={() => set(!on)} className={`flex items-center gap-1 rounded-full border px-2.5 py-1 backdrop-blur transition ${on ? "border-gold/40 bg-gold-soft text-gold" : "border-line bg-surface/80 text-ink-mute"}`}>
      {I && <I className="size-3" />}
      {label}
    </button>
  );
}

function toSvg(svg: SVGSVGElement, cx: number, cy: number) {
  const r = svg.getBoundingClientRect();
  // viewBox uses "meet" scaling: same scale both axes, centred.
  const s = Math.min(r.width / W, r.height / H);
  return { x: (cx - r.left - (r.width - W * s) / 2) / s, y: (cy - r.top - (r.height - H * s) / 2) / s };
}

const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));
