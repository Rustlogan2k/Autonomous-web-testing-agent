# demo_repo — intake fixture

A minimal repository used to demonstrate the Repo Intake path end to end:

    repo -> detection -> policy gate -> build -> run -> health check
         -> base_url -> WebFunctionalEnv -> exploration -> judge -> bug report

It is deliberately trivial: `busybox httpd` serving four static pages, with no build-time
package installation, so it builds under the default `--build-network none` and the demo
measures the pipeline rather than a package manager.

Three defects are seeded, chosen so both halves of the detector have something to find:

| id | page | defect | found by |
|---|---|---|---|
| DEMO-01 | index.html | `Pricing` links to a page that does not exist | deterministic (404) |
| DEMO-02 | catalog.html | `Export catalog` throws an uncaught `TypeError` and does nothing | deterministic (console) + judge (dead control) |
| DEMO-03 | contact.html | `message` declares `maxlength=20`, and nothing enforces it | judge only |

DEMO-03 is the one that matters for the argument: no console error, no HTTP error, every
page returns 200, and the only way to notice is to compare what the field declared against
what the application accepted.

Run it:

    python scripts/run_repo.py --repo tests/fixtures/demo_repo --judge stub
