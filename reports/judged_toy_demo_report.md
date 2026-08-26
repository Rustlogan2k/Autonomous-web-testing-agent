# Bug report — http://127.0.0.1:62081/index.html

Generated 2026-08-26T07:25:43+00:00

**25 findings** — 7 high, 18 medium, 0 low.

11 were found by deterministic triggers and would have been caught without a language model. 14 required semantic judgment.

> Severity is a CVSS-*inspired* 0-10 ordering, not a CVSS score. Judge severities are the model's own 0-1 rating rescaled; deterministic ones are fixed per trigger.

---

### 1. A control that promises to generate a report did nothing and issued an uncaught console error.

**Severity** 8.0/10 (high) · **Type** `js_error` · **Found by** LLM judge, 95% confident, citation verified against the window

**Steps to reproduce**

1. Scroll down
2. Click Widgets
3. Click Generate report

**What should happen** — Clicking 'Generate report' should trigger a request to generate a report or show a report.

**What happened** — Clicking 'Generate report' produced no change, no navigation, and a console error.

**Why that is wrong** — A control that promises to generate a report did nothing and issued an uncaught console error.

**Evidence**

```
console    : Cannot read properties of undefined (reading 'build')
```

### 2. Repeated rapid clicks issued multiple requests but had no effect, indicating a race condition or unhandled error.

**Severity** 8.0/10 (high) · **Type** `race_condition` · **Found by** LLM judge, 95% confident, citation verified against the window

**Steps to reproduce**

1. Scroll down
2. Click Widgets
3. Click Generate report
4. Rapidly click Generate report

**What should happen** — Clicking 'Generate report' should trigger a request to generate a report.

**What happened** — Rapid clicking on 'Generate report' produced no change, no navigation, and multiple console errors.

**Why that is wrong** — Repeated rapid clicks issued multiple requests but had no effect, indicating a race condition or unhandled error.

**Evidence**

```
console    : Cannot read properties of undefined (reading 'build') | Cannot read properties of undefined (reading 'build') | Cannot read properties of undefined (reading 'build') | Cannot read properties of undefined (reading 'build') | Cannot read properties of undefined (reading 'build')
```

### 3. The application failed to load the requested resource, resulting in an error page.

**Severity** 8.0/10 (high) · **Type** `broken_navigation` · **Found by** LLM judge, 95% confident, citation verified against the window
**Occurrences** 3

**Steps to reproduce**

1. Click Widgets
2. Click Generate report
3. Rapidly click Generate report
4. Click ← Home
5. BROWSER_FORWARD
6. Click Pricing

**What should happen** — Navigating to '/pricing.html' should load the pricing page without errors.

**What happened** — Clicking 'Pricing' navigated to /pricing.html but resulted in a 404 error.

**Why that is wrong** — The application failed to load the requested resource, resulting in an error page.

**Evidence**

```
http errors: 404 /pricing.html
```

### 4. The application failed to load the expected page and showed an error instead.

**Severity** 8.0/10 (high) · **Type** `broken_navigation` · **Found by** LLM judge, 95% confident, citation verified against the window

**Steps to reproduce**

1. Rapidly click Generate report
2. Click ← Home
3. BROWSER_FORWARD
4. Click Pricing
5. BROWSER_BACK
6. BROWSER_FORWARD

**What should happen** — Forward navigation should return to the previously visited page without errors.

**What happened** — Navigating forward to /pricing.html resulted in an error page.

**Why that is wrong** — The application failed to load the expected page and showed an error instead.

**Evidence**

```
console    : Failed to load resource: the server responded with a status of 404 (File not found)
```

### 5. Page itself failed to load

**Severity** 7.0/10 (high) · **Type** `document_http_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:62081/pricing.html
**Occurrences** 2

**Steps to reproduce**

1. Click Pricing

### 6. Page itself failed to load

**Severity** 7.0/10 (high) · **Type** `document_http_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:62081/pricing.html

**Steps to reproduce**

1. BROWSER_FORWARD on BROWSER_FORWARD

### 7. Page itself failed to load

