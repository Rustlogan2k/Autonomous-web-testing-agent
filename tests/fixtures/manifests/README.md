# Manifest fixtures

Realistically-shaped dependency manifests, one directory per ecosystem, used only by
`tests/unit/test_repo_profile_manifests.py`.

These exist because the profiler's ecosystem detectors were covered only by *synthetic*
trees written alongside the detectors — a one-line `go.mod`, a two-line `Cargo.toml`.
A manifest written by the same person who wrote the parser tends to be the manifest the
parser already handles. Real ones have multi-line `require (…)` blocks, `// indirect`
comments, inline tables, version ranges, dependency groups and XML.

Nothing here is built, installed or executed. They are input to a pure-function
profiler, so they carry no lockfiles, no vendored code and no real source.
