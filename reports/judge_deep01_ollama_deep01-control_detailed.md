# Bug report — deep_flow_site::deep01-control

Generated 2026-08-30T09:42:12+00:00

**3 findings** — 3 high, 0 medium, 0 low.

0 were found by deterministic triggers and would have been caught without a language model. 3 required semantic judgment.

> 5 further verdict(s) were **excluded**: the evidence they quoted does not occur in what the judge was shown. They are listed at the end as evidence about the judge, not about the application.

> Severity is a CVSS-*inspired* 0-10 ordering, not a CVSS score. Judge severities are the model's own 0-1 rating rescaled; deterministic ones are fixed per trigger.

---

### 1. The quantity field remains empty after navigating to /order-2.html, even though it is required and has constraints [type=number min=1 max=100 step=1].

**Severity** 10.0/10 (high) · **Type** `validation_bypass` · **Found by** LLM judge, 100% confident, citation verified against the window
**URL** http://127.0.0.1:63597/order-2.html?product=folders-25

**What should happen** — Upon clicking 'Continue', the application should validate that a valid quantity is entered and either display an error or populate the quantity field if it was left empty. The URL should include the selected product (folders-25) but the form should have a non-empty default value for the quantity field to guide the user.

**What happened** — The user clicked 'Continue' on /order-1.html, which navigated to /order-2.html?product=folders-25 with an empty quantity field.

**Why that is wrong** — The quantity field remains empty after navigating to /order-2.html, even though it is required and has constraints [type=number min=1 max=100 step=1].

**Evidence**

```
form state: quantity: empty -> empty
```

**Recorded evidence** (episode 1, step 3)

- page after: `pages/c6d5cc0caa00….html`
- page before: `pages/58596412aa6e….html`
- screenshot after: `screenshots/c5fa7263ddf6….png`
- screenshot before: `screenshots/19b3f3be34be….png`

### 2. The application did not handle the click event correctly by not updating the page or triggering any side effects.

**Severity** 10.0/10 (high) · **Type** `dead_control` · **Found by** LLM judge, 100% confident, citation verified against the window
**URL** http://127.0.0.1:63597/catalog.html

**What should happen** — A click on a link that is already in the current URL should not cause any changes unless there is a specific reason for it (e.g., dynamic content update).

**What happened** — The user clicked on 'Catalog' which was already the current page, leading to no observable change.

**Why that is wrong** — The application did not handle the click event correctly by not updating the page or triggering any side effects.

**Evidence**

```
STEP 6: CLICK on 'Catalog' selector   : [id="nav-catalog"] url        : /catalog.html (unchanged) effect     : NO OBSERVABLE CHANGE — the page is byte-identical after this action
```

**Recorded evidence** (episode 1, step 11)

- page after: `pages/106da00d6af7….html`
- page before: `pages/106da00d6af7….html`
- screenshot after: `screenshots/1e9b855ceffc….png`
- screenshot before: `screenshots/1e9b855ceffc….png`

### 3. The URL changed correctly to /support.html, but there is no observable change in the page content or layout. The title and links are updated as expected, but th

**Severity** 10.0/10 (high) · **Type** `ui_regression` · **Found by** LLM judge, 100% confident, citation verified against the window
**URL** http://127.0.0.1:63597/support.html

**What should happen** — Navigating to the support page should update the title, headings, and visible text accordingly.

**What happened** — The user clicked on 'Support' which navigated to /support.html.

**Why that is wrong** — The URL changed correctly to /support.html, but there is no observable change in the page content or layout. The title and links are updated as expected, but the page text remains unchanged from the catalog page.

**Evidence**

```
NO OBSERVABLE CHANGE — the page is byte-identical after this action
```

**Recorded evidence** (episode 1, step 12)

- page after: `pages/4b61ad4f36cf….html`
- page before: `pages/106da00d6af7….html`
- screenshot after: `screenshots/712bc92497e9….png`
- screenshot before: `screenshots/1e9b855ceffc….png`

---

## Excluded: verdicts whose evidence was not in the window

These are **not** findings about the application. Each one quotes something the judge was never shown, which makes the verdict unreliable regardless of whether it happens to be right.

### 1. No change was observed after selecting the 'folders-25' option. The page did not display any relevant information about the selected product.

**Severity** 10.0/10 (high) · **Type** `dead_control` · **Found by** LLM judge, 100% confident, citation verified against the window
**URL** http://127.0.0.1:63597/order-1.html

**What should happen** — A selection in the 'product' dropdown should update the displayed text or show additional information related to the chosen product, such as a description or price.

**What happened** — The 'product' field was selected with value 'folders-25', but no observable change occurred on the page.

**Why that is wrong** — No change was observed after selecting the 'folders-25' option. The page did not display any relevant information about the selected product.

**Evidence**

```
The form state shows that the 'product' field is now selected, but no new text or elements were made visible on the page.
```

**Recorded evidence** (episode 1, step 2)