**Severity** 7.0/10 (high) · **Type** `document_http_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:62081/pricing.html

**Steps to reproduce**

1. REFRESH on REFRESH

### 8. A control that promises to export data did nothing, so it is not wired up.

**Severity** 6.0/10 (medium) · **Type** `dead_control` · **Found by** LLM judge, 90% confident, citation verified against the window

**Steps to reproduce**

1. Click Pricing
2. Resize the viewport to tablet
3. BROWSER_BACK
4. Click Widgets
5. Rapidly click Export data

**What should happen** — Clicking 'Export data' should trigger a download or a modal for export options.

**What happened** — Rapidly clicking 'Export data' produced no change, no navigation and no request.

**Why that is wrong** — A control that promises to export data did nothing, so it is not wired up.

**Evidence**

```
url        : /widgets.html (unchanged)
```

### 9. A control that promises to export data did nothing upon rapid clicking.

**Severity** 6.0/10 (medium) · **Type** `dead_control` · **Found by** LLM judge, 90% confident, citation verified against the window

**Steps to reproduce**

1. BROWSER_BACK
2. Click Widgets
3. Rapidly click Export data
4. Resize the viewport to mobile
5. Scroll up
6. Rapidly click Export data

**What should happen** — Clicking 'Export data' should trigger an export action, such as a download or a modal dialog.

**What happened** — Rapid clicking on 'Export data' produced no change.

**Why that is wrong** — A control that promises to export data did nothing upon rapid clicking.

**Evidence**

```
effect     : NO OBSERVABLE CHANGE — the page is byte-identical after this action
```

### 10. The navigation did not occur as expected, instead the page was reloaded.

**Severity** 6.0/10 (medium) · **Type** `broken_navigation` · **Found by** LLM judge, 90% confident, citation verified against the window
**Occurrences** 2

**Steps to reproduce**

1. Rapidly click Export data
2. Click Increment counter
3. Scroll up
4. Resize the viewport to desktop
5. Click ← Home

**What should happen** — Clicking '← Home' should navigate to index.html without reloading the page.

**What happened** — Clicking '← Home' changed the URL to /index.html and updated the title and page text.

**Why that is wrong** — The navigation did not occur as expected, instead the page was reloaded.

**Evidence**

```
url        : /widgets.html -> /index.html
```

### 11. The application did not follow the BROWSER_BACK action, instead showing the starting page again.

**Severity** 6.0/10 (medium) · **Type** `broken_navigation` · **Found by** LLM judge, 90% confident, citation verified against the window

**Steps to reproduce**

1. Resize the viewport to tablet
2. Resize the viewport to desktop
3. REFRESH
4. Scroll down
5. BROWSER_FORWARD
6. BROWSER_BACK

**What should happen** — Going back should return to the previous page in the browser history.

**What happened** — Navigating back from /pricing.html to /index.html.

**Why that is wrong** — The application did not follow the BROWSER_BACK action, instead showing the starting page again.

**Evidence**

```
url        : /pricing.html -> /index.html
```

### 12. A required field accepted an invalid value without any observable change, which is unexpected behavior.

**Severity** 6.0/10 (medium) · **Type** `validation_bypass` · **Found by** LLM judge, 90% confident, citation verified against the window

**Steps to reproduce**

1. Click Sign up
2. Select 'us' in country
3. TYPE on username
4. BROWSER_FORWARD
5. Type 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' into username

**What should happen** — The application should validate the input and either update the form or show validation errors.

**What happened** — Typing a long username caused no change to the page.

**Why that is wrong** — A required field accepted an invalid value without any observable change, which is unexpected behavior.

**Evidence**

```
constraints: username declares [type=text required]
```

### 13. A control that promises to save and redirect did not change the URL, suggesting it failed to execute properly.

**Severity** 6.0/10 (medium) · **Type** `dead_control` · **Found by** LLM judge, 85% confident, citation verified against the window

**Steps to reproduce**

1. Click Settings
2. Click Save settings

**What should happen** — Clicking 'Save settings' should save the form data and navigate to a success or confirmation page.

**What happened** — Clicking 'Save settings' produced a confirmation message but did not navigate to another page.

**Why that is wrong** — A control that promises to save and redirect did not change the URL, suggesting it failed to execute properly.

**Evidence**

```
url        : /settings.html (unchanged)
```

### 14. The sign-up options became unreachable after the resize, which is unexpected behavior.

**Severity** 6.0/10 (medium) · **Type** `ui_regression` · **Found by** LLM judge, 85% confident, citation verified against the window

**Steps to reproduce**

1. Click Settings
2. Click Save settings
3. Click ← Home
4. Resize the viewport to mobile

**What should happen** — Mobile resizing should not make sign-up options unreachable, as they were still reachable before the resize.

**What happened** — Resizing to mobile hid a#nav-signup and a#cta-signup; the only links left are Widgets -> widgets.html; Settings -> settings.html; Pricing -> pricing.html.

**Why that is wrong** — The sign-up options became unreachable after the resize, which is unexpected behavior.

**Evidence**

```
NO LONGER reachable: Sign up -> signup.html; Create account -> signup.html
```

### 15. The back navigation did not update the page content, leaving it in an error state.

**Severity** 6.0/10 (medium) · **Type** `ui_regression` · **Found by** LLM judge, 85% confident, citation verified against the window

**Steps to reproduce**

1. Click Pricing
2. Resize the viewport to tablet
3. BROWSER_BACK

**What should happen** — Going back should restore the previous state of the application, including its content and title.

**What happened** — Navigating back from /pricing.html to /index.html changed the URL but not the page content.

**Why that is wrong** — The back navigation did not update the page content, leaving it in an error state.

**Evidence**

```
url        : /pricing.html -> /index.html
```

### 16. Link or submit did not navigate

**Severity** 6.0/10 (medium) · **Type** `broken_navigation` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:62081/pricing.html
**Occurrences** 2

**Steps to reproduce**

1. Click Pricing

**What happened** — http://127.0.0.1:62081/pricing.html

**Evidence**

```
http://127.0.0.1:62081/pricing.html
```

### 17. Unexpected HTTP error response

**Severity** 5.0/10 (medium) · **Type** `http_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:62081/pricing.html
**Occurrences** 2

