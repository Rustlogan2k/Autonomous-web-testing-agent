# Bug report — http://127.0.0.1:8002/index.html

Generated 2026-09-18T16:06:56+00:00

**9 findings** — 2 high, 7 medium, 0 low.

9 were found by deterministic triggers and would have been caught without a language model. 0 required semantic judgment.

> Severity is a CVSS-*inspired* 0-10 ordering, not a CVSS score. Judge severities are the model's own 0-1 rating rescaled; deterministic ones are fixed per trigger.

---

### 1. Page itself failed to load

**Severity** 7.0/10 (high) · **Type** `document_http_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:8002/dashboard.html

**Steps to reproduce**

1. Click Back to dashboard

### 2. Page itself failed to load

**Severity** 7.0/10 (high) · **Type** `document_http_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:8002/dashboard.html

**Steps to reproduce**

1. REFRESH on REFRESH

### 3. Link or submit did not navigate

**Severity** 6.0/10 (medium) · **Type** `broken_navigation` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:8002/dashboard.html

**Steps to reproduce**

1. Click Back to dashboard

**What happened** — http://127.0.0.1:8002/dashboard.html

**Evidence**

```
http://127.0.0.1:8002/dashboard.html
```

### 4. Link or submit did not navigate

**Severity** 6.0/10 (medium) · **Type** `broken_navigation` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:8002/contact.html

**Steps to reproduce**

1. Click Send message

**What happened** — http://127.0.0.1:8002/contact.html

**Evidence**

```
http://127.0.0.1:8002/contact.html
```

### 5. Unexpected HTTP error response

**Severity** 5.0/10 (medium) · **Type** `http_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:8002/dashboard.html

**Steps to reproduce**

1. Click Back to dashboard

**What happened** — 404 http://127.0.0.1:8002/dashboard.html

**Evidence**

```
404 http://127.0.0.1:8002/dashboard.html
```

### 6. Unexpected HTTP error response

**Severity** 5.0/10 (medium) · **Type** `http_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:8002/dashboard.html

**Steps to reproduce**

1. REFRESH on REFRESH

**What happened** — 404 http://127.0.0.1:8002/dashboard.html

**Evidence**

```
404 http://127.0.0.1:8002/dashboard.html
```

### 7. JavaScript error logged to the console

**Severity** 4.0/10 (medium) · **Type** `console_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:8002/dashboard.html

**Steps to reproduce**

1. Click Back to dashboard

**What happened** — Failed to load resource: the server responded with a status of 404 (File not found)

**Evidence**

```
Failed to load resource: the server responded with a status of 404 (File not found)
```

### 8. JavaScript error logged to the console

**Severity** 4.0/10 (medium) · **Type** `console_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:8002/dashboard.html

**Steps to reproduce**

1. REFRESH on REFRESH

**What happened** — Failed to load resource: the server responded with a status of 404 (File not found)

**Evidence**

```
Failed to load resource: the server responded with a status of 404 (File not found)
```

### 9. JavaScript error logged to the console

**Severity** 4.0/10 (medium) · **Type** `console_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:8002/catalog.html

**Steps to reproduce**

1. Click Export catalog

**What happened** — Cannot read properties of undefined (reading 'download')

**Evidence**

```
Cannot read properties of undefined (reading 'download')
```

---

## Run

```json
{
  "episodes": 3,
  "steps_per_episode": 40,
  "seed": 0,
  "wall_clock_s": 44.0,
  "judge": "stub",
  "judge_calls": 71
}
```
