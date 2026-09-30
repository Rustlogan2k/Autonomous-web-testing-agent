# Bug report — http://127.0.0.1:8001/index.html

Generated 2026-09-17T13:46:46+00:00

**5 findings** — 1 high, 4 medium, 0 low.

5 were found by deterministic triggers and would have been caught without a language model. 0 required semantic judgment.

> Severity is a CVSS-*inspired* 0-10 ordering, not a CVSS score. Judge severities are the model's own 0-1 rating rescaled; deterministic ones are fixed per trigger.

---

### 1. Page itself failed to load

**Severity** 7.0/10 (high) · **Type** `document_http_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:8001/pricing.html

**Steps to reproduce**

1. Click Pricing

### 2. Link or submit did not navigate

**Severity** 6.0/10 (medium) · **Type** `broken_navigation` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:8001/pricing.html

**Steps to reproduce**

1. Click Pricing

**What happened** — http://127.0.0.1:8001/pricing.html

**Evidence**

```
http://127.0.0.1:8001/pricing.html
```

### 3. Unexpected HTTP error response

**Severity** 5.0/10 (medium) · **Type** `http_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:8001/pricing.html

**Steps to reproduce**

1. Click Pricing

**What happened** — 404 http://127.0.0.1:8001/pricing.html

**Evidence**

```
404 http://127.0.0.1:8001/pricing.html
```

### 4. JavaScript error logged to the console

**Severity** 4.0/10 (medium) · **Type** `console_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:8001/catalog.html
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
**URL** http://127.0.0.1:8001/pricing.html

**Steps to reproduce**

1. Click Pricing

**What happened** — Failed to load resource: the server responded with a status of 404 (File not found)

**Evidence**

```
Failed to load resource: the server responded with a status of 404 (File not found)
```

---

## Run

```json
{
  "episodes": 2,
  "steps_per_episode": 25,
  "seed": 0,
  "wall_clock_s": 20.2,
  "judge": "stub",
  "judge_calls": 17
}
```
