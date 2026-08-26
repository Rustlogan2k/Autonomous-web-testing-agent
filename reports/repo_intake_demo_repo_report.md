# Bug report — http://127.0.0.1:57346/

Generated 2026-08-26T07:55:08+00:00

**5 findings** — 1 high, 4 medium, 0 low.

5 were found by deterministic triggers and would have been caught without a language model. 0 required semantic judgment.

> Severity is a CVSS-*inspired* 0-10 ordering, not a CVSS score. Judge severities are the model's own 0-1 rating rescaled; deterministic ones are fixed per trigger.

---

### 1. Page itself failed to load

**Severity** 7.0/10 (high) · **Type** `document_http_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:57346/pricing.html

**Steps to reproduce**

1. Click Pricing

### 2. Link or submit did not navigate

**Severity** 6.0/10 (medium) · **Type** `broken_navigation` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:57346/pricing.html

**Steps to reproduce**

1. Click Pricing

**What happened** — http://127.0.0.1:57346/pricing.html

**Evidence**

```
http://127.0.0.1:57346/pricing.html
```

### 3. Unexpected HTTP error response

**Severity** 5.0/10 (medium) · **Type** `http_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:57346/pricing.html

**Steps to reproduce**

1. Click Pricing

**What happened** — 404 http://127.0.0.1:57346/pricing.html

**Evidence**

```
404 http://127.0.0.1:57346/pricing.html
```

### 4. JavaScript error logged to the console

**Severity** 4.0/10 (medium) · **Type** `console_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:57346/catalog.html
**Occurrences** 2

**Steps to reproduce**

1. Click Export catalog

**What happened** — Cannot read properties of undefined (reading 'run')

**Evidence**

```
Cannot read properties of undefined (reading 'run')
```

### 5. JavaScript error logged to the console

**Severity** 4.0/10 (medium) · **Type** `console_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:57346/pricing.html

**Steps to reproduce**

1. Click Pricing

**What happened** — Failed to load resource: the server responded with a status of 404 (Not Found)

**Evidence**

```
Failed to load resource: the server responded with a status of 404 (Not Found)
```

---

## Run

```json
{
  "repository": "C:\\Users\\reach\\OneDrive\\Code files\\SEM7\\RL web tester\\tests\\fixtures\\demo_repo",
  "build_definition": "C:\\Users\\reach\\OneDrive\\Code files\\SEM7\\RL web tester\\tests\\fixtures\\demo_repo\\Dockerfile",
  "build_network": "none",
  "image": "wta-target-11e5a410f50a:latest",
  "episodes": 2,
  "steps_per_episode": 25,
  "seed": 0,
  "judge": "stub",
  "wall_clock_s": 21.5,
  "scope_note": "Trusted/controlled repository input, resource-limited execution. NOT a security boundary against a determined attacker: Dockerfile build commands execute before any docker run restriction applies."
}
```
