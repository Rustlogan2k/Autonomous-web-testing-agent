# Bug report — deep_flow_site::deep01-scripted

Generated 2026-08-30T09:42:12+00:00

**2 findings** — 2 high, 0 medium, 0 low.

0 were found by deterministic triggers and would have been caught without a language model. 2 required semantic judgment.

> 4 further verdict(s) were **excluded**: the evidence they quoted does not occur in what the judge was shown. They are listed at the end as evidence about the judge, not about the application.

> Severity is a CVSS-*inspired* 0-10 ordering, not a CVSS score. Judge severities are the model's own 0-1 rating rescaled; deterministic ones are fixed per trigger.

---

### 1. No change in URL or form state was observed, even though a selection was made.

**Severity** 10.0/10 (high) · **Type** `dead_control` · **Found by** LLM judge, 100% confident, citation verified against the window
**URL** http://127.0.0.1:63597/order-1.html

**What should happen** — The application should update the form state to reflect the selected product and show a link to proceed to the next step.

**What happened** — SELECT on 'product' with value 'folders-25'

**Why that is wrong** — No change in URL or form state was observed, even though a selection was made.

**Evidence**

```
url        : /order-1.html (unchanged)
form state : product: unselected -> selected
```

**Recorded evidence** (episode 1, step 2)

- page after: `pages/58596412aa6e….html`
- page before: `pages/f47a11de050b….html`
- screenshot after: `screenshots/19b3f3be34be….png`
- screenshot before: `screenshots/1da8cfb409db….png`

### 2. The URL query string includes 'quantity' even though the field was empty. This implies an automatic form submission which did not occur based on the user action

**Severity** 10.0/10 (high) · **Type** `broken_flow` · **Found by** LLM judge, 100% confident, citation verified against the window
**URL** http://127.0.0.1:63597/order-2.html?product=folders-25

**What should happen** — The application should have navigated to /order-2.html with the product parameter set and pre-filled the quantity field with a default value or left it as empty if no default is applicable, but not change the URL query string in such a way that suggests a form submission occurred without user interaction.

**What happened** — A CLICK on 'Continue' led to /order-2.html?product=folders-25 with an empty quantity field.

**Why that is wrong** — The URL query string includes 'quantity' even though the field was empty. This implies an automatic form submission which did not occur based on the user action (a simple click).

**Evidence**

```
url: /order-1.html -> /order-2.html?product=folders-25
```

**Recorded evidence** (episode 1, step 3)

- page after: `pages/c6d5cc0caa00….html`
- page before: `pages/58596412aa6e….html`
- screenshot after: `screenshots/c5fa7263ddf6….png`
- screenshot before: `screenshots/19b3f3be34be….png`

---

## Excluded: verdicts whose evidence was not in the window

These are **not** findings about the application. Each one quotes something the judge was never shown, which makes the verdict unreliable regardless of whether it happens to be right.

### 1. The email field was left empty after navigating to /order-3.html. A correct application would either pre-fill the email field with a default value (e.g., from s

**Severity** 10.0/10 (high) · **Type** `validation_bypass` · **Found by** LLM judge, 100% confident, citation verified against the window
**URL** http://127.0.0.1:63597/order-3.html?product=folders-25&quantity=42

**What should happen** — The application should have populated the email field with a default value or validated that it is not left empty before proceeding to the next step.

**What happened** — A 'Continue' button click on /order-2.html?product=folders-25 led to /order-3.html?product=folders-25&quantity=42, with an empty email field.

**Why that is wrong** — The email field was left empty after navigating to /order-3.html. A correct application would either pre-fill the email field with a default value (e.g., from session data) or enforce validation to ensure the field is filled before allowing further progress.

**Evidence**

```
In /order-2.html, the 'Continue' button was clicked, but in /order-3.html, the email field remained empty despite no validation error being observed.
```

**Recorded evidence** (episode 1, step 5)

