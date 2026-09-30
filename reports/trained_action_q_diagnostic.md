# Trained-checkpoint action-Q diagnostic

## 1. Executive conclusion

Across 6096 genuine within-state CLICK-CLICK action pairs scored by the 8 final post-Markov checkpoints, action-label cosine correlates with |Q gap| at Pearson -0.0396 / Spearman -0.0239. Mean |Q gap| is 0.2027 against a reward scale of 1.5-2.5.

**Read the magnitude, not the p-value.** The dueling decomposition makes `V(s)` cancel within a state, so some relationship is structurally guaranteed; what matters is whether the induced Q differences are large enough to change a ranking at the scale the reward operates on.

## 2. Capture methodology

17 real observations captured from the deep fixture by `scripts/capture_action_q_states.py`, built with the production `encode_modalities` call that `evaluation.TrainedPolicy.act` uses. Gated states were reached by walking the flow through `setup_actions`, so `Continue` appears only where the gate genuinely opens. No state was constructed offline.

## 3. Dataset and coverage

- states: 17, checkpoints: 16 (8 seeds x init/final)
- per-action Q observations: 5488
- within-state pair observations: 53648

| Requested pair | Co-valid state captured? | N states | N checkpoint evals |
|---|---|---|---|
| Place order / Cancel order | yes | 2 | 32 |
| Place order / Back | yes | 2 | 32 |
| Place order / Start order | **no** - both labels exist but never co-valid in any captured state | 0 | 0 |
| Place order / Continue | **no** - both labels exist but never co-valid in any captured state | 0 | 0 |
| Next / Continue | **no** - not present in current fixture | 0 | 0 |
| Login / Sign in | **no** - not present in current fixture | 0 | 0 |
| Back / Previous | **no** - not present in current fixture | 0 | 0 |

## 4. Within-state Q-ranking analysis

| subset | n | Pearson | Spearman | mean \|Q gap\| |
|---|---|---|---|---|
| all_pairs_final | 26824 | -0.1039 | -0.0810 | 0.27415 |
| same_type_final | 6960 | -0.2205 | -0.2210 | 0.183448 |
| click_click_final | 6096 | -0.0396 | -0.0239 | 0.202665 |
| type_type_final | 320 | n/a | n/a | 0.049065 |
| cross_type_final | 19864 | -0.0300 | -0.0153 | 0.305931 |

### Mean |Q gap| by cosine bin (CLICK-CLICK, final)

| cosine bin | n | mean \|Q gap\| | median |
|---|---|---|---|
| [-1.01, -0.25) | 112 | 0.346014 | 0.227979 |
| [-0.25, +0.00) | 2448 | 0.200483 | 0.134084 |
| [+0.00, +0.25) | 2792 | 0.197707 | 0.125312 |
| [+0.25, +0.50) | 592 | 0.229267 | 0.162347 |
| [+0.50, +1.01) | 152 | 0.119663 | 0.092809 |

## 5. Place order vs Cancel order

Hypothesis category: *misleadingly similar (opposite outcomes)*. 32 observations.

| phase | n | cosine | mean gap (Place-other) | Place higher | other higher |
|---|---|---|---|---|---|
| init | 16 | +0.4244 | -0.0019 | 6 | 10 |
| final | 16 | +0.4244 | +0.2616 | 13 | 3 |

## 6. Place order vs Back

Hypothesis category: *behaviourally distinct*. 32 observations.

| phase | n | cosine | mean gap (Place-other) | Place higher | other higher |
|---|---|---|---|---|---|
| init | 16 | -0.2425 | -0.0039 | 4 | 12 |
| final | 16 | -0.2425 | +0.2706 | 12 | 4 |

## 7. Init vs final

| phase | n | Pearson | mean \|Q gap\| |
|---|---|---|---|
| init | 6096 | -0.0905 | 0.006033 |
| final | 6096 | -0.0396 | 0.202665 |

Q-gap growth factor init to final: **33.593x**. Final mean |Q gap| is 0.1351 of the lower reward-scale bound.

## 8. Order-4 deadlock analysis

At `flow:order-4-deadlock` (20 valid actions), across the 8 final checkpoints:

- `Place order` is the argmax in **5/8** seeds
- ranked above `Cancel order` in **6/8**
- ranked above `Back` in **6/8**
- observed argmax labels: ['Back', 'Cancel order', 'Place order']

| seed | argmax | rank(Place) | Q(Place) | rank(Cancel) | rank(Back) |
|---|---|---|---|---|---|
| 0 | Place order | 0 | +0.8879 | 1 | 3 |
| 1 | Cancel order | 3 | -1.5212 | 0 | 2 |
| 2 | Back | 2 | -1.0146 | 3 | 0 |
| 3 | Place order | 0 | +0.8306 | 1 | 3 |
| 4 | Place order | 0 | -0.2232 | 4 | 1 |
| 5 | Cancel order | 1 | -0.8267 | 0 | 2 |
| 6 | Place order | 0 | +1.4933 | 2 | 5 |
| 7 | Place order | 0 | +0.4337 | 3 | 2 |

## 9. Limitations

- Five of the seven requested pairs have **zero** co-valid states; three of those labels (`Next`, `Previous`, `Login`/`Sign in`) do not exist in any fixture. Report them as unavailable, not as negative evidence.
- One fixture, 17 states, one action vocabulary. Nothing here generalises to another application.
- Repeated measures: the same pair is scored by 8 seeds, so pooled n overstates independence. Per-seed figures are reported alongside.
- A cosine/|Q gap| relationship is structurally expected under the dueling head and is not by itself evidence of a defect.
- This is an observational diagnostic. It cannot establish that the representation caused the deadlock; only an intervention could.