**Steps to reproduce**

1. Click Pricing

**What happened** — 404 http://127.0.0.1:62081/pricing.html

**Evidence**

```
404 http://127.0.0.1:62081/pricing.html
```

### 18. Unexpected HTTP error response

**Severity** 5.0/10 (medium) · **Type** `http_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:62081/pricing.html

**Steps to reproduce**

1. BROWSER_FORWARD on BROWSER_FORWARD

**What happened** — 404 http://127.0.0.1:62081/pricing.html

**Evidence**

```
404 http://127.0.0.1:62081/pricing.html
```

### 19. Unexpected HTTP error response

**Severity** 5.0/10 (medium) · **Type** `http_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:62081/pricing.html

**Steps to reproduce**

1. REFRESH on REFRESH

**What happened** — 404 http://127.0.0.1:62081/pricing.html

**Evidence**

```
404 http://127.0.0.1:62081/pricing.html
```

### 20. The darkmode, display_name, and newsletter fields are missing their expected states (off, empty, off).

**Severity** 4.0/10 (medium) · **Type** `state_persistence` · **Found by** LLM judge, 90% confident, citation verified against the window
**Occurrences** 2

**Steps to reproduce**

1. Click Settings

**What should happen** — Navigating to /settings.html should update the page title and initialize the form fields correctly.

**What happened** — Clicking 'Settings' navigated to /settings.html with a new title and form state.

**Why that is wrong** — The darkmode, display_name, and newsletter fields are missing their expected states (off, empty, off).

**Evidence**

```
form state : darkmode: absent -> off; display_name: absent -> empty; newsletter: absent -> off
```

### 21. The selection changed the form state but not the URL, which is unexpected behavior.

**Severity** 4.0/10 (medium) · **Type** `ui_regression` · **Found by** LLM judge, 85% confident, citation verified against the window

**Steps to reproduce**

1. Scroll up
2. Click Sign up
3. Select 'us' in country

**What should happen** — Selecting a country should update the selected value in the form.

**What happened** — Selecting 'us' for 'country' did not change the URL or form state.

**Why that is wrong** — The selection changed the form state but not the URL, which is unexpected behavior.

**Evidence**

```
url        : /signup.html (unchanged)
```

### 22. JavaScript error logged to the console

**Severity** 4.0/10 (medium) · **Type** `console_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:62081/widgets.html
**Occurrences** 2

**Steps to reproduce**

1. Click Generate report

**What happened** — Cannot read properties of undefined (reading 'build')

**Evidence**

```
Cannot read properties of undefined (reading 'build')
```

### 23. JavaScript error logged to the console

**Severity** 4.0/10 (medium) · **Type** `console_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:62081/pricing.html
**Occurrences** 2

**Steps to reproduce**

1. Click Pricing

**What happened** — Failed to load resource: the server responded with a status of 404 (File not found)

**Evidence**

```
Failed to load resource: the server responded with a status of 404 (File not found)
```

### 24. JavaScript error logged to the console

**Severity** 4.0/10 (medium) · **Type** `console_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:62081/pricing.html

**Steps to reproduce**

1. BROWSER_FORWARD on BROWSER_FORWARD

**What happened** — Failed to load resource: the server responded with a status of 404 (File not found)

**Evidence**

```
Failed to load resource: the server responded with a status of 404 (File not found)
```

### 25. JavaScript error logged to the console

**Severity** 4.0/10 (medium) · **Type** `console_error` · **Found by** deterministic trigger (no language model involved)
**URL** http://127.0.0.1:62081/pricing.html

**Steps to reproduce**

1. REFRESH on REFRESH

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
  "wall_clock_s": 215.2,
  "judge": "ollama/qwen2.5:7b-instruct",
  "judge_calls": 22
}
```
