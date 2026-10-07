// Unit tests for the register's pure rules (src/sightings.js) over the seed, no server needed.
// Run: node --test tests/

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, test } from "node:test";

import * as reg from "../src/sightings.js";

const seed = JSON.parse(readFileSync(new URL("../seed/register.json", import.meta.url), "utf8"));
const SITES = seed.sites.map((site) => site.id);
const fresh = () => seed.sightings.map((row) => ({ ...row }));
const refs = (rows) => rows.map((row) => reg.refOf(row.id));

/** Walk every page of a feed the way the client does; return the refs in the order listed. */
function walk(rows) {
  const listed = [];
  let page = reg.pageOf(rows);
  listed.push(...refs(page.rows));
  const sizes = [page.rows.length];
  while (page.more) {
    page = reg.pageOf(rows, reg.parseCursor(page.next));
    listed.push(...refs(page.rows));
    sizes.push(page.rows.length);
  }
  return { listed, sizes };
}

/** 1-based newest-first positions that share their date with the row after them. */
function tiePositions(rows) {
  return rows.slice(0, -1).flatMap((row, index) => (row.date === rows[index + 1].date ? [index + 1] : []));
}

describe("seed ordering", () => {
  test("22 verified sightings, newest first by (date, id)", () => {
    const rows = reg.matching(fresh(), "all");
    assert.equal(rows.length, 22);
    assert.ok(rows.every((row) => row.status === "verified"));
    assert.deepEqual(refs(rows), Array.from({ length: 22 }, (_, i) => `s${22 - i}`));
    assert.equal(rows[0].date, "2026-06-09");
    assert.equal(rows[21].date, "2026-05-20");
  });

  test("same-date pairs sit at positions 5/6, 11/12 and 17/18 and nowhere else", () => {
    const rows = reg.matching(fresh(), "all");
    assert.deepEqual(tiePositions(rows), [5, 11, 17]);
    for (const position of [5, 11, 17]) {
      assert.notEqual(rows[position - 1].site, rows[position].site, "each pair is at two different sites");
    }
  });

  test("no single-site view contains a same-date tie, and each has more than one page", () => {
    for (const site of SITES) {
      const rows = reg.matching(fresh(), site);
      assert.deepEqual(tiePositions(rows), [], site);
      assert.ok(rows.length > reg.PAGE_SIZE, `${site} has a second page`);
    }
  });

  test("s14 is on page 2 at Wood edge; s21 is on page 1 at Lakeside", () => {
    const rows = reg.matching(fresh(), "all");
    const page1 = reg.pageOf(rows);
    const page2 = reg.pageOf(rows, reg.parseCursor(page1.next));
    assert.ok(refs(page2.rows).includes("s14"));
    assert.equal(rows.find((row) => row.id === 14).site, "wood");
    assert.ok(refs(page1.rows).includes("s21"));
    assert.equal(rows.find((row) => row.id === 21).site, "lake");
  });
});

describe("keyset pages", () => {
  test("the seed loads as 6 + 6 + 6 + 4, every sighting once", () => {
    const { listed, sizes } = walk(reg.matching(fresh(), "all"));
    assert.deepEqual(sizes, [6, 6, 6, 4]);
    assert.equal(new Set(listed).size, 22);
  });

  test("one sighting dated today moves the first pair onto the 6/7 boundary, and nothing is skipped", () => {
    const sightings = fresh();
    sightings.push({ id: reg.nextId(sightings), date: "2026-06-10", site: "marsh", species: "heron", count: 1, notes: "", status: "verified" });
    const rows = reg.matching(sightings, "all");
    const first = reg.pageOf(rows);
    assert.equal(first.next, "2026-06-05.18", "page 1 ends inside the 5 June pair");
    const second = reg.pageOf(rows, reg.parseCursor(first.next));
    assert.equal(refs(second.rows)[0], "s17", "the pair's second row opens page 2");
    const { listed, sizes } = walk(rows);
    assert.deepEqual(sizes, [6, 6, 6, 5]);
    assert.deepEqual(listed, refs(rows));
  });

  test("a date-only cursor would lose s17 at that boundary (the defect keysets must avoid)", () => {
    const rows = reg.matching(fresh(), "all");
    const cursor = { date: "2026-06-05", id: 18 };
    assert.ok(refs(reg.after(rows, cursor)).includes("s17"));
    assert.ok(!refs(rows.filter((row) => row.date < cursor.date)).includes("s17"));
  });

  test("every filtered view visits each of its sightings once", () => {
    for (const site of SITES) {
      const rows = reg.matching(fresh(), site);
      assert.deepEqual(walk(rows).listed, refs(rows), site);
    }
  });

  test("cursors round-trip and malformed ones are rejected", () => {
    assert.deepEqual(reg.parseCursor("2026-05-30.11"), { date: "2026-05-30", id: 11 });
    for (const bad of ["", "2026-05-30", "2026-05-30.0", "x.11", "2026-5-30.11", null]) {
      assert.equal(reg.parseCursor(bad), null, String(bad));
    }
  });
});

