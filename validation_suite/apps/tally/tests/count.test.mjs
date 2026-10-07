// Unit tests of the count, the declaration and the poll's phase machine. No server needed:
// node --test tests/count.test.mjs

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import { countBallots } from "../src/count.js";
import { buildDeclaration } from "../src/declaration.js";
import * as polls from "../src/poll.js";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const SEED = JSON.parse(readFileSync(join(ROOT, "seed", "poll.json"), "utf8"));
const IDS = SEED.candidates.map((c) => c.id);
const letter = (id) => id.slice(-1).toUpperCase();

/** Rounds in the plan's notation: "A2 B3 C1 D5". */
const summary = (count) => count.rounds.map((round) =>
  Object.entries(round.tallies).map(([id, votes]) => `${letter(id)}${votes}`).join(" "));

const withExtra = (...extra) => [...SEED.ballots, ...extra.map((preferences, i) => ({ paper: 12 + i, preferences }))];

describe("the count (verified figures)", () => {
  test("seed: Dev elected 6 of 11 in round 3", () => {
    const count = countBallots(SEED.ballots, SEED.candidates);
    assert.deepEqual(summary(count), ["A2 B3 C1 D5", "A2 B4 D5", "B5 D6"]);
    assert.deepEqual(count.rounds.map((r) => r.outcome), [
      { kind: "eliminated", candidate: "cand-c" }, { kind: "eliminated", candidate: "cand-a" }, { kind: "elected", candidate: "cand-d" },
    ]);
    const last = count.rounds.at(-1);
    assert.deepEqual([last.leaderVotes, last.continuing, last.exhausted], [6, 11, 0]);
  });

  test("seed + (D): exactly half in round 1 is not a majority; Dev elected 7 of 12 in round 3", () => {
    const count = countBallots(withExtra(["cand-d"]), SEED.candidates);
    assert.deepEqual(summary(count), ["A2 B3 C1 D6", "A2 B4 D6", "B5 D7"]);
    assert.equal(count.rounds[0].leaderVotes * 2, count.rounds[0].continuing);
    assert.equal(count.rounds[0].outcome.kind, "eliminated");
    assert.deepEqual(count.result, { kind: "elected", candidate: "cand-d" });
  });

  test("seed + (B) + (A,B): Bram elected 7 of 13 although Dev led round 1", () => {
    const count = countBallots(withExtra(["cand-b"], ["cand-a", "cand-b"]), SEED.candidates);
    assert.deepEqual(summary(count), ["A3 B4 C1 D5", "A3 B5 D5", "B7 D6"]);
    assert.equal(count.rounds[0].leader, "cand-d");
    assert.deepEqual(count.result, { kind: "elected", candidate: "cand-b" });
    assert.deepEqual(buildDeclaration(count, SEED.candidates),
      { candidate: "cand-b", name: "Bram Lindqvist", votes: 7, continuing: 13, exhausted: 0, round: 3, ballots: 13 });
  });

  test("seed + (A): Ada goes out in round 2 and one ballot exhausts; Dev elected 6 of 11", () => {
    const count = countBallots(withExtra(["cand-a"]), SEED.candidates);
    assert.deepEqual(summary(count), ["A3 B3 C1 D5", "A3 B4 D5", "B5 D6"]);
    assert.deepEqual([count.rounds[2].continuing, count.rounds[2].exhausted], [11, 1]);
  });

  test("a final two-way tie elects nobody and cannot be declared", () => {
    const count = countBallots(withExtra(["cand-a", "cand-b"]), SEED.candidates);
    assert.deepEqual(summary(count), ["A3 B3 C1 D5", "A3 B4 D5", "B6 D6"]);
    assert.deepEqual(count.result, { kind: "tied", candidate: null });
    assert.equal(buildDeclaration(count, SEED.candidates), null);
  });

  test("elimination ties go to fewer first preferences, then later on the paper", () => {
    const ballots = [
      { paper: 1, preferences: ["cand-a"] }, { paper: 2, preferences: ["cand-b"] },
      { paper: 3, preferences: ["cand-c"] }, { paper: 4, preferences: ["cand-d"] },
      { paper: 5, preferences: ["cand-d"] },
    ];
    const count = countBallots(ballots, SEED.candidates);
    assert.deepEqual(count.rounds[0].outcome, { kind: "eliminated", candidate: "cand-c" });
    assert.deepEqual(count.rounds[1].outcome, { kind: "eliminated", candidate: "cand-b" });
  });

  test("every elected round in the verified counts has a strict majority", () => {
    for (const ballots of [SEED.ballots, withExtra(["cand-d"]), withExtra(["cand-b"], ["cand-a", "cand-b"]), withExtra(["cand-a"])]) {
      for (const round of countBallots(ballots, SEED.candidates).rounds) {
        if (round.outcome.kind === "elected") assert.ok(round.leaderVotes * 2 > round.continuing);
      }
    }
  });
});

