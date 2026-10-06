// One append-only JSON-lines log per session under WTA_DATA_DIR. Nothing is ever rewritten:
// a request reads its session's log, folds it over the seed (claims.js), and appends the
// events its command produced. The Node event loop runs each request's synchronous
// read-fold-append to completion before the next, so a session's log is never interleaved.

import fs from "node:fs";
import path from "node:path";

const SID = /^s\d{6}$/;

export class EventStore {
  constructor(directory) {
    this.directory = path.join(directory, "sessions");
    fs.mkdirSync(this.directory, { recursive: true });
    // Session ids come from a counter, never from the clock or randomness. Continue after
    // any logs already in the directory so a restarted server never reuses an id.
    this.counter = fs.readdirSync(this.directory)
      .map((name) => /^s(\d{6})\.jsonl$/.exec(name))
      .filter(Boolean)
      .reduce((max, match) => Math.max(max, Number(match[1])), 0);
  }

  file(sid) {
    return path.join(this.directory, `${sid}.jsonl`);
  }

  exists(sid) {
    return SID.test(sid ?? "") && fs.existsSync(this.file(sid));
  }

  open(at) {
    this.counter += 1;
    const sid = `s${String(this.counter).padStart(6, "0")}`;
    fs.writeFileSync(this.file(sid), "", { flag: "wx" });
    this.append(sid, [{ type: "session_opened", at }], 0);
    return sid;
  }

  read(sid) {
    const text = fs.readFileSync(this.file(sid), "utf8");
    return text.split("\n").filter((line) => line.trim()).map((line) => JSON.parse(line));
  }

  /** Append `events` after the `length` events already read, numbering them on. */
  append(sid, events, length) {
    if (!events.length) return;
    const lines = events.map((event, index) => JSON.stringify({ seq: length + index + 1, ...event }));
    fs.appendFileSync(this.file(sid), `${lines.join("\n")}\n`, "utf8");
  }
}
