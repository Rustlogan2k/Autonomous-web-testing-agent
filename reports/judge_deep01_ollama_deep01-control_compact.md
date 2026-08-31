# Bug report — deep_flow_site::deep01-control

Generated 2026-08-30T09:42:12+00:00

**10 findings** — 4 high, 6 medium, 0 low.

0 were found by deterministic triggers and would have been caught without a language model. 10 required semantic judgment.

> Severity is a CVSS-*inspired* 0-10 ordering, not a CVSS score. Judge severities are the model's own 0-1 rating rescaled; deterministic ones are fixed per trigger.

---

### 1. The form state shows that the 'quantity' field is empty, which violates the [type=number min=1 max=100 step=1] constraint declared by the app. However, the navi

**Severity** 8.0/10 (high) · **Type** `validation_bypass` · **Found by** LLM judge, 95% confident, citation verified against the window
**URL** http://127.0.0.1:63597/order-2.html?product=folders-25
**Occurrences** 2

**What should happen** — Clicking 'Continue' should validate the required fields and proceed to the next step if valid.

**What happened** — Clicking 'Continue' on the 'folders-25' product navigated to /order-2.html?product=folders-25, but did not validate the quantity input.

**Why that is wrong** — The form state shows that the 'quantity' field is empty, which violates the [type=number min=1 max=100 step=1] constraint declared by the app. However, the navigation proceeded without validation.

**Evidence**

```
constraints: quantity declares [type=number min=1 max=100 step=1]
```

**Recorded evidence** (episode 1, step 3)

- page after: `pages/c6d5cc0caa00….html`
- page before: `pages/58596412aa6e….html`
- screenshot after: `screenshots/c5fa7263ddf6….png`
- screenshot before: `screenshots/19b3f3be34be….png`

### 2. The form state did not advance; the email field was still absent when it should have been present.

**Severity** 8.0/10 (high) · **Type** `ui_regression` · **Found by** LLM judge, 95% confident, citation verified against the window
**URL** http://127.0.0.1:63597/order-3.html?product=folders-25&quantity=42

**What should happen** — Continuing from step 2 should navigate to step 3 and populate the next required field (email).

**What happened** — Clicking 'Continue' on step 3 navigated to step 4 with an email field, as expected.

**Why that is wrong** — The form state did not advance; the email field was still absent when it should have been present.

**Evidence**

```
form state : email: absent -> empty
  constraints: email declares [type=email]
```

**Recorded evidence** (episode 1, step 5)

- page after: `pages/23a70cf7554b….html`
- page before: `pages/87c0ac2aad06….html`
- screenshot after: `screenshots/e50887ac7b2a….png`
- screenshot before: `screenshots/23674b71b4b0….png`

### 3. The email field was emptied when navigating back, which violates the expected behavior of preserving form state during navigation.

**Severity** 7.0/10 (high) · **Type** `state_persistence` · **Found by** LLM judge, 95% confident, citation verified against the window
**URL** http://127.0.0.1:63597/order-3.html

**What should happen** — Navigating back should preserve all form data unless explicitly cleared by the app.

**What happened** — Clicking 'Back' from step 4 to step 3 correctly navigated, but reset the email field.

**Why that is wrong** — The email field was emptied when navigating back, which violates the expected behavior of preserving form state during navigation.

**Evidence**

```
form state : email: absent -> empty
```

**Recorded evidence** (episode 1, step 8)

- page after: `pages/23a70cf7554b….html`
- page before: `pages/db82a35d8df4….html`
- screenshot after: `screenshots/e50887ac7b2a….png`
- screenshot before: `screenshots/7296624d3e4e….png`

### 4. The email field is still filled after cancelling the order.

**Severity** 7.0/10 (high) · **Type** `ui_regression` · **Found by** LLM judge, 90% confident, citation verified against the window
**URL** http://127.0.0.1:63597/index.html

**What should happen** — Cancelling an order should clear all form fields and navigate to a relevant page.

**What happened** — Clicking 'Cancel order' navigated to the home page, but left the email field filled.

**Why that is wrong** — The email field is still filled after cancelling the order.

**Evidence**

```
form state : email: absent -> empty
```

**Recorded evidence** (episode 1, step 9)

- page after: `pages/13ff207abecc….html`
- page before: `pages/23a70cf7554b….html`
- screenshot after: `screenshots/3ff237637e6f….png`
- screenshot before: `screenshots/e50887ac7b2a….png`

### 5. The click did not navigate to /order-4.html as expected, and the page content did not change.

**Severity** 6.0/10 (medium) · **Type** `dead_control` · **Found by** LLM judge, 90% confident, citation verified against the window
**URL** http://127.0.0.1:63597/order-4.html?product=folders-25&quantity=42&email=tester%40example.com

**What should happen** — Continuing to the next step should update the URL and display the review page.

**What happened** — Clicking 'Continue' on step 6 produced no change in URL or page content.

**Why that is wrong** — The click did not navigate to /order-4.html as expected, and the page content did not change.

**Evidence**

```
url        : /order-3.html?product=folders-25&quantity=42 -> /order-3.html?product=folders-25&quantity=42&email=tester%40example.com
```

**Recorded evidence** (episode 1, step 7)

- page after: `pages/db82a35d8df4….html`
- page before: `pages/d036a4ebec26….html`
- screenshot after: `screenshots/7296624d3e4e….png`
- screenshot before: `screenshots/5aa23f04d905….png`

