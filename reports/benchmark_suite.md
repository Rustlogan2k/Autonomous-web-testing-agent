# Benchmark suite

6 of 6 applications admitted by `scripts/validate_benchmark.py` (every check in a real browser).

| application | name | structure | state | fault | class | witness → minimized | H | random walk (600 steps) | clean-build assertion firings |
|---|---|---|---|---|---|---|---|---|---|
| account_hub | Tidepool account hub | multi-page settings hub; each page is a set of independent p | sessionStorage for committed s | ACCT-01 | calculation_error | 8 → 8 | 4 | nothing verified | 0 |
|  |  |  |  | ACCT-02 | constraint_bypass | 7 → 7 | 3 |  |  |
| admin_records | Brasswick Asset Register | table-driven, multi-page, wide and shallow: every list row c | sessionStorage (register, cust | ADMIN-01 | persistence_failure | 9 → 9 | 1 | nothing verified | 0 |
|  |  |  |  | ADMIN-02 | state_corruption | 7 → 7 | 1 |  |  |
| booking_desk | Millrace Rooms |  | one JSON object in sessionStor | BOOK-01 | calculation_error | 10 → 10 | 1 | nothing verified | 0 |
|  |  |  |  | BOOK-02 | broken_transition | 10 → 10 | 2 |  |  |
| media_library | Reelhouse (Kestrel FM recordings library) |  | sessionStorage: the shelf unde | MEDIA-01 | state_corruption | 6 → 6 | 1 | nothing verified | 0 |
|  |  |  |  | MEDIA-02 | persistence_failure | 6 → 6 | 1 |  |  |
| shop_catalog | Fernbrook Outfitters | The deep-flow fixture carries flow state in the query string | sessionStorage, not the URL qu | SHOP-01 | state_corruption | 8 → 7 | 3 | nothing verified | 0 |
|  |  |  |  | SHOP-02 | constraint_bypass | 7 → 6 | 1 |  |  |
| support_portal | Larkfield Broadband care desk |  | sessionStorage draft (larkfiel | SUPP-01 | constraint_bypass | 9 → 9 | 1 | nothing verified | 0 |
|  |  |  |  | SUPP-02 | broken_transition | 7 → 7 | 1 |  |  |

Witness → minimized: the reachability witness's length and the length delta debugging reduced it to while it still fired on the faulty build and stayed silent on the clean one. H: steps between the triggering action and the page where the violation is visible.
