// The way into Ledgerly: password checks, the address to continue to, and the two pages a
// visitor who is not signed in may see. Credentials are PBKDF2-HMAC-SHA256 hashes with a
// salt per person (seed/users.json), 20 000 iterations: the suite's documented fixture cost.
// An unknown username is checked against a stand-in record so it takes as long as a real one.

import crypto from "node:crypto";
import { promisify } from "node:util";

import { e, t } from "./views.js";

const derive = promisify(crypto.pbkdf2);
const STAND_IN = { pw_salt: "f".repeat(32), pw_hash: "f".repeat(64), pw_iterations: 20000 };

/** The account behind `username` if `password` is right for it, else null. */
export async function authenticate(accounts, username, password) {
  const account = accounts.find((row) => row.username === String(username ?? "").trim()) ?? null;
  const record = account ?? STAND_IN;
  const key = await derive(String(password ?? ""), Buffer.from(record.pw_salt, "hex"), record.pw_iterations, 32, "sha256");
  const right = crypto.timingSafeEqual(key, Buffer.from(record.pw_hash, "hex"));
  return right && account ? account : null;
}

/** Continue to `next` only when it is a path on this site (and not the sign-in page). */
export function continueTo(next) {
  const text = String(next ?? "");
  if (!text.startsWith("/") || text.startsWith("//") || text.includes("\\") || /\s/.test(text)) return "/";
  return text.startsWith("/login") ? "/" : text;
}

function shell(title, body) {
  return `<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${e(title)} · ${e(t("app.name"))}</title>
<link rel="icon" href="data:,">
<link rel="stylesheet" href="/static/ledgerly.css">
</head>
<body>
<header class="bar"><span class="brand">${e(t("app.name"))}</span></header>
<main class="narrow">
${body}
</main>
</body>
</html>`;
}

export function signInPage({ next = "/", refused = false } = {}) {
  return shell(t("signin.title"), `<h1>${e(t("signin.title"))}</h1>
<form class="stack" method="post" action="/login">
  <input type="hidden" name="next" value="${e(next)}">
  <label for="login-username">${e(t("field.username"))}</label>
  <input id="login-username" name="username" type="text" autocomplete="username">
  <label for="login-password">${e(t("field.password"))}</label>
  <input id="login-password" name="password" type="password" autocomplete="current-password">
  <p id="login-error" class="${refused ? "error" : "quiet-line"}" role="alert">${refused ? e(t("signin.refused")) : ""}</p>
  <p class="actions"><button id="login-submit" class="primary" type="submit">${e(t("act.sign_in"))}</button></p>
</form>
<p><a id="forgot-password" href="/login/forgot">${e(t("act.forgot_password"))}</a></p>`);
}

export function forgotPage() {
  return shell(t("forgot.title"), `<h1>${e(t("forgot.title"))}</h1>
<p id="forgot-text">${e(t("forgot.body"))}</p>
<p><a id="back-to-sign-in" href="/login">${e(t("act.back_to_sign_in"))}</a></p>`);
}
