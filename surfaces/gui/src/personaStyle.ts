// Per-persona presentation: the accent its session is tinted with, and the start screen +
// composer voice it introduces itself with.
//
// The problem this solves: every session looked identical, so nothing on screen told you which
// coworker you were talking to — the composer said "Ask the coworker…" and the start screen
// offered the default persona's three tasks (analyze a folder, HubSpot leads, GitHub→Slack) no
// matter who was answering. Those tasks are wrong for a code Builder and meaningless for a
// research briefer.
//
// Resolution order, per field: what the persona's manifest declares → the built-in defaults for
// the shipped personas → a family default. So a third-party persona that declares nothing still
// gets a coherent screen in its own colour, and one that declares an `intro:` block owns it.

import type {
  Persona,
  PersonaBudget,
  PersonaCheckpoint,
  PersonaIntro,
  PersonaStarter,
} from "./api";

// The curated accents, ordered around the wheel so neighbours in the list are the ones most
// likely to be confused — anything that has to pick several at once (accentMap) walks the list
// and naturally spreads. Names only: each resolves to a light/dark pair in styles.css, so a
// persona can never land on a colour that's illegible on one of the themes.
export const ACCENTS = [
  "cobalt",
  "indigo",
  "violet",
  "magenta",
  "rose",
  "amber",
  "lime",
  "green",
  "teal",
  "cyan",
  "slate",
] as const;
export type AccentName = (typeof ACCENTS)[number];

const isAccent = (v: string): v is AccentName => (ACCENTS as readonly string[]).includes(v);

// Accents for the shipped personas. Cowork keeps cobalt — the product's signature colour stays
// on the default persona, and everything else reads as a deliberate departure from it.
const BUILTIN_ACCENTS: Record<string, AccentName> = {
  cowork: "cobalt",
  code: "indigo",
  chat: "slate",
  ops: "teal",
};

/** What the persona's manifest asked for, if it named a curated accent. */
function declaredAccent(persona?: { accent?: string }): AccentName | undefined {
  const declared = (persona?.accent || "").toLowerCase();
  return isAccent(declared) ? declared : undefined;
}

/** The accent a persona gets on its own: the built-in table, else a stable hash of its id.
 *  Never cobalt — that colour keeps meaning "this is the default Coworker". */
function derivedAccent(id: string): AccentName {
  if (BUILTIN_ACCENTS[id]) return BUILTIN_ACCENTS[id];
  const pool = ACCENTS.filter((a) => a !== "cobalt");
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) % 100003;
  return pool[h % pool.length];
}

/** One persona's accent, considered alone: declared → built-in → derived. Use accentMap()
 *  instead wherever the full installed list is on hand — two personas resolve to the same
 *  colour often enough that "each persona has its own" needs the set, not the individual. */
export function accentFor(persona?: { id?: string; accent?: string }): AccentName {
  return declaredAccent(persona) ?? derivedAccent(persona?.id || "");
}

/**
 * Accents for the whole installed set, all distinct. Two personas landing on the same colour
 * defeats the point of tinting at all — and it happens easily: two manifests can declare the
 * same `accent:`, and the id hash collides.
 *
 * Three passes over the personas sorted by id (a stable order — the display order changes when
 * the default persona moves, and colours must not shuffle with it):
 *   1. declared accents, first claim wins;
 *   2. each remaining persona's derived accent, if still free;
 *   3. whatever is left, in palette order.
 * Past the palette's size, colours repeat — a duplicate beats an unstyled persona. Installing a
 * persona that claims a taken colour can shift another's; uniqueness is the property worth
 * keeping stable, not any one persona's hue.
 */
export function accentMap(personas?: readonly Persona[] | null): Record<string, AccentName> {
  const out: Record<string, AccentName> = {};
  if (!personas?.length) return out;
  const ordered = [...personas].sort((a, b) => a.id.localeCompare(b.id));
  const taken = new Set<AccentName>();
  const claim = (id: string, accent: AccentName) => {
    out[id] = accent;
    taken.add(accent);
  };

  const afterDeclared = ordered.filter((p) => {
    const want = declaredAccent(p);
    if (!want || taken.has(want)) return true;
    claim(p.id, want);
    return false;
  });
  const unplaced = afterDeclared.filter((p) => {
    const want = derivedAccent(p.id);
    if (taken.has(want)) return true;
    claim(p.id, want);
    return false;
  });
  let next = 0;
  for (const p of unplaced) {
    while (next < ACCENTS.length && taken.has(ACCENTS[next])) next++;
    claim(p.id, ACCENTS[next % ACCENTS.length]);
    next++;
  }
  return out;
}

const starter = (
  key: string,
  title: string,
  sub: string,
  prompt: string,
  requires: string[] = [],
): PersonaStarter => ({ key, title, sub, prompt, requires });