### 6. The link did not cause any observable change, even though it was clicked multiple times.

**Severity** 6.0/10 (medium) · **Type** `dead_control` · **Found by** LLM judge, 90% confident, citation verified against the window
**URL** http://127.0.0.1:63597/catalog.html

**What should happen** — A click on a navigation link should navigate to the intended page.

**What happened** — Clicking on 'Catalog' did not change the URL or content of the page.

**Why that is wrong** — The link did not cause any observable change, even though it was clicked multiple times.

**Evidence**

```
url        : /catalog.html (unchanged)
```

**Recorded evidence** (episode 1, step 11)

- page after: `pages/106da00d6af7….html`
- page before: `pages/106da00d6af7….html`
- screenshot after: `screenshots/1e9b855ceffc….png`
- screenshot before: `screenshots/1e9b855ceffc….png`

### 7. The click on 'Support' did not cause any changes in the page text or form state, even though the URL and title were updated.

**Severity** 6.0/10 (medium) · **Type** `ui_regression` · **Found by** LLM judge, 90% confident, citation verified against the window
**URL** http://127.0.0.1:63597/support.html

**What should happen** — Clicking a navigation link should update the page content to match the new section.

**What happened** — Clicking 'Support' changed the URL and title but not the page content.

**Why that is wrong** — The click on 'Support' did not cause any changes in the page text or form state, even though the URL and title were updated.

**Evidence**

```
url        : /catalog.html -> /support.html
  title      : 'Northwind Supply — Catalog' -> 'Northwind Supply — Support'
  page text  : Northwind Supply — Support | Home | Catalog | Support | About | Support | Delivery runs Monday to Thursday. Most questions are answered below. | Delivery times | Returns | Invoices | Terms | Privacy | Status | Contact us
```

**Recorded evidence** (episode 1, step 12)

- page after: `pages/4b61ad4f36cf….html`
- page before: `pages/106da00d6af7….html`
- screenshot after: `screenshots/712bc92497e9….png`
- screenshot before: `screenshots/1e9b855ceffc….png`

### 8. The link to 'Step 2' is hidden, making it unreachable from this page.

**Severity** 6.0/10 (medium) · **Type** `ui_regression` · **Found by** LLM judge, 85% confident, citation verified against the window
**URL** http://127.0.0.1:63597/order-1.html

**What should happen** — Starting an order should navigate to the first step of the process and provide clear navigation back to previous steps or home.

**What happened** — Clicking 'Start order' navigated to /order-1.html, changing the title and form state but hiding a link to Step 2.

**Why that is wrong** — The link to 'Step 2' is hidden, making it unreachable from this page.

**Evidence**

```
now hidden : a#to-step-2
```

**Recorded evidence** (episode 1, step 1)

- page after: `pages/f47a11de050b….html`
- page before: `pages/13ff207abecc….html`
- screenshot after: `screenshots/1da8cfb409db….png`
- screenshot before: `screenshots/3ff237637e6f….png`

### 9. The selection of a product did not cause any observable change in the page, such as showing the 'to-step-2' link.

**Severity** 6.0/10 (medium) · **Type** `dead_control` · **Found by** LLM judge, 85% confident, citation verified against the window
**URL** http://127.0.0.1:63597/order-1.html

**What should happen** — Selecting a product should update the form and prepare the user for the next step.

**What happened** — Selecting 'folders-25' updated the form but did not trigger a page reload or URL change.

**Why that is wrong** — The selection of a product did not cause any observable change in the page, such as showing the 'to-step-2' link.

**Evidence**

```
now shown  : a#to-step-2
```

**Recorded evidence** (episode 1, step 2)

- page after: `pages/58596412aa6e….html`
- page before: `pages/f47a11de050b….html`
- screenshot after: `screenshots/19b3f3be34be….png`
- screenshot before: `screenshots/1da8cfb409db….png`

### 10. The page URL changed, but no observable changes were made to the page content or layout.

**Severity** 4.0/10 (medium) · **Type** `ui_regression` · **Found by** LLM judge, 90% confident, citation verified against the window
**URL** http://127.0.0.1:63597/about.html

**What should happen** — A click on a navigation link should change the page content accordingly.

**What happened** — Clicking 'About' navigated from /support.html to /about.html.

**Why that is wrong** — The page URL changed, but no observable changes were made to the page content or layout.

**Evidence**

```
effect     : NO OBSERVABLE CHANGE — the page is byte-identical after this action
```

**Recorded evidence** (episode 1, step 13)

- page after: `pages/bc9fc21265ba….html`
- page before: `pages/4b61ad4f36cf….html`
- screenshot after: `screenshots/340a357a25b0….png`
- screenshot before: `screenshots/712bc92497e9….png`

---

## Run

**Evidence**

- trace directory: `C:\Users\reach\AppData\Local\Temp\claude\c--Users-reach-OneDrive-Code-files-SEM7-RL-web-tester\153893c5-c1a5-45b6-82ac-c03f5197be2a\scratchpad\jv_corpus\deep01-control`
- steps referenced: 11
- page bodies and screenshots are content-addressed; the digests on each finding resolve to files in that directory

```json
{
  "corpus": "deep01-control",
  "judge": "ollama/qwen2.5:7b-instruct",
  "prompt": "compact"
}
```