- page after: `pages/58596412aa6e….html`
- page before: `pages/f47a11de050b….html`
- screenshot after: `screenshots/19b3f3be34be….png`
- screenshot before: `screenshots/1da8cfb409db….png`

### 2. The 'quantity' field was typed with a value of '42', but no validation error or feedback was provided, even though the field declares [type=number min=1 max=100

**Severity** 10.0/10 (high) · **Type** `validation_bypass` · **Found by** LLM judge, 100% confident, citation verified against the window
**URL** http://127.0.0.1:63597/order-2.html?product=folders-25

**What should happen** — The application should validate the input and ensure it meets the constraints declared by the field.

**What happened** — quantity: empty -> filled

**Why that is wrong** — The 'quantity' field was typed with a value of '42', but no validation error or feedback was provided, even though the field declares [type=number min=1 max=100 step=1].

**Evidence**

```
Constraints: quantity declares [type=number min=1 max=100 step=1], but no validation message or error is shown when '42' is entered.
```

**Recorded evidence** (episode 1, step 4)

- page after: `pages/87c0ac2aad06….html`
- page before: `pages/c6d5cc0caa00….html`
- screenshot after: `screenshots/23674b71b4b0….png`
- screenshot before: `screenshots/c5fa7263ddf6….png`

### 3. 'Continue' button does not navigate to the expected page '/order-3.html?product=folders-25&quantity=42'. Instead, it seems to have navigated back to an earlier 

**Severity** 10.0/10 (high) · **Type** `broken_navigation` · **Found by** LLM judge, 100% confident, citation verified against the window
**URL** http://127.0.0.1:63597/order-3.html?product=folders-25&quantity=42

**What should happen** — The application should navigate to the next step, /order-4.html, with all form data correctly preserved.

**What happened** — CLICK on 'Continue'

**Why that is wrong** — 'Continue' button does not navigate to the expected page '/order-3.html?product=folders-25&quantity=42'. Instead, it seems to have navigated back to an earlier state or failed to update the URL and title as expected.

**Evidence**

```
The URL remains at /order-2.html?product=folders-25&quantity=42, and the title does not change to 'Northwind Supply — Order, step 3 of 4'.
```

**Recorded evidence** (episode 1, step 5)

- page after: `pages/23a70cf7554b….html`
- page before: `pages/87c0ac2aad06….html`
- screenshot after: `screenshots/e50887ac7b2a….png`
- screenshot before: `screenshots/23674b71b4b0….png`

### 4. The email field was filled without any validation error messages, and no further navigation occurred. This suggests that the application did not enforce the ema

**Severity** 10.0/10 (high) · **Type** `validation_bypass` · **Found by** LLM judge, 100% confident, citation verified against the window
**URL** http://127.0.0.1:63597/order-3.html?product=folders-25&quantity=42

**What should happen** — The application should validate the email field according to its declared constraint [type=email] and possibly show a success message or proceed to the next step.

**What happened** — TYPE on 'email' with value 'tester@example.com'

**Why that is wrong** — The email field was filled without any validation error messages, and no further navigation occurred. This suggests that the application did not enforce the email format constraint.

**Evidence**

```
Constraints: email declares [type=email]. Field value: email: '' -> 'tester@example.com'. No validation errors or navigation to the next step were observed.
```

**Recorded evidence** (episode 1, step 6)

- page after: `pages/d036a4ebec26….html`
- page before: `pages/23a70cf7554b….html`
- screenshot after: `screenshots/5aa23f04d905….png`
- screenshot before: `screenshots/e50887ac7b2a….png`

### 5. No observable change in the form state or page text. The email field is empty again as expected, but there is no indication that the quantity value of '42' was 

**Severity** 10.0/10 (high) · **Type** `broken_flow` · **Found by** LLM judge, 100% confident, citation verified against the window
**URL** http://127.0.0.1:63597/order-3.html

**What should happen** — Navigating back should revert all form states and page content to their previous state before the forward navigation.

**What happened** — The user clicked 'Back' from step 4, which correctly navigated to step 3.

**Why that is wrong** — No observable change in the form state or page text. The email field is empty again as expected, but there is no indication that the quantity value of '42' was reset.

**Evidence**

```
The quantity field still shows a value of '42', which it should not if the navigation back to step 3 were correct.
```

**Recorded evidence** (episode 1, step 8)

- page after: `pages/23a70cf7554b….html`
- page before: `pages/db82a35d8df4….html`
- screenshot after: `screenshots/e50887ac7b2a….png`
- screenshot before: `screenshots/7296624d3e4e….png`

---

## Run

**Evidence**

- trace directory: `C:\Users\reach\AppData\Local\Temp\claude\c--Users-reach-OneDrive-Code-files-SEM7-RL-web-tester\153893c5-c1a5-45b6-82ac-c03f5197be2a\scratchpad\jv_corpus\deep01-control`
- steps referenced: 8
- page bodies and screenshots are content-addressed; the digests on each finding resolve to files in that directory

```json
{
  "corpus": "deep01-control",
  "judge": "ollama/qwen2.5:7b-instruct",
  "prompt": "detailed"
}
```
