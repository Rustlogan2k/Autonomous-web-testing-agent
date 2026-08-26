# Bug report — http://127.0.0.1:59978/

Generated 2026-08-26T07:58:41+00:00

**14 findings** — 8 high, 6 medium, 0 low.

5 were found by deterministic triggers and would have been caught without a language model. 9 required semantic judgment.

> Severity is a CVSS-*inspired* 0-10 ordering, not a CVSS score. Judge severities are the model's own 0-1 rating rescaled; deterministic ones are fixed per trigger.

---

### 1. The link to Pricing resulted in a 404 error and navigation to a non-existent page.

**Severity** 10.0/10 (high) · **Type** `broken_navigation` · **Found by** LLM judge, 95% confident, citation verified against the window

**Steps to reproduce**

1. Rapidly click Export catalog
2. REFRESH
3. BROWSER_FORWARD
4. BROWSER_BACK
5. BROWSER_BACK
6. Click Pricing

**What should happen** — Clicking 'Pricing' should navigate to /pricing.html without errors.

**What happened** — Clicking 'Pricing' led to a 404 error, navigating to an undefined URL.

**Why that is wrong** — The link to Pricing resulted in a 404 error and navigation to a non-existent page.

**Evidence**

```
url        : / -> /pricing.html
  title      : 'Widget Co — Home' -> '404 Not Found'
  page text  : 404 Not Found | 404 Not Found | The requested URL was not found
  console    : Failed to load resource: the server responded with a status of 404 (Not Found)
  http errors: 404 /pricing.html
```

### 2. A network request or page re-render is expected when typing, but none occurred.

**Severity** 8.0/10 (high) · **Type** `dead_control` · **Found by** LLM judge, 95% confident, citation verified against the window

**Steps to reproduce**

1. Click Contact
2. Type 'Test User Input' into message
3. TYPE on name
4. TYPE on name
5. Type 'a' into message

**What should happen** — Typing should update the field value.

**What happened** — Typing a single character into the 'message' field caused no change.

**Why that is wrong** — A network request or page re-render is expected when typing, but none occurred.

**Evidence**

```
effect     : NO OBSERVABLE CHANGE — the page is byte-identical after this action
```

### 3. A control that promises to export something did nothing and caused a console error.

**Severity** 8.0/10 (high) · **Type** `dead_control` · **Found by** LLM judge, 95% confident, citation verified against the window

**Steps to reproduce**

1. Scroll down
2. Click Catalog
3. BROWSER_FORWARD
4. Click Export catalog

**What should happen** — 'Export catalog' should trigger an export process or request.

**What happened** — Clicking 'Export catalog' produced no change, no navigation, and a console error.

**Why that is wrong** — A control that promises to export something did nothing and caused a console error.

**Evidence**

```
console    : Cannot read properties of undefined (reading 'run')
```

### 4. Repeated rapid clicks resulted in multiple uncaught console errors without any observable effect on the page.

**Severity** 8.0/10 (high) · **Type** `dead_control` · **Found by** LLM judge, 95% confident, citation verified against the window

**Steps to reproduce**

1. Click Catalog
2. BROWSER_FORWARD
3. Click Export catalog
4. BROWSER_BACK
5. BROWSER_FORWARD
6. Rapidly click Export catalog

**What should happen** — Clicking a control that promises to export something should either work or provide feedback, even if it fails.

**What happened** — Rapid clicking on 'Export catalog' produced multiple console errors but no change in the UI.

**Why that is wrong** — Repeated rapid clicks resulted in multiple uncaught console errors without any observable effect on the page.

**Evidence**

```
console    : Cannot read properties of undefined (reading 'run') | Cannot read properties of undefined (reading 'run') | Cannot read properties of undefined (reading 'run') | Cannot read properties of undefined (reading 'run') | Cannot read properties of undefined (reading 'run')
```

### 5. The form fields (message and name) were cleared instead of being initialized with existing values.

**Severity** 7.0/10 (high) · **Type** `ui_regression` · **Found by** LLM judge, 90% confident, citation verified against the window
**Occurrences** 2

**Steps to reproduce**

1. Click Contact

**What should happen** — Navigating to a contact page should preserve or initialize form fields appropriately.

**What happened** — Clicking 'Contact' navigated to /contact.html, updated the title, and cleared form fields.

**Why that is wrong** — The form fields (message and name) were cleared instead of being initialized with existing values.

**Evidence**

```
form state : message: absent -> empty; name: absent -> empty
```

### 6. The page did not change as expected, suggesting a navigation issue.

**Severity** 7.0/10 (high) · **Type** `ui_regression` · **Found by** LLM judge, 90% confident, citation verified against the window
**Occurrences** 2

