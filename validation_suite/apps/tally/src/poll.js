// The poll's phase machine: open -> closing -> counted -> declared (closing can go back to
// open; nothing else goes back). Pure functions over one session's poll; server.js reads the
// session, calls one of these and writes it back. Each answers {ok, message: [key, params]}.

import { countBallots } from "./count.js";
import { buildDeclaration } from "./declaration.js";

/** A fresh poll from the seed: papers 1..n issued to the first n members and used. */
export function freshPoll(seed) {
  return {
    phase: "open",
    papers: seed.ballots.map((ballot, index) => ({
      number: ballot.paper, member: seed.members[index].id, started: true, used: true, void: false,
    })),
    ballots: seed.ballots.map((ballot) => ({ paper: ballot.paper, preferences: [...ballot.preferences] })),
    kiosk: null,
    count: null,
    declaration: null,
  };
}

/** The paper at the kiosk (issued to the voter now voting), or null. */
export function kioskPaper(poll) {
  return poll.kiosk === null ? null : poll.papers.find((p) => p.number === poll.kiosk) ?? null;
}

const refuse = (key, params = {}) => ({ ok: false, message: [key, params] });
const accept = (key, params = {}) => ({ ok: true, message: [key, params] });

/** Next voter: issue a paper to the next member on the register who has none. */
export function issuePaper(poll, members) {
  if (poll.phase !== "open") return refuse("flash.not_open");
  const waiting = kioskPaper(poll);
  if (waiting && !waiting.used) return refuse("flash.paper_waiting", { paper: waiting.number });
  const member = members.find((m) => !poll.papers.some((p) => p.member === m.id));
  if (!member) return refuse("flash.all_issued");
  const number = poll.papers.length + 1;
  poll.papers.push({ number, member: member.id, started: false, used: false, void: false });
  poll.kiosk = number;
  return accept("flash.issued", { paper: number, member: member.name });
}

/** Start ballot: the voter begins ranking on the kiosk's paper. */
export function startBallot(poll, number) {
  const paper = kioskPaper(poll);
  if (poll.phase !== "open" || !paper || paper.number !== number) return refuse("kiosk.no_paper");
  if (paper.used) return refuse("kiosk.used", { paper: number });
  paper.started = true;
  return accept("kiosk.started", { paper: number });
}

/**
 * The ranking a voter sent: candidate ids for 1st, 2nd, 3rd ("" for none), at least one,
 * no gaps, nobody twice. Returns {preferences} or {problem: messageKey}.
 */
export function checkPreferences(raw, candidateIds, ranks) {
  if (!Array.isArray(raw) || raw.length > ranks) return { problem: "kiosk.bad_ranking" };
  const values = raw.map((value) => (typeof value === "string" ? value : ""));
  while (values.length && values[values.length - 1] === "") values.pop();
  if (!values.length) return { problem: "kiosk.empty" };
  if (values.includes("")) return { problem: "kiosk.gap" };
  if (values.some((value) => !candidateIds.includes(value))) return { problem: "kiosk.bad_ranking" };
  if (new Set(values).size !== values.length) return { problem: "kiosk.twice" };
  return { preferences: values };
}

/** Cast ballot: record the ranking on the voter's paper. One paper, one ballot. */
export function castBallot(poll, body, candidateIds, ranks) {
  if (poll.phase !== "open") return refuse("kiosk.closed");
  const paper = poll.papers.find((p) => p.number === body.paper && !p.used);
  if (!paper) {
    const known = poll.papers.some((p) => p.number === body.paper);
    return refuse(known ? "kiosk.used" : "kiosk.no_paper", { paper: body.paper });
  }
  if (!paper.started || paper.void) return refuse("kiosk.no_paper", { paper: paper.number });
  const ranking = checkPreferences(body.preferences, candidateIds, ranks);
  if (ranking.problem) return refuse(ranking.problem);
  poll.ballots.push({ paper: paper.number, preferences: ranking.preferences });
  paper.used = true;
  return accept("kiosk.cast", { paper: paper.number });
}

/** Close poll: allowed while no voter is part-way through a ballot. */
export function requestClose(poll) {
  if (poll.phase !== "open") return refuse("flash.not_open");
  const paper = kioskPaper(poll);
  if (paper && paper.started && !paper.used) return refuse("flash.voter_at_kiosk", { paper: paper.number });
  poll.phase = "closing";
  return accept("flash.closing");
}

export function cancelClose(poll) {
  if (poll.phase !== "closing") return refuse("flash.not_closing");
  poll.phase = "open";
  return accept("flash.reopened");
}

/** Papers issued but never used; closing voids them. */
export function unusedPapers(poll) {
  return poll.papers.filter((p) => !p.used && !p.void);
}

/** Confirm close: void unused papers, count once, and show the results. */
export function confirmClose(poll, candidates) {
  if (poll.phase !== "closing") return refuse("flash.not_closing");
  const voided = unusedPapers(poll);
  for (const paper of voided) paper.void = true;
  poll.kiosk = null;
  poll.count = countBallots(poll.ballots, candidates);
  poll.phase = "counted";
  return accept("flash.counted", { voided: voided.length });
}

/** Publish declaration: only from the counted phase, and only when someone was elected. */
export function publishDeclaration(poll, candidates) {
  if (poll.phase === "declared") return refuse("flash.already_declared");
  if (poll.phase !== "counted") return refuse("flash.not_counted");
  const declaration = buildDeclaration(poll.count, candidates);
  if (!declaration) return refuse("flash.tied");
  poll.declaration = declaration;
  poll.phase = "declared";
  return accept("flash.declared", { name: declaration.name });
}
