# Out-of-list vocabulary probe

*2026-09-18T12:51:35+00:00 · probe `2026-09-18.out-of-list.1` · git `7b6182c`*

Does A2's anchor projection keep discriminating commit-like from navigate-like controls when every head word is outside A1's frozen word list? No RL training; four scorers, two frozen label sets.

## Pre-registered reading

> A2 on the 48-D projection reaches **AUC >= 0.80 with a bootstrap 95% lower bound above 0.5** on tier 1, or the probe does not establish that the projection generalises past A1's vocabulary.

**PREDICTION PARTIALLY MET. A2 reaches AUC 0.764 with a 95% lower bound of 0.556 -- above chance, below the pre-specified 0.80. Some discrimination survives outside A1's vocabulary, less than the clean reading required.**

## tier1_out_of_list  (12 commit / 12 navigate)

Rule: every token of every label is absent from A1's positive and negative lists.

> A1 scores 0.0 on all 24 labels, so its AUC is exactly 0.5 by construction. Reported, not discovered.

| scorer | AUC | 95% CI (over items) | items | ordered pairs |
|---|---|---|---|---|
| `A1_lexical_frozen_indicator` | **0.500** | [0.500, 0.500] | 24 | 144 |
| `A1_lexical_count_variant` | **0.500** | [0.500, 0.500] | 24 | 144 |
| `A2_anchor_on_projected_48d` | **0.764** | [0.556, 0.931] | 24 | 144 |
| `A2_anchor_on_raw_minilm_384d` | **0.750** | [0.535, 0.924] | 24 | 144 |
| `anchor_on_trigram_hash_32d` | **0.424** | [0.201, 0.660] | 24 | 144 |

<details><summary>per-label scores</summary>

| label | category | `A1_lexical_frozen_indicator` | `A1_lexical_count_variant` | `A2_anchor_on_projected_48d` | `A2_anchor_on_raw_minilm_384d` | `anchor_on_trigram_hash_32d` |
|---|---|---|---|---|---|---|
| Finalise the booking | commit | +0.0000 | +0.0000 | +0.1202 | +0.1545 | +0.1857 |
| Authorise the transaction | commit | +0.0000 | +0.0000 | +0.0657 | +0.0735 | +0.3019 |
| Ratify the agreement | commit | +0.0000 | +0.0000 | -0.0188 | +0.0621 | -0.2194 |
| Dispatch the parcel | commit | +0.0000 | +0.0000 | +0.2688 | +0.1912 | +0.2188 |
| Endorse the document | commit | +0.0000 | +0.0000 | -0.1872 | +0.0557 | +0.0567 |
| Execute the contract | commit | +0.0000 | +0.0000 | +0.2137 | +0.1316 | +0.0136 |
| Enact the amendment | commit | +0.0000 | +0.0000 | -0.0507 | +0.0080 | +0.0635 |
| Lodge the appeal | commit | +0.0000 | +0.0000 | +0.0539 | +0.1579 | -0.0011 |
| Remit the balance | commit | +0.0000 | +0.0000 | -0.0658 | +0.0731 | -0.1752 |
| Countersign the lease | commit | +0.0000 | +0.0000 | -0.1817 | -0.0936 | +0.1133 |
| Commission the artwork | commit | +0.0000 | +0.0000 | +0.0019 | +0.1509 | +0.0471 |
| Validate the entry | commit | +0.0000 | +0.0000 | +0.2780 | +0.1669 | +0.1830 |
| Browse the archive | navigate | +0.0000 | +0.0000 | -0.2296 | -0.0882 | +0.2746 |
| Peruse the listings | navigate | +0.0000 | +0.0000 | -0.0716 | +0.1174 | +0.0955 |
| Revisit the earlier screen | navigate | +0.0000 | +0.0000 | -0.2958 | -0.1872 | +0.2481 |
| Wander through the gallery | navigate | +0.0000 | +0.0000 | -0.0274 | +0.0594 | +0.0418 |
| Skim the announcements | navigate | +0.0000 | +0.0000 | -0.0053 | +0.1334 | +0.2424 |
| Inspect the directory | navigate | +0.0000 | +0.0000 | -0.1799 | -0.0870 | +0.1138 |
| Traverse the sitemap | navigate | +0.0000 | +0.0000 | -0.2165 | -0.0477 | +0.0764 |
| Navigate to the lobby | navigate | +0.0000 | +0.0000 | -0.1809 | +0.0104 | -0.1225 |
| Explore the showroom | navigate | +0.0000 | +0.0000 | -0.0111 | +0.0771 | +0.0815 |
| Scan the bulletin | navigate | +0.0000 | +0.0000 | +0.0057 | +0.0077 | -0.0302 |
| Visit the reading room | navigate | +0.0000 | +0.0000 | -0.1942 | -0.0187 | +0.1845 |
| Tour the exhibition | navigate | +0.0000 | +0.0000 | +0.0666 | +0.1407 | +0.0239 |

</details>

## tier2_partial_coverage  (8 commit / 8 navigate)