**Steps to reproduce**

1. Scroll down
2. Click Catalog
3. BROWSER_FORWARD
4. Click Export catalog
5. BROWSER_BACK

**What should happen** — Navigating back should restore the previous state of the page.

**What happened** — BROWSING BACK from /catalog.html to / refreshed the title and page text.

**Why that is wrong** — The page did not change as expected, suggesting a navigation issue.

**Evidence**

```
url        : /catalog.html -> /
```

### 7. The navigation back to the home page did not update the content, showing a stale version.

**Severity** 7.0/10 (high) · **Type** `state_persistence` · **Found by** LLM judge, 90% confident, citation verified against the window

**Steps to reproduce**

1. BROWSER_BACK
2. BROWSER_BACK
3. Click Pricing
4. Scroll down
5. BROWSER_FORWARD
6. BROWSER_BACK

**What should happen** — Navigating back should restore the previous state without re-fetching.

**What happened** — Browser back from 404 page navigated to home but did not re-fetch the content.

**Why that is wrong** — The navigation back to the home page did not update the content, showing a stale version.

**Evidence**

```
url        : /pricing.html -> /
  title      : '404 Not Found' -> 'Widget Co — Home'
  page text  : Widget Co — Home | Widget Co | Catalog | Contact | Pricing | Everything you need, in one place.
```

### 8. Page itself failed to load

**Severity** 7.0/10 (high) · **Type** `document_http_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:59978/pricing.html

**Steps to reproduce**

1. Click Pricing

### 9. The back action caused a full navigation, which is unexpected for such a simple operation.

**Severity** 6.0/10 (medium) · **Type** `ui_regression` · **Found by** LLM judge, 85% confident, citation verified against the window

**Steps to reproduce**

1. Scroll up
2. Type 'a' into message
3. Type 'Test User Input' into name
4. Scroll up
5. BROWSER_BACK

**What should happen** — Back should revert to the previous state without requiring a scroll.

**What happened** — Scrolling and back navigation changed the URL to / and updated the title and page text.

**Why that is wrong** — The back action caused a full navigation, which is unexpected for such a simple operation.

**Evidence**

```
url        : /contact.html -> /
```

### 10. Link or submit did not navigate

**Severity** 6.0/10 (medium) · **Type** `broken_navigation` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:59978/pricing.html

**Steps to reproduce**

1. Click Pricing

**What happened** — http://127.0.0.1:59978/pricing.html

**Evidence**

```
http://127.0.0.1:59978/pricing.html
```

### 11. The input was processed correctly (as evidenced by the form state change), but there was no observable change on the page, which is unexpected for a user experi

**Severity** 5.0/10 (medium) · **Type** `ui_regression` · **Found by** LLM judge, 80% confident, citation verified against the window

**Steps to reproduce**

1. Type 'a' into message
2. Resize the viewport to mobile
3. Scroll up
4. Type 'a' into message
5. Type 'Test User Input' into name

**What should happen** — Typing into a required field should update the form state and potentially trigger some visual feedback or validation message.

**What happened** — Typing into 'name' resulted in a change to the form state but no observable visual change on the page.

**Why that is wrong** — The input was processed correctly (as evidenced by the form state change), but there was no observable change on the page, which is unexpected for a user experience perspective.

**Evidence**

```
effect     : NO OBSERVABLE CHANGE — the page is byte-identical after this action
```

### 12. Unexpected HTTP error response

**Severity** 5.0/10 (medium) · **Type** `http_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:59978/pricing.html

**Steps to reproduce**

1. Click Pricing

**What happened** — 404 http://127.0.0.1:59978/pricing.html

**Evidence**

```
404 http://127.0.0.1:59978/pricing.html
```

### 13. JavaScript error logged to the console

**Severity** 4.0/10 (medium) · **Type** `console_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:59978/catalog.html
**Occurrences** 2

**Steps to reproduce**

1. Click Export catalog

**What happened** — Cannot read properties of undefined (reading 'run')

**Evidence**

```
Cannot read properties of undefined (reading 'run')
```

### 14. JavaScript error logged to the console

**Severity** 4.0/10 (medium) · **Type** `console_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:59978/pricing.html

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
  "image": "wta-target-14e7fb3a37e5:latest",
  "episodes": 2,
  "steps_per_episode": 25,
  "seed": 0,
  "judge": "ollama/qwen2.5:7b-instruct",
  "wall_clock_s": 200.0,
  "scope_note": "Trusted/controlled repository input, resource-limited execution. NOT a security boundary against a determined attacker: Dockerfile build commands execute before any docker run restriction applies."
}
```