describe("the poll", () => {
  test("the kiosk loop: issue, start, cast once", () => {
    const poll = polls.freshPoll(SEED);
    assert.equal(polls.issuePaper(poll, SEED.members).ok, true);
    assert.equal(polls.issuePaper(poll, SEED.members).ok, false, "paper 12 is still unused");
    assert.equal(polls.castBallot(poll, { paper: 12, preferences: ["cand-a"] }, IDS, 3).ok, false, "not started");
    assert.equal(polls.startBallot(poll, 12).ok, true);
    assert.equal(polls.castBallot(poll, { paper: 12, preferences: ["cand-a"] }, IDS, 3).ok, true);
    const again = polls.castBallot(poll, { paper: 12, preferences: ["cand-a"] }, IDS, 3);
    assert.deepEqual(again, { ok: false, message: ["kiosk.used", { paper: 12 }] });
    assert.equal(poll.ballots.length, 12);
    assert.equal(poll.papers.length, 12);
  });

  test("rankings: at least one, in order, nobody twice", () => {
    assert.deepEqual(polls.checkPreferences(["cand-b", "cand-a", ""], IDS, 3), { preferences: ["cand-b", "cand-a"] });
    assert.deepEqual(polls.checkPreferences(["", "", ""], IDS, 3), { problem: "kiosk.empty" });
    assert.deepEqual(polls.checkPreferences(["", "cand-a", ""], IDS, 3), { problem: "kiosk.gap" });
    assert.deepEqual(polls.checkPreferences(["cand-a", "cand-a", ""], IDS, 3), { problem: "kiosk.twice" });
    assert.deepEqual(polls.checkPreferences(["cand-x"], IDS, 3), { problem: "kiosk.bad_ranking" });
    assert.deepEqual(polls.checkPreferences("cand-a", IDS, 3), { problem: "kiosk.bad_ranking" });
  });

  test("close is refused while a voter is ranking; closing voids unused papers", () => {
    const poll = polls.freshPoll(SEED);
    polls.issuePaper(poll, SEED.members);
    polls.startBallot(poll, 12);
    assert.equal(polls.requestClose(poll).ok, false);
    const other = polls.freshPoll(SEED);
    polls.issuePaper(other, SEED.members);
    assert.equal(polls.requestClose(other).ok, true);
    assert.deepEqual(polls.confirmClose(other, SEED.candidates), { ok: true, message: ["flash.counted", { voided: 1 }] });
    assert.equal(other.papers[11].void, true);
    assert.equal(other.phase, "counted");
  });

  test("the declaration is published once, from the counted phase", () => {
    const poll = polls.freshPoll(SEED);
    assert.equal(polls.publishDeclaration(poll, SEED.candidates).ok, false);
    polls.requestClose(poll);
    assert.equal(polls.cancelClose(poll).ok, true);
    polls.requestClose(poll);
    polls.confirmClose(poll, SEED.candidates);
    assert.equal(polls.publishDeclaration(poll, SEED.candidates).ok, true);
    assert.equal(poll.declaration.name, "Dev Patel");
    assert.deepEqual(polls.publishDeclaration(poll, SEED.candidates), { ok: false, message: ["flash.already_declared", {}] });
  });
});