- page after: `pages/23a70cf7554b….html`
- page before: `pages/87c0ac2aad06….html`
- screenshot after: `screenshots/e50887ac7b2a….png`
- screenshot before: `screenshots/23674b71b4b0….png`

### 2. No validation error was shown for an invalid email address (e.g., missing '@' symbol).

**Severity** 10.0/10 (high) · **Type** `validation_bypass` · **Found by** LLM judge, 100% confident, citation verified against the window
**URL** http://127.0.0.1:63597/order-3.html?product=folders-25&quantity=42

**What should happen** — The application should validate the email field according to its declared constraint [type=email] and display no errors or warnings.

**What happened** — TYPE on 'email' with value 'tester@example.com'

**Why that is wrong** — No validation error was shown for an invalid email address (e.g., missing '@' symbol).

**Evidence**

```
The 'email' field accepted a value without '@', which is not a valid email format, but no validation error message was displayed.
```

**Recorded evidence** (episode 1, step 6)

- page after: `pages/d036a4ebec26….html`
- page before: `pages/23a70cf7554b….html`
- screenshot after: `screenshots/5aa23f04d905….png`
- screenshot before: `screenshots/e50887ac7b2a….png`

### 3. No observable change occurred after clicking 'Continue'. The URL changed as expected, but the page content did not update to reflect the final step of the order

**Severity** 10.0/10 (high) · **Type** `broken_flow` · **Found by** LLM judge, 100% confident, citation verified against the window
**URL** http://127.0.0.1:63597/order-4.html?product=folders-25&quantity=42&email=tester%40example.com

**What should happen** — The application should navigate to a confirmation or summary page, showing the selected product, quantity, and email address.

**What happened** — CLICK on 'Continue'

**Why that is wrong** — No observable change occurred after clicking 'Continue'. The URL changed as expected, but the page content did not update to reflect the final step of the order process.

**Evidence**

```
The title remained unchanged at 'Northwind Supply — Order, step 3 of 4', and the page text did not display any summary or confirmation information for the order details. The links were still those from step 3, indicating no change in the UI state.
```

**Recorded evidence** (episode 1, step 7)

- page after: `pages/db82a35d8df4….html`
- page before: `pages/d036a4ebec26….html`
- screenshot after: `screenshots/7296624d3e4e….png`
- screenshot before: `screenshots/5aa23f04d905….png`

### 4. The quantity of the product on the receipt page shows as 1 instead of 42.

**Severity** 10.0/10 (high) · **Type** `broken_flow` · **Found by** LLM judge, 100% confident, citation verified against the window
**URL** http://127.0.0.1:63597/receipt.html?product=folders-25&quantity=42&email=tester%40example.com

**What should happen** — Upon clicking 'Place order', the application should have processed the order and displayed a receipt page with the correct product, quantity, and email information. The URL change is expected but does not indicate any issues by itself.

**What happened** — The 'Place order' button was clicked, and the URL changed to /receipt.html?product=folders-25&quantity=42&email=tester%40example.com.

**Why that is wrong** — The quantity of the product on the receipt page shows as 1 instead of 42.

**Evidence**

```
On the final step, the page text states: 'Quantity: | 1', whereas the expected value was 42 based on the form input in previous steps.
```

**Recorded evidence** (episode 1, step 8)

- page after: `pages/0668fa0c8e68….html`
- page before: `pages/db82a35d8df4….html`
- screenshot after: `screenshots/d38cea86bb0f….png`
- screenshot before: `screenshots/7296624d3e4e….png`

---

## Run

**Evidence**

- trace directory: `C:\Users\reach\AppData\Local\Temp\claude\c--Users-reach-OneDrive-Code-files-SEM7-RL-web-tester\153893c5-c1a5-45b6-82ac-c03f5197be2a\scratchpad\jv_corpus\deep01-scripted`
- steps referenced: 6
- page bodies and screenshots are content-addressed; the digests on each finding resolve to files in that directory

```json
{
  "corpus": "deep01-scripted",
  "judge": "ollama/qwen2.5:7b-instruct",
  "prompt": "detailed"
}
```
