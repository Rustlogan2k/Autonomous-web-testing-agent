"use strict";
/*
 * Orbit's collections: their `$jsonSchema` validators (strict, error) and indexes. Every
 * session database is created from this table before its seed is inserted, so a document
 * the app writes is checked by the server, not only by the code that wrote it.
 *
 *   users       accounts (credentials are PBKDF2 hashes; never sent to the browser)
 *   workspaces  the studio and who belongs to it                    member_ids -> users
 *   projects    a board: its columns (WIP limits), members and roles workspace_id -> workspaces
 *                                                                    members.user_id -> users
 *   cards       one card with its checklist, comments and history   project_id -> projects
 *                                                                    blocked_by -> cards
 *               (an aggregate: everything the card modal shows)     assignee_ids, comments.author_id,
 *                                                                    activity.by -> users
 *   counters    one document: the last card number, board position and event number
 *
 * The rules that span documents (WIP limits, "blocked until its dependency ships") are the
 * domain's (board.js): a validator sees one document at a time.
 */

const COLUMNS = ["backlog", "doing", "review", "shipped"];
const ROLES = ["owner", "member", "viewer"];
const ISO_MINUTE = "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}$";
const TITLE_LIMIT = 120;
const COMMENT_LIMIT = 2000;
const WIP_MAX = 9;

const string = (extra = {}) => ({ bsonType: "string", ...extra });
const int = (minimum = 0) => ({ bsonType: "int", minimum });
const key = string({ pattern: "^[a-z]+$" });
const object = (properties, required = Object.keys(properties)) =>
  ({ bsonType: "object", required, additionalProperties: false, properties });
const arrayOf = (items, extra = {}) => ({ bsonType: "array", items, ...extra });
const column = { enum: COLUMNS };

/** One entry of a card's history; `detail` says what happened, by kind. */
function event(kind, detail) {
  return object({ kind: { enum: [kind] }, n: int(1), at: string({ pattern: ISO_MINUTE }), by: key,
    detail: object(detail) });
}

const ACTIVITY = {
  oneOf: [
    event("created", { column }),
    event("moved", { from: column, to: column, via: { enum: ["move", "pull-in"] } }),
    event("shipped", { from: { enum: ["doing", "review"] } }),
    event("parked", { from: { enum: ["doing", "review"] } }),
    event("checked", { item: int(1), done: { bsonType: "bool" } }),
    event("comment", { comment: int(1) }),
  ],
};

const COLLECTIONS = [
  {
    name: "users",
    schema: object({
      _id: key,
      username: string({ pattern: "^[a-z]+\\.[a-z]+$" }),
      name: string({ minLength: 1, maxLength: 80 }),
      pw: object({ salt: string({ pattern: "^[0-9a-f]{32}$" }), hash: string({ pattern: "^[0-9a-f]{64}$" }),
        iter: int(1000) }),
    }),
    indexes: [{ key: { username: 1 }, name: "username_unique", unique: true }],
  },
  {
    name: "workspaces",
    schema: object({
      _id: key,
      name: string({ minLength: 1 }),
      member_ids: arrayOf(key, { minItems: 1, uniqueItems: true }),
    }),
    indexes: [{ key: { member_ids: 1 }, name: "member_ids" }],
  },
  {
    name: "projects",
    schema: object({
      _id: key,
      workspace_id: key,
      name: string({ minLength: 1, maxLength: 80 }),
      summary: string(),
      position: int(1),
      columns: arrayOf(object({
        key: column, name: string({ minLength: 1 }),
        wip: { bsonType: ["int", "null"], minimum: 1, maximum: WIP_MAX }, position: int(1),
      }), { minItems: COLUMNS.length, maxItems: COLUMNS.length }),
      members: arrayOf(object({ user_id: key, role: { enum: ROLES } }), { minItems: 1 }),
      labels: arrayOf(object({ key, name: string({ minLength: 1 }) })),
    }),
    indexes: [
      { key: { workspace_id: 1, name: 1 }, name: "workspace_name_unique", unique: true },
      { key: { "members.user_id": 1 }, name: "member_user" },
    ],
  },
  {
    name: "cards",
    schema: object({
      _id: int(1),
      project_id: key,
      column,
      position: int(1),
      title: string({ minLength: 1, maxLength: TITLE_LIMIT }),
      description: string({ maxLength: COMMENT_LIMIT }),
      checklist: arrayOf(object({ n: int(1), text: string({ minLength: 1 }), done: { bsonType: "bool" } })),
      blocked_by: arrayOf(int(1), { uniqueItems: true }),
      labels: arrayOf(key, { uniqueItems: true }),
      assignee_ids: arrayOf(key, { uniqueItems: true }),
      comments: arrayOf(object({ n: int(1), author_id: key,
        body: string({ minLength: 1, maxLength: COMMENT_LIMIT }), at: string({ pattern: ISO_MINUTE }) })),
      activity: arrayOf(ACTIVITY),
    }),
    indexes: [
      { key: { project_id: 1, column: 1, position: 1 }, name: "board_order" },
      { key: { blocked_by: 1 }, name: "blocked_by" },
      { key: { assignee_ids: 1 }, name: "assignees" },
    ],
  },
  {
    name: "counters",
    schema: object({ _id: { enum: ["seq"] }, card: int(0), position: int(0), event: int(0) }),
    indexes: [],
  },
];

module.exports = { COLLECTIONS, COLUMNS, ROLES, TITLE_LIMIT, COMMENT_LIMIT, WIP_MAX };
