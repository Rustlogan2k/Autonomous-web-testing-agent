"use strict";
/*
 * Signing in: password checks, the `next` rule, and the two pages anyone may see (sign in,
 * forgotten password). Both pages are plain HTML forms, so they work before any script runs.
 *
 * Passwords are stored as PBKDF2-HMAC-SHA256 hashes with a per-user salt (seed/users.json).
 * 20 000 iterations is a documented fixture choice (suite plan, D10): the real mechanism, at
 * a cost that suits the hundreds of sign-ins a test run makes. Checking an unknown username
 * costs the same as checking a known one, so the answer time does not tell them apart.
 */

const crypto = require("node:crypto");
const { promisify } = require("node:util");

const pbkdf2 = promisify(crypto.pbkdf2);
const KEY_BYTES = 32;
const STAND_IN = { salt: "00".repeat(16), hash: "00".repeat(KEY_BYTES), iter: 20000 };

const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/** Whether `password` matches the stored credential `pw` ({salt, hash, iter}); false if none. */
async function verify(pw, password) {
  const record = pw || STAND_IN;
  const derived = await pbkdf2(String(password), Buffer.from(record.salt, "hex"), record.iter,
    KEY_BYTES, "sha256");
  return Boolean(pw) && crypto.timingSafeEqual(derived, Buffer.from(record.hash, "hex"));
}

/** Where to go after signing in: a path on this site (never another origin), else "/". */
function safeNext(value) {
  const next = String(value || "");
  return /^\/(?![/\\])[^\s\\]*$/.test(next) && !next.startsWith("/login") ? next : "/";
}

function page(t, title, body) {
  return `<!doctype html>
<html lang="en-GB">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${esc(title)}</title>
  <link rel="icon" href="data:,">
  <link rel="stylesheet" href="/styles.css">
</head>
<body class="auth">
  <header class="topbar"><span class="brand">${esc(t("app.title"))}</span></header>
  <main class="auth-card">${body}
  </main>
</body>
</html>`;
}

/** The sign-in form; `error` is the generic refusal after a failed attempt. */
function loginPage(t, { error = "", next = "/" } = {}) {
  return page(t, t("login.title"), `
    <h1>${esc(t("login.title"))}</h1>
    <form id="login-form" method="post" action="/login">
      <input type="hidden" name="next" value="${esc(next)}">
      <label for="login-username">${esc(t("field.username"))}</label>
      <input id="login-username" name="username" type="text" autocomplete="username" spellcheck="false">
      <label for="login-password">${esc(t("field.password"))}</label>
      <input id="login-password" name="password" type="password" autocomplete="current-password">
      <p id="login-error" class="message error" role="alert">${esc(error)}</p>
      <button id="login-submit" class="primary" type="submit">${esc(t("act.sign_in"))}</button>
    </form>
    <p class="aside"><a id="forgot-password" href="/login/forgot">${esc(t("act.forgot_password"))}</a></p>`);
}

function forgotPage(t) {
  return page(t, t("forgot.title"), `
    <h1>${esc(t("forgot.title"))}</h1>
    <p id="forgot-help">${esc(t("forgot.body"))}</p>
    <p><a id="back-to-sign-in" class="button" href="/login">${esc(t("act.back_to_sign_in"))}</a></p>`);
}

module.exports = { verify, safeNext, loginPage, forgotPage, esc };