Rule: head verb absent from A1; exactly one A1 positive token per label; the same token multiset on both sides.

> A1 scores +1.0 on all 16 labels, so its AUC is exactly 0.5 by construction. The word list is loud and uninformative here, rather than silent as in tier 1.

| scorer | AUC | 95% CI (over items) | items | ordered pairs |
|---|---|---|---|---|
| `A1_lexical_frozen_indicator` | **0.500** | [0.500, 0.500] | 16 | 64 |
| `A1_lexical_count_variant` | **0.500** | [0.500, 0.500] | 16 | 64 |
| `A2_anchor_on_projected_48d` | **0.844** | [0.609, 1.000] | 16 | 64 |
| `A2_anchor_on_raw_minilm_384d` | **0.766** | [0.500, 0.969] | 16 | 64 |
| `anchor_on_trigram_hash_32d` | **0.516** | [0.219, 0.812] | 16 | 64 |

<details><summary>per-label scores</summary>

| label | category | `A1_lexical_frozen_indicator` | `A1_lexical_count_variant` | `A2_anchor_on_projected_48d` | `A2_anchor_on_raw_minilm_384d` | `anchor_on_trigram_hash_32d` |
|---|---|---|---|---|---|---|
| Finalise the order | commit | +1.0000 | +1.0000 | +0.3758 | +0.3622 | +0.2491 |
| Ratify the purchase | commit | +1.0000 | +1.0000 | +0.4541 | +0.2831 | +0.1331 |
| Authorise the checkout | commit | +1.0000 | +1.0000 | +0.2407 | +0.1749 | -0.0342 |
| Dispatch the order | commit | +1.0000 | +1.0000 | +0.5745 | +0.4179 | +0.2421 |
| Countersign the order | commit | +1.0000 | +1.0000 | +0.3364 | +0.2225 | +0.2544 |
| Endorse the purchase | commit | +1.0000 | +1.0000 | +0.2202 | +0.2623 | +0.1644 |
| Execute the order | commit | +1.0000 | +1.0000 | +0.5907 | +0.4776 | +0.2183 |
| Validate the checkout | commit | +1.0000 | +1.0000 | +0.3342 | +0.2132 | -0.1101 |
| Browse the order history | navigate | +1.0000 | +1.0000 | +0.0342 | +0.0786 | +0.2389 |
| Peruse the purchase records | navigate | +1.0000 | +1.0000 | +0.2242 | +0.2298 | +0.2935 |
| Revisit the checkout guide | navigate | +1.0000 | +1.0000 | +0.1839 | +0.1990 | +0.0065 |
| Skim the order summary | navigate | +1.0000 | +1.0000 | -0.0151 | +0.0763 | +0.2099 |
| Inspect the order archive | navigate | +1.0000 | +1.0000 | +0.2543 | +0.2420 | +0.1774 |
| Tour the purchase gallery | navigate | +1.0000 | +1.0000 | +0.2979 | +0.2554 | +0.1906 |
| Scan the order listings | navigate | +1.0000 | +1.0000 | +0.3809 | +0.2721 | +0.0973 |
| Survey the checkout statistics | navigate | +1.0000 | +1.0000 | +0.0646 | +0.1874 | +0.0270 |

</details>

## Coverage bookkeeping

A1 vocabulary `2026-09-18.frozen`, 35 tokens. Required head words absent from it: `finalise` yes, `authorise` yes, `ratify` yes, `dispatch` yes.

Tier-1 labels carrying any A1 token: none.

Tier-1 tokens that *are* in the projection's fit corpus: `archive`, `booking`, `browse`, `to`.

Probe labels that also appear in `HELD_OUT_PROBE`: ['Dispatch the parcel'].

> A1 has never seen any tier-1 token. The projection was fitted on a corpus that contains some of them. That asymmetry is the experiment's subject, not a flaw -- a word list cannot generalise past its words and an embedding is supposed to -- but it means a tier-1 win for A2 is evidence about *this* fitted projection, not about sentence embeddings in general.

## What this cannot establish

- **This is not an experiment about policies.** Nothing here runs an environment, a browser or a learner. A scorer that ranks labels well may still not change what an agent does, and the A0/A1/A2 run is what speaks to that.
- **The label sets were written by one author who knew A2's anchors.** They were frozen in git before the first run, which rules out tuning against results, not the possibility that the phrasing unconsciously suits the anchors. The trigram-hash row is the floor control that makes that visible: if the hash also scores well, the population is separable by surface form rather than by meaning.
- **Category assignment is a judgement.** 'Commit-like' and 'navigate-like' were assigned by the author before scoring and not revised. Someone else might place `Inspect the directory` differently.
- **n is 24 items in tier 1 and 16 in tier 2.** The intervals resample labels and are correspondingly wide. This is a cheap diagnostic, and it is powered to separate 'holds up' from 'collapses', not to resolve small differences.
- **A2's projection was fitted on a corpus sharing some of these tokens; A1's list was not.** The coverage table above gives the exact overlap. A tier-1 win for A2 is evidence about this fitted projection, not about frozen sentence embeddings in general.