// Cowork's three rows (§27) — unchanged, just relocated: they are one persona's suggestions
// now, not the app's. "folder" is the pseudo-source meaning "a shared directory", handled by
// SessionIntro's add-folder flow; the others are connector ids.
const COWORK_INTRO: PersonaIntro = {
  greeting: "What should we produce?",
  lede:
    "Pick a task to start — I'll do the work and save the result. Or just type what you need below.",
  placeholder: "Ask the coworker…  (drop or paste files)",
  starters: [
    starter(
      "folder",
      "Analyze the files in a directory",
      "I'll read them and summarize what matters",
      "Analyze the files in this folder and summarize what matters.",
      ["folder"],
    ),
    starter(
      "hubspot",
      "Create a report from my HubSpot leads",
      "Sources, stages, and who needs follow-up",
      "Create a report on my recent HubSpot leads: sources, stages, and who needs follow-up.",
      ["hubspot"],
    ),
    starter(
      "github-slack",
      "Automate a weekly GitHub progress report to Slack",
      "Repo activity, summarized and posted every Friday",
      "Set up a weekly progress report: summarize activity in my GitHub repos and post it to Slack every Friday morning.",
      ["github", "slack"],
    ),
  ],
};

const BUILTIN_INTROS: Record<string, PersonaIntro> = {
  cowork: COWORK_INTRO,
  code: {
    greeting: "What are we building?",
    lede: "Point me at the change — I'll read the code first, then edit it.",
    placeholder: "Ask the coder to build, fix, or explain…  (drop or paste files)",
    starters: [
      starter(
        "orient",
        "Explain how this project fits together",
        "The entry points, the moving parts, and where things live",
        "Read this project and explain how it fits together: the entry points, the main components, and where things live.",
      ),
      starter(
        "tests",
        "Run the test suite and fix what fails",
        "I'll read the failures before touching anything",
        "Run the test suite, read the failures, and fix them one at a time.",
      ),
      starter(
        "review",
        "Review my uncommitted changes",
        "Bugs and loose ends in the current diff",
        "Review my uncommitted changes for bugs and loose ends, worst first.",
      ),
    ],
  },
  chat: {
    greeting: "How can I help?",
    lede: "",
    placeholder: "Ask anything…  (drop or paste files)",
    starters: [],
  },
};

// Family fallbacks — what a persona that declares no `intro:` gets. Deliberately generic: they
// must never pretend to know a persona's job, only its shape (a codebase vs. a deliverable).
const FAMILY_INTROS: Record<string, PersonaIntro> = {
  code: {
    greeting: "What are we building?",
    lede: "Point me at the change — I'll read the code first, then edit it.",
    placeholder: "Describe the change to make…  (drop or paste files)",
    starters: [
      starter(
        "orient",
        "Explain how this project fits together",
        "The entry points, the moving parts, and where things live",
        "Read this project and explain how it fits together: the entry points, the main components, and where things live.",
      ),
      starter(
        "tests",
        "Run the test suite and fix what fails",
        "I'll read the failures before touching anything",
        "Run the test suite, read the failures, and fix them one at a time.",
      ),
    ],
  },
  knowledge: {
    greeting: "What should we work on?",
    lede: "Describe the job — I'll do the work and leave the result behind as a file.",
    placeholder: "Describe the job…  (drop or paste files)",
    starters: [],
  },
};

/** The persona's start screen + composer voice, with each field falling back independently. */
export function introFor(persona?: Persona, personaId?: string): PersonaIntro {
  const id = persona?.id || personaId || "";
  const family = persona?.family || (id === "code" ? "code" : "knowledge");
  const base = BUILTIN_INTROS[id] || FAMILY_INTROS[family] || FAMILY_INTROS.knowledge;
  const declared = persona?.intro;
  if (!declared) return base;
  return {
    greeting: declared.greeting || base.greeting,
    lede: declared.lede || base.lede,
    placeholder: declared.placeholder || base.placeholder,
    // Starters are all-or-nothing: a persona that lists its own tasks means those INSTEAD of
    // the fallback's, never merged with tasks it never asked for.
    starters: declared.starters?.length ? declared.starters : base.starters,
  };
}

/** The composer placeholder for a persona — the one string that used to say "coworker" for all. */
export function placeholderFor(persona?: Persona, personaId?: string): string {
  return introFor(persona, personaId).placeholder;
}


// The shape of ONE job, per family, for personas that declare no checkpoints of their own.
// Deliberately coarse: a fallback must not pretend to know a persona's method, only whether its
// work ends in a changed repo or a written deliverable.
const cp = (id: string, label: string, evidence: string[]): PersonaCheckpoint => ({ id, label, evidence });