describe("tallies", () => {
  test("the first page counts 17 birds: Marsh hide 5, Wood edge 8, Lakeside 4", () => {
    const page = reg.pageOf(reg.matching(fresh(), "all"));
    assert.deepEqual(reg.tallyOf(page.rows, SITES), { birds: 17, countedBirds: 17, bySite: { marsh: 5, wood: 8, lake: 4 } });
  });

  test("a doubtful row is in `birds` but not in `countedBirds` or its site", () => {
    const sightings = fresh();
    reg.review(sightings.find((row) => row.id === 14), "flag");
    const rows = reg.matching(sightings, "all");
    const second = reg.pageOf(rows, reg.parseCursor(reg.pageOf(rows).next));
    const tally = reg.tallyOf(second.rows, SITES);
    assert.equal(tally.birds, 14);
    assert.equal(tally.countedBirds, 11);
    assert.deepEqual(tally.bySite, { marsh: 7, wood: 0, lake: 4 });
    assert.equal(tally.countedBirds, Object.values(tally.bySite).reduce((a, b) => a + b, 0));
  });
});

describe("new sightings and review", () => {
  const rules = { site: "marsh", speciesIds: seed.species.map((kind) => kind.id), today: "2026-06-10" };
  const valid = { species: "heron", count: "1", seen_on: "2026-06-10", notes: "" };

  test("a complete sighting is accepted as verified", () => {
    assert.deepEqual(reg.checkNewSighting(valid, rules), {
      sighting: { date: "2026-06-10", site: "marsh", species: "heron", count: 1, notes: "", status: "verified" },
    });
  });

  test("each rule refuses with its own message", () => {
    const cases = [
      [{ species: "" }, "error.species"],
      [{ species: "eagle" }, "error.species"],
      [{ count: "0" }, "error.count"],
      [{ count: "999999999" }, "error.count"],
      [{ count: "" }, "error.count"],
      [{ count: "1.5" }, "error.count"],
      [{ seen_on: "" }, "error.date_format"],
      [{ seen_on: "2026-02-30" }, "error.date_format"],
      [{ seen_on: "2026-06-15" }, "error.date_future"],
      [{ seen_on: "9999-12-31" }, "error.date_future"],
      [{ seen_on: "0001-01-01" }, "error.date_early"],
      [{ notes: "A".repeat(2048) }, "error.notes"],
    ];
    for (const [change, problem] of cases) {
      assert.deepEqual(reg.checkNewSighting({ ...valid, ...change }, rules), { problem }, JSON.stringify(change).slice(0, 60));
    }
    assert.equal(reg.checkNewSighting({ ...valid, count: "500" }, rules).sighting.count, 500);
  });

  test("flag and restore each work once and are refused when already done", () => {
    const row = { id: 20, status: "verified" };
    assert.deepEqual(reg.review(row, "restore"), { changed: false, message: "error.already_verified" });
    assert.deepEqual(reg.review(row, "flag"), { changed: true, message: "flash.flagged" });
    assert.deepEqual(reg.review(row, "flag"), { changed: false, message: "error.already_doubtful" });
    assert.deepEqual(reg.review(row, "restore"), { changed: true, message: "flash.restored" });
    assert.equal(row.status, "verified");
  });
});
