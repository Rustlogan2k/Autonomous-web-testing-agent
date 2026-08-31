# Bug report — deep_flow_site::deep01-noisy

Generated 2026-08-30T09:42:12+00:00

**2 findings** — 2 high, 0 medium, 0 low.

0 were found by deterministic triggers and would have been caught without a language model. 2 required semantic judgment.

> 4 further verdict(s) were **excluded**: the evidence they quoted does not occur in what the judge was shown. They are listed at the end as evidence about the judge, not about the application.

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

### 2. The URL changed as expected, but the content did not reflect any changes related to the order placement. The product name and quantity were incorrectly displaye

**Severity** 10.0/10 (high) · **Type** `broken_flow` · **Found by** LLM judge, 100% confident, citation verified against the window
**URL** http://127.0.0.1:63597/receipt.html?product=folders-25&quantity=42&email=tester%40example.com

**What should happen** — Upon clicking 'Place order', the application should have processed the order and redirected to a confirmation page displaying the details of the order.

**What happened** — The 'Place order' button was clicked, which navigated from /order-4.html to /receipt.html.

**Why that is wrong** — The URL changed as expected, but the content did not reflect any changes related to the order placement. The product name and quantity were incorrectly displayed as 'folders-25' and '1', respectively, instead of 'folders-25' and '42'.

**Evidence**

```
Product: | folders-25 | Quantity: | 1 | Contact: | tester@example.com
```

**Recorded evidence** (episode 1, step 13)

- page after: `pages/0668fa0c8e68….html`
- page before: `pages/db82a35d8df4….html`
- screenshot after: `screenshots/d38cea86bb0f….png`
- screenshot before: `screenshots/a5fe37e7765f….png`

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
  "prompt": "detailed"
}
```
