/*
 * Signing in to Wayfarer: checking a password, where to go afterwards, and the two pages a
 * visitor without a session may see (the sign-in form and its help page). Both pages are
 * plain forms rendered on the server.
 *
 * Passwords are PBKDF2-HMAC-SHA256 hashes with per-user salts (seed/users.json), at the
 * suite's documented fixture cost of 20 000 iterations. An unknown username is checked
 * against a stand-in credential, so it costs as much as a real one.
 */
"use strict";

const crypto = require("node:crypto");

const DERIVED_BYTES = 32;
const STAND_IN = { salt: "0".repeat(32), hash: "0".repeat(64), iter: 20000 };

const ENTITIES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const h = (value) => String(value === undefined || value === null ? "" : value).replace(/[&<>"']/g, (ch) => ENTITIES[ch]);

function derive(password, credential) {
  return new Promise((resolve, reject) => {
    crypto.pbkdf2(password, Buffer.from(credential.salt, "hex"), credential.iter, DERIVED_BYTES, "sha256",
      (error, key) => (error ? reject(error) : resolve(key)));
  });
}

/** Resolves to the account `password` opens: `account` itself, or null. */
async function check(account, password) {
  const credential = account ? account.pw : STAND_IN;
  const key = await derive(String(password || ""), credential);
  const matches = crypto.timingSafeEqual(key, Buffer.from(credential.hash, "hex"));
  return account && matches ? account : null;
}

/** A same-site path to continue to after signing in (never the sign-in page); else "/". */
function destination(next) {
  const target = String(next || "");
  const sameSite = target.startsWith("/") && !target.startsWith("//") && !/[\\\s]/.test(target);
  return sameSite && !target.startsWith("/login") ? target : "/";
}

function frame(t, title, inner) {
  return `<!doctype html>
<html lang="en-GB">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${h(title)} · ${h(t("app.title"))}</title>
  <link rel="icon" href="data:,">
  <link rel="stylesheet" href="/styles.css">
</head>
<body>
  <header class="masthead">
    <span class="wordmark">${h(t("app.title"))}</span>
    <span class="tagline">${h(t("app.tagline"))}</span>
  </header>
  <main class="gate">${inner}
  </main>
</body>
</html>`;
}

function signInPage(t, { next = "/", refusal = "" } = {}) {
  return frame(t, t("title.sign_in"), `
    <h1>${h(t("title.sign_in"))}</h1>
    <form method="post" action="/login" class="planner-form">
      <input type="hidden" name="next" value="${h(next)}">
      <label for="login-username">${h(t("field.username"))}</label>
      <input type="text" id="login-username" name="username" autocomplete="username" autocapitalize="off">
      <label for="login-password">${h(t("field.password"))}</label>
      <input type="password" id="login-password" name="password" autocomplete="current-password">
      <p class="problem" id="login-error" role="alert">${h(refusal)}</p>
      <button type="submit" id="login-submit" class="lead">${h(t("act.sign_in"))}</button>
    </form>
    <p><a id="login-help" href="/login/help">${h(t("act.login_help"))}</a></p>`);
}

function helpPage(t) {
  return frame(t, t("title.login_help"), `
    <h1>${h(t("title.login_help"))}</h1>
    <p id="login-help-text">${h(t("msg.login_help"))}</p>
    <p><a id="back-to-sign-in" href="/login">${h(t("act.back_to_sign_in"))}</a></p>`);
}

module.exports = { check, destination, signInPage, helpPage, h };
