// The sightings register: feed order, the keyset-paginated feed, bird tallies and the rules a
// new record or a review must satisfy. Pure functions over plain data; the server owns
// sessions, HTTP and HTML.
//
// A sighting is { id, date, site, species, count, notes, status } where `id` is a whole
// number (shown to people as "s<id>"), `date` an ISO day and `status` "verified" or
// "doubtful".

export const PAGE_SIZE = 6;
export const FIRST_DAY = "2026-01-01";
export const MAX_COUNT = 500;
export const MAX_NOTES = 500;

/** The reference people see and links use: 14 -> "s14". */
export function refOf(id) {
  return `s${id}`;
}

/** The record number behind a reference, or null when it is not one: "s14" -> 14. */
export function idFromRef(text) {
  const match = /^s([1-9]\d{0,5})$/.exec(String(text ?? ""));
  return match ? Number(match[1]) : null;
}

/** Feed order: the most recent day first, and on one day the later record first. */
export function newestFirst(a, b) {
  if (a.date !== b.date) return a.date < b.date ? 1 : -1;
  return b.id - a.id;
}

/** Every sighting matching the site filter ("all" or a site id), in feed order. */
export function matching(sightings, site) {
  return sightings.filter((row) => site === "all" || row.site === site).sort(newestFirst);
}

// -- keyset pagination ---------------------------------------------------------------------
//
// A page ends at a row; the cursor is that row's (date, id). The next page is everything
// strictly after the cursor in feed order, so a page boundary that falls between two
// records of the same day still lists each of them exactly once, and nothing depends on how
// many rows came before (no offsets, no page numbers).

export function cursorOf(row) {
  return `${row.date}.${row.id}`;
}

export function parseCursor(text) {
  const match = /^(\d{4}-\d{2}-\d{2})\.([1-9]\d{0,5})$/.exec(String(text ?? ""));
  return match ? { date: match[1], id: Number(match[2]) } : null;
}

/** The rows that follow cursor `c`; `rows` must already be in feed order. */
export function after(rows, c) {
  return rows.filter((r) => r.date < c.date || (r.date === c.date && r.id < c.id));
}

/** One page of a feed: its rows, whether more follow, and the cursor to ask for them with. */
export function pageOf(rows, cursor = null) {
  const remaining = cursor ? after(rows, cursor) : rows;
  const page = remaining.slice(0, PAGE_SIZE);
  const more = remaining.length > page.length;
  return { rows: page, more, next: more ? cursorOf(page[page.length - 1]) : null };
}

// -- tallies -------------------------------------------------------------------------------

/**
 * Birds on a set of listed rows. `birds` counts every row; `countedBirds` and `bySite`
 * count only verified rows, because a doubtful record is listed but never counted.
 */
export function tallyOf(rows, siteIds) {
  const bySite = Object.fromEntries(siteIds.map((site) => [site, 0]));
  let birds = 0;
  let countedBirds = 0;
  for (const row of rows) {
    birds += row.count;
    if (row.status !== "verified") continue;
    countedBirds += row.count;
    bySite[row.site] += row.count;
  }
  return { birds, countedBirds, bySite };
}

/** Verified sightings and birds per species for one month ("2026-06"), in species order. */
export function monthTotals(sightings, month, species) {
  return species.map((kind) => {
    const rows = sightings.filter((row) => row.species === kind.id && row.status === "verified"
      && row.date.startsWith(`${month}-`));
    return { species: kind, sightings: rows.length, birds: rows.reduce((sum, row) => sum + row.count, 0) };
  }).filter((line) => line.sightings > 0);
}

/** What the register holds for one site: counts, the latest day and the species seen. */
export function siteSummary(sightings, siteId, species) {
  const here = sightings.filter((row) => row.site === siteId).sort(newestFirst);
  const verified = here.filter((row) => row.status === "verified");
  return {
    sightings: here.length,
    birds: verified.reduce((sum, row) => sum + row.count, 0),
    latest: here.length ? here[0].date : null,
    species: species.filter((kind) => verified.some((row) => row.species === kind.id)),
  };
}

// -- new records ---------------------------------------------------------------------------

function isCalendarDay(text) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (!match) return false;
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
  const lengths = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return month >= 1 && month <= 12 && day >= 1 && day <= lengths[month - 1];
}

/**
 * Check a submitted sighting. Returns { problem: <vocabulary key> } for the first rule it
 * breaks, or { sighting } (without an id) when it may be recorded.
 */
export function checkNewSighting(form, { site, speciesIds, today }) {
  const species = String(form.species ?? "");
  const countText = String(form.count ?? "").trim();
  const day = String(form.seen_on ?? "").trim();
  const notes = String(form.notes ?? "").trim();
  if (!speciesIds.includes(species)) return { problem: "error.species" };
  const count = /^\d{1,6}$/.test(countText) ? Number(countText) : NaN;
  if (!(count >= 1 && count <= MAX_COUNT)) return { problem: "error.count" };
  if (!isCalendarDay(day)) return { problem: "error.date_format" };
  if (day > today) return { problem: "error.date_future" };
  if (day < FIRST_DAY) return { problem: "error.date_early" };
  if (notes.length > MAX_NOTES) return { problem: "error.notes" };
  return { sighting: { date: day, site, species, count, notes, status: "verified" } };
}

/** The id the next record gets: one more than any record so far. */
export function nextId(sightings) {
  return sightings.reduce((max, row) => Math.max(max, row.id), 0) + 1;
}

// -- review --------------------------------------------------------------------------------

const REVIEW = {
  flag: { from: "verified", to: "doubtful", done: "flash.flagged", refused: "error.already_doubtful" },
  restore: { from: "doubtful", to: "verified", done: "flash.restored", refused: "error.already_verified" },
};

/** Apply a review action to `row` in place. Returns the message key for what happened. */
export function review(row, action) {
  const rule = REVIEW[action];
  if (!rule) return { changed: false, message: "error.review" };
  if (row.status !== rule.from) return { changed: false, message: rule.refused };
  row.status = rule.to;
  return { changed: true, message: rule.done };
}
