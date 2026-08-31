# Bug report — deep_flow_site::deep01-noisy

Generated 2026-08-30T09:42:12+00:00

**5 findings** — 2 high, 3 medium, 0 low.

0 were found by deterministic triggers and would have been caught without a language model. 5 required semantic judgment.

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

### 3. The click did not navigate to /order-4.html as expected, and the page content did not change.

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

### 4. The link to 'Step 2' is hidden, making it unreachable from this page.

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

### 5. The selection of a product did not cause any observable change in the page, such as showing the 'to-step-2' link.

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

---

## Run

**Evidence**

- trace directory: `C:\Users\reach\AppData\Local\Temp\claude\c--Users-reach-OneDrive-Code-files-SEM7-RL-web-tester\153893c5-c1a5-45b6-82ac-c03f5197be2a\scratchpad\jv_corpus\deep01-noisy`
- steps referenced: 6
- page bodies and screenshots are content-addressed; the digests on each finding resolve to files in that directory

```json
{
  "corpus": "deep01-noisy",
  "judge": "ollama/qwen2.5:7b-instruct",
  "prompt": "compact"
}
```