// `propose_plan` and `explore` are first-party tools (coworker/tools/plan.py, subagent.py) that
// evidence their step as plainly as todo_write and grep do; leaving them out pinned a run that
// planned and gathered through them at step one forever.
const FAMILY_CHECKPOINTS: Record<string, PersonaCheckpoint[]> = {
  code: [
    cp("recall", "Recall", ["brain_recall"]),
    cp("plan", "Plan", ["todo_write", "propose_plan"]),
    cp("locate", "Locate the change", ["grep", "read_file", "read_file_lines", "explore"]),
    cp("implement", "Implement", ["write_file", "replace_in_file", "apply_patch", "apply_unified_diff"]),
    cp("verify", "Verify", ["run_shell"]),
  ],
  knowledge: [
    cp("recall", "Recall", ["brain_recall"]),
    cp("plan", "Plan", ["todo_write", "propose_plan"]),
    cp("gather", "Gather", ["web_search", "web_fetch", "grep", "read_file", "read_file_lines", "run_shell", "explore"]),
    cp("produce", "Produce the deliverable", ["write_file"]),
    cp("record", "Record what lasts", ["brain_note"]),
  ],
};

// Capability -> the tools it provides, for pruning fallback steps a persona could never take.
// Only the ones a default checkpoint names; a persona that declares its own steps is trusted.
const CAPABILITY_TOOLS: Record<string, string[]> = {
  brain: ["brain_recall", "brain_note"],
};

/** A persona's job shape: what it declared, else its family's — minus any fallback step whose
 *  evidence the persona cannot produce.
 *
 *  Without the pruning, the default Coworker (no `brain` capability) was shown a "Recall" step
 *  it can never satisfy: permanently struck through as skipped, and permanently making the step
 *  after it look like the run's position. A checkpoint nothing can complete is worse than none.
 */
export function checkpointsFor(persona?: Persona, personaId?: string): PersonaCheckpoint[] {
  if (persona?.checkpoints?.length) return persona.checkpoints;
  const family = persona?.family || (personaId === "code" ? "code" : "knowledge");
  const steps = FAMILY_CHECKPOINTS[family] || FAMILY_CHECKPOINTS.knowledge;
  // Unknown tool list (persona still loading) → show the shape rather than an empty strip.
  if (!persona?.tools) return steps;
  const missing = new Set(
    Object.entries(CAPABILITY_TOOLS)
      .filter(([cap]) => !persona.tools.includes(cap))
      .flatMap(([, tools]) => tools),
  );
  return steps.filter((step) => step.evidence.some((t) => !missing.has(t)));
}

export type CheckpointState = "done" | "skipped" | "current" | "pending";

/**
 * Where the run has got to, judged from the tools it has actually called.
 *
 * A step is done once any of its evidence tools has been used. The CURRENT step is the first
 * unevidenced one at or after the furthest step reached; anything unevidenced BEFORE that was
 * skipped, and says so.
 *
 * Both halves matter. Calling the first gap "current" would have claimed a run with five files
 * edited was still at "Recall" — the panel contradicting its own activity line. Quietly
 * treating the furthest step as the position would hide that planning never happened, which is
 * exactly the failure worth seeing. Nothing called yet means step one is current, not done.
 */
export function checkpointProgress(
  checkpoints: PersonaCheckpoint[],
  toolNames: string[],
): { checkpoint: PersonaCheckpoint; state: CheckpointState }[] {
  const used = new Set(toolNames);
  const done = checkpoints.map((c) => c.evidence.some((t) => used.has(t)));
  const furthest = done.lastIndexOf(true);
  const currentIndex = done.findIndex((d, i) => !d && i > furthest);
  return checkpoints.map((checkpoint, i) => ({
    checkpoint,
    state: done[i]
      ? "done"
      : i === currentIndex
        ? "current"
        : i < furthest
          ? "skipped"
          : "pending",
  }));
}


export type BudgetUse = {
  budget: PersonaBudget;
  used: number;
  /** at = spent, over = past the ceiling. "near" starts at 75%: late enough to mean something,
   *  early enough to still change the run. */
  state: "ok" | "near" | "at" | "over";
};

/**
 * How much of each declared ceiling this run has spent.
 *
 * Counted from the session's tool calls, so it is what actually happened rather than what the
 * model believes it did — a model asked to stay under eight searches is not a reliable narrator
 * of how many it has run.
 */
export function budgetUse(persona: Persona | undefined, toolNames: string[]): BudgetUse[] {
  const budgets = persona?.budgets || [];
  if (!budgets.length) return [];
  return budgets.map((budget) => {
    // "*" is the total-call sentinel the manifest allows (seven automations declare a ceiling
    // on ALL tool calls, and enumerating a persona's whole toolset to express it would rot as
    // the catalog changes). Without this branch such a budget counts nothing and sits at 0/N
    // forever — reassuring, and wrong.
    const all = budget.tools.length === 1 && budget.tools[0] === "*";
    const used = all ? toolNames.length : toolNames.filter((t) => budget.tools.includes(t)).length;
    const ratio = used / budget.limit;
    const state: BudgetUse["state"] =
      used > budget.limit ? "over" : used === budget.limit ? "at" : ratio >= 0.75 ? "near" : "ok";
    return { budget, used, state };
  });
}
