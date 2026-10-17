// The mock "LLM": routes a message, walks the graph and writes an answer or a storyline
// diff. It is deterministic keyword matching, enough to exercise every UI path.

import type {
  Citation,
  GraphEntity,
  GraphEvent,
  GraphRelation,
  Route,
  RetrievalTrace,
  StoryGraph,
} from "../types";

export interface OpDiff {
  add_entities: GraphEntity[];
  add_events: GraphEvent[];
  add_relations: GraphRelation[];
  hide: string[];
  modify: { key: string; description: string }[];
}

export interface Op {
  seq: number;
  reverted: boolean;
  diff: OpDiff;
  passage: string;
}

const STOP = new Set(
  "what if suppose imagine instead the a an and of to in on at was were had have has is are be been who why how when where which does did do this that with from for into never not about would could should she he they his her their them it its make let rewrite change".split(" "),
);

const words = (s: string) => s.toLowerCase().match(/[a-z']{4,}/g)?.filter((w) => !STOP.has(w)) ?? [];

export function classify(text: string, inStoryline: boolean): Route {
  const t = text.toLowerCase().trim();
  if (/^(what if|suppose|imagine|rewrite|change|make|let)\b/.test(t) || /\b(instead of|never|had not|hadn't|didn't)\b/.test(t)) return "edit";
  if (inStoryline && /\b(original|canon|in the book originally|real story)\b/.test(t)) return "canon";
  if (/\b(theme|themes|summar|overall|about|moral|message|main idea|big picture|whole book)\b/.test(t)) return "global";
  return "local";
}

function visible(g: StoryGraph) {
  const hidden = new Set(g.hidden);
  return {
    entities: g.entities.filter((e) => !hidden.has(e.key)),
    events: g.events.filter((e) => !hidden.has(e.key)),
    relations: g.relations.filter((r) => !hidden.has(r.src) && !hidden.has(r.dst) && !hidden.has(r.id)),
  };
}

const GENERIC_NAME_PARTS = new Set(["the", "of", "wicked", "witch", "good", "mr.", "mrs.", "miss", "captain", "great", "long", "white", "cowardly"]);

/** Entities named in `text`, in order of first mention so the sentence's subject leads. */
export function matchEntities(g: StoryGraph, text: string): GraphEntity[] {
  const t = text.toLowerCase();
  const scored: [GraphEntity, number, number][] = []; // entity, score, first position
  for (const e of visible(g).entities) {
    let score = 0;
    let pos = Infinity;
    const hit = (term: string, weight: number) => {
      const m = new RegExp(`\\b${escape(term)}\\b`).exec(t);
      if (m) {
        score = Math.max(score, weight);
        pos = Math.min(pos, m.index);
      }
    };
    for (const term of [e.name, ...e.aliases]) {
      const full = term.toLowerCase();
      hit(full, full.length * 2);
      for (const part of full.split(/\s+/)) if (part.length > 3 && !GENERIC_NAME_PARTS.has(part)) hit(part, part.length);
    }
    if (score) scored.push([e, score, pos]);
  }
  return scored.sort((a, b) => a[2] - b[2] || b[1] - a[1]).map(([e]) => e);
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function overlap(a: string, b: string) {
  const wb = new Set(words(b));
  return words(a).filter((w) => wb.has(w)).length;
}

function neighbours(g: StoryGraph, keys: string[]) {
  const set = new Set(keys);
  const v = visible(g);
  const edges = v.relations.filter((r) => set.has(r.src) || set.has(r.dst));
  const nodes = new Set<string>();
  edges.forEach((r) => (nodes.add(r.src), nodes.add(r.dst)));
  const events = v.events.filter((ev) => ev.involves.some((k) => set.has(k)));
  events.forEach((ev) => nodes.add(ev.key));
  keys.forEach((k) => nodes.delete(k));
  return { nodes: [...nodes], edges: edges.map((r) => r.id), events };
}

function cite(ev: GraphEvent, quotes: Record<string, string>): Citation {
  return ev.sid
    ? { chapter: ev.chapter, page: null, quote: ev.summary, storyline_chunk: true }
    : { chapter: ev.chapter, page: ev.first_page, quote: quotes[ev.key] ?? ev.summary };
}

const ref = (ev: GraphEvent) => (ev.sid ? "*this storyline*" : `${ev.chapter}, p. ${ev.first_page}`);

export interface Answer {
  content: string;
  citations: Citation[];
  trace: RetrievalTrace;
}

export function answerLocal(g: StoryGraph, question: string, quotes: Record<string, string>, canonOnly = false): Answer {
  const seeds = matchEntities(g, question).slice(0, 3);
  const steps = [
    { label: "Rewrote question", detail: question.replace(/\?*$/, "?") },
    { label: `Routed: ${canonOnly ? "canon" : "local"} search`, detail: canonOnly ? "Answering from the original book, ignoring storyline changes." : "Specific question, so start from matching entities." },
  ];
  if (!seeds.length) {
    // Widen once (keyword match on events), then give up, as the inference pipeline does.
    const v = visible(g);
    const ev = v.events.map((e) => [e, overlap(question, e.summary)] as const).filter(([, s]) => s > 0).sort((a, b) => b[1] - a[1])[0]?.[0];
    if (!ev) {
      return {
        content: "I couldn't find that in the book. Try naming a character, place or moment from the story.",
        citations: [],
        trace: { rewritten_question: question, seeds: [], expanded: [], edges: [], hops: 0, communities: [], steps: [...steps, { label: "Vector match", detail: "No entities matched." }, { label: "Widened search", detail: "Still nothing, so the answer is: not in the book." }] },
      };
    }
    seeds.push(...v.entities.filter((e) => ev.involves.includes(e.key)).slice(0, 2));
  }
  const keys = seeds.map((s) => s.key);
  const hood = neighbours(g, keys);
  const events = hood.events
    .map((e) => [e, overlap(question, e.summary) + (e.sid ? 2 : 0)] as const)
    .sort((a, b) => b[1] - a[1] || a[0].story_pos - b[0].story_pos)
    .slice(0, 4)
    .map(([e]) => e)
    .sort((a, b) => a.story_pos - b.story_pos);

  const lead = seeds[0];
  const ties = visible(g).entities.filter((e) => hood.nodes.includes(e.key)).slice(0, 4).map((e) => e.name);
  const lines = [
    `**${lead.name}**: ${lead.description}`,
    ...(seeds[1] ? [`\n**${seeds[1].name}**: ${seeds[1].description}`] : []),
    "",
    events.length ? "Here is what the story shows:" : "",
    ...events.map((e) => `- ${e.sid ? "In this storyline, " + lowerFirst(e.summary) : e.summary} (${ref(e)})`),
    ties.length ? `\nMost closely tied to ${list(ties)}.` : "",
  ];

  return {
    content: lines.filter((l) => l !== "").join("\n"),
    citations: events.map((e) => cite(e, quotes)),
    trace: {
      rewritten_question: question,
      seeds: keys,
      expanded: hood.nodes,
      edges: hood.edges,
      hops: 1,
      communities: [],
      steps: [
        ...steps,
        { label: "Vector match", detail: `Seed entities: ${list(seeds.map((s) => s.name))}.` },
        { label: "Expanded 1 hop", detail: `${hood.nodes.length} connected nodes over ${hood.edges.length} relations.` },
        { label: "Fetched source chunks", detail: `${events.length} passages, reranked by relevance.` },
        { label: "Grounding check", detail: "Every claim maps to a cited passage. Passed." },
      ],
    },
  };
}

export function answerGlobal(g: StoryGraph, question: string): Answer {
  const scored = g.communities.map((c) => [c, overlap(question, c.title + " " + c.summary)] as const).sort((a, b) => b[1] - a[1]);
  const picked = scored[0]?.[1] ? scored.filter(([, s]) => s > 0).map(([c]) => c) : g.communities;
  const v = visible(g);
  const members = v.entities.filter((e) => picked.some((c) => c.cid === e.community_id)).map((e) => e.key);
  const storyEvents = v.events.filter((e) => e.sid);
  const content = [
    `Taken as a whole, the story turns on ${picked.length === 1 ? "one main thread" : `${picked.length} threads`}:`,
    "",
    ...picked.map((c) => `**${c.title}**: ${c.summary}`),
    storyEvents.length ? `\nIn this storyline, things have shifted: ${storyEvents.map((e) => lowerFirst(e.summary).replace(/\.$/, "")).join("; ")}.` : "",
  ].filter(Boolean).join("\n");
  return {
    content,
    citations: [],
    trace: {
      rewritten_question: question,
      seeds: [],
      expanded: members,
      edges: v.relations.filter((r) => members.includes(r.src) && members.includes(r.dst)).map((r) => r.id),
      hops: 0,
      communities: picked.map((c) => c.cid),
      steps: [
        { label: "Rewrote question", detail: question.replace(/\?*$/, "?") },
        { label: "Routed: global search", detail: "A big-picture question, answered from community summaries." },
        { label: "Picked communities", detail: picked.map((c) => c.title).join(", ") },
        { label: "Map-reduce", detail: `${picked.length} partial answers merged into one.` },
      ],
    },
  };
}

export interface EditResult {
  ok: boolean;
  content: string;
  diff?: OpDiff;
  passage?: string;
  divergence_pos?: number;
  trace: RetrievalTrace;
}

const VERB_RELS: [RegExp, string][] = [
  [/\b(kill|defeat|destroy|melt|slay)/, "DEFEATS"],
  [/\b(love|marr|kiss|court)/, "LOVES"],
  [/\b(betray|abandon|desert)/, "BETRAYS"],
  [/\b(befriend|friend|join|ally|help|team)/, "ALLIES_WITH"],
  [/\b(fight|duel|battle|rival)/, "OPPOSES"],
];

export function applyEdit(g: StoryGraph, text: string, sid: string, opSeq: number): EditResult {
  const clause = text.trim().replace(/^(what if|suppose|imagine|rewrite it so|make it so|let)\s+/i, "").replace(/[?.!]+$/, "");
  const sentence = upperFirst(clause) + ".";
  const seeds = matchEntities(g, text).slice(0, 3);
  const known = new Set(g.entities.flatMap((e) => [e.name, ...e.aliases].flatMap((n) => n.split(/\s+/))));
  const newNames = [...new Set(clause.match(/(?<=\s)[A-Z][a-z]{2,}(?:\s[A-Z][a-z]{2,})?/g) ?? [])].filter(
    (n) => !n.split(" ").some((p) => known.has(p)) && !STOP.has(n.toLowerCase()),
  );
  const steps = [
    { label: "Routed: storyline edit", detail: "The message changes the plot, so it is parsed into ops." },
    { label: "Parsed ops", detail: `Entities touched: ${seeds.length ? list(seeds.map((s) => s.name)) : "none"}${newNames.length ? `; new: ${list(newNames)}` : ""}.` },
  ];
  const trace: RetrievalTrace = { rewritten_question: sentence, seeds: seeds.map((s) => s.key), expanded: [], edges: [], hops: 1, communities: [], steps };

  if (!seeds.length) {
    trace.steps.push({ label: "Consistency check", detail: "Failed: the edit names nobody from the story." });
    return { ok: false, content: "I couldn't anchor that change to the story. Name a character, place or moment, for example *\"What if the Scarecrow had a heart instead of a brain?\"*", trace };
  }

  const v = visible(g);
  const seedKeys = new Set(seeds.map((s) => s.key));
  const linked = v.events.filter((e) => e.involves.some((k) => seedKeys.has(k))).sort((a, b) => a.story_pos - b.story_pos);
  const divergence = linked.map((e) => [e, overlap(clause, e.summary)] as const).sort((a, b) => b[1] - a[1] || a[0].story_pos - b[0].story_pos)[0]?.[0] ?? linked[0];
  const negates = /\b(never|not|n't|instead|without|no longer|refuse)/i.test(clause);
  const stale = negates && divergence ? linked.filter((e) => e.story_pos >= divergence.story_pos).slice(0, 3) : [];
  const pos = (divergence?.story_pos ?? 0) + 0.5;

  const tag = { sid };
  const add_entities: GraphEntity[] = newNames.slice(0, 2).map((name) => ({
    key: `${sid}:${slug(name)}`,
    name,
    type: "person",
    aliases: [],
    description: `Introduced in this storyline. ${sentence}`,
    first_page: null,
    last_page: null,
    mention_count: 1,
    community_id: seeds[0].community_id,
    ...tag,
  }));
  const involves = [...seeds.map((s) => s.key), ...add_entities.map((e) => e.key)];
  const add_events: GraphEvent[] = [{ key: `${sid}:ev${opSeq}`, summary: sentence, story_pos: pos, chapter: divergence?.chapter ?? null, first_page: null, involves, ...tag }];
  const rel = VERB_RELS.find(([re]) => re.test(clause.toLowerCase()))?.[1] ?? (add_entities.length ? "MEETS" : "CROSSES_PATHS");
  const add_relations: GraphRelation[] = [];
  // Link to newcomers when there are any; otherwise relate the named characters to each other.
  const others = add_entities.length ? add_entities.map((e) => e.key) : seeds.slice(1).map((s) => s.key);
  others.forEach((dst, i) =>
    add_relations.push({ id: `${sid}:r${opSeq}.${i}`, src: seeds[0].key, dst, rel, description: sentence, weight: 1, ...tag }),
  );
  const modify = seeds.slice(0, 2).map((s) => ({ key: s.key, description: `${s.description} In this storyline: ${sentence}` }));

  const lead = seeds[0].name;
  const passage = [
    `${sentence} ${lead} felt the story tilt beneath ${seeds.length > 1 ? "them all" : "every step"}, like a page turned before its time.`,
    stale.length ? `What the old telling held now falls quietly away: ${stale.map((e) => lowerFirst(e.summary).replace(/\.$/, "")).join("; ")}.` : "",
    add_entities.length ? `And there, where no one had stood before, was ${list(add_entities.map((e) => e.name))}.` : `${lead} pressed on, into a chapter no reader had seen before.`,
  ].filter(Boolean).join(" ");

  const hood = neighbours(g, [...seedKeys]);
  trace.expanded = hood.nodes;
  trace.edges = hood.edges;
  trace.steps.push(
    { label: "Divergence point", detail: divergence ? `${divergence.chapter}, story position ${divergence.story_pos}: “${divergence.summary}”` : "Start of the book." },
    { label: "Impact analysis", detail: stale.length ? `${stale.length} later linked events marked stale (hidden, not deleted).` : "Additive change, so nothing goes stale." },
    { label: "Generated passage", detail: "Continuation written and extracted with the ingestion extractor." },
    { label: "Consistency check", detail: "No contradiction with active canon. Passed." },
    { label: "Applied ops", detail: `Op log written first, then the Falkor delta. Head is now op ${opSeq}.` },
  );

  const parts = [
    `Your storyline now diverges at **${divergence?.chapter ?? "the opening"}**.`,
    stale.length ? `I hid ${stale.length} event${stale.length > 1 ? "s" : ""} that no longer hold,` : "Nothing in canon had to be hidden. I",
    `added ${add_events.length + add_entities.length} node${add_events.length + add_entities.length > 1 ? "s" : ""}${add_relations.length ? ` and ${add_relations.length} relation${add_relations.length > 1 ? "s" : ""}` : ""}, and updated ${list(modify.map((m) => g.entities.find((e) => e.key === m.key)!.name))}.`,
  ];
  return { ok: true, content: parts.join(" "), diff: { add_entities, add_events, add_relations, hide: stale.map((e) => e.key), modify }, passage, divergence_pos: pos, trace };
}

/** Canon graph + applied ops up to `head`. Ops are the source of truth. */
export function materialize(canon: StoryGraph, ops: Op[], head: number): StoryGraph {
  const g: StoryGraph = structuredClone(canon);
  for (const op of ops) {
    if (op.reverted || op.seq > head) continue;
    const d = op.diff;
    g.entities.push(...d.add_entities);
    g.events.push(...d.add_events);
    g.relations.push(...d.add_relations);
    g.hidden.push(...d.hide);
    for (const m of d.modify) {
      const e = g.entities.find((x) => x.key === m.key);
      if (e) e.description = m.description;
    }
  }
  return g;
}

const OPENERS = new Set("one without together then after before but once so yet as while there here".split(" "));

/** A small graph from plain text: capitalised names become entities, sentences naming two become events. */
export function extractFromText(text: string): StoryGraph {
  const sentences = text.replace(/\s+/g, " ").match(/[^.!?]+[.!?]/g) ?? [];
  const counts = new Map<string, number>();
  for (const s of sentences) {
    // Runs of capitalised words ("Heron Queen"), minus sentence-opening filler ("Together Mira").
    for (const run of s.trim().match(/\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*/g) ?? []) {
      const words = run.split(/\s+/);
      while (words.length && (STOP.has(words[0].toLowerCase()) || OPENERS.has(words[0].toLowerCase()))) words.shift();
      const m = words.join(" ");
      if (m) counts.set(m, (counts.get(m) ?? 0) + 1);
    }
  }
  const names = [...counts.entries()].filter(([, c]) => c > 1).sort((a, b) => b[1] - a[1]).slice(0, 14).map(([n]) => n);
  const entities: GraphEntity[] = names.map((n, i) => ({
    key: slug(n), name: n, type: "person", aliases: [], description: `Appears ${counts.get(n)} times in the text.`,
    first_page: 1, last_page: null, mention_count: counts.get(n)!, community_id: (i % 3) + 1, sid: null,
  }));
  const events: GraphEvent[] = [];
  const relCount = new Map<string, number>();
  sentences.forEach((s, i) => {
    const inv = names.filter((n) => new RegExp(`\\b${n}\\b`).test(s));
    if (inv.length >= 2) {
      if (events.length < 16) events.push({ key: `ev${i}`, summary: s.trim(), story_pos: (i + 1) * 100, chapter: `¶${Math.floor(i / 8) + 1}`, first_page: Math.floor(i / 12) + 1, sid: null, involves: inv.map(slug) });
      for (let a = 0; a < inv.length; a++) for (let b = a + 1; b < inv.length; b++) {
        const k = `${slug(inv[a])}|${slug(inv[b])}`;
        relCount.set(k, (relCount.get(k) ?? 0) + 1);
      }
    }
  });
  const relations: GraphRelation[] = [...relCount.entries()].slice(0, 30).map(([k, w], i) => {
    const [src, dst] = k.split("|");
    return { id: `r${i}`, src, dst, rel: "APPEARS_WITH", description: `Together in ${w} sentence${w > 1 ? "s" : ""}.`, weight: w, sid: null };
  });
  return {
    entities, events, relations, hidden: [],
    communities: [1, 2, 3].map((cid) => ({ cid, title: `Thread ${cid}`, summary: `Characters: ${list(entities.filter((e) => e.community_id === cid).map((e) => e.name)) || "none"}.` })),
  };
}

export const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-");
/** Lowercases a leading article only, so sentences starting with a name keep it intact. */
const lowerFirst = (s: string) => (/^(A|An|The|In|On|At|It|Her|His|Their)\b/.test(s) ? s.charAt(0).toLowerCase() + s.slice(1) : s);
const upperFirst = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
export function list(xs: string[]) {
  return xs.length <= 1 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;
}
