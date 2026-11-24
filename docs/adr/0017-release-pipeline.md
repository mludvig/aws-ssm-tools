# 0017. One release job publishes the tested artifacts

## Status
Accepted (2026-11-24)

## Context
The workflow token could write to everything in every job, and actions were pinned by tag. On a tag, `publish` rebuilt the package for PyPI without waiting for `build-windows`. v2.2.1 went to PyPI two minutes before the Windows build finished. The Windows build installed its dependencies unlocked, and nothing recorded what the release files were.

## Decision
- The token is read-only by default. Only the tag-only `release` job gets `contents: write` and the attestation permissions.
- `release` needs `build` and `build-windows`. It publishes their artifacts as they are: it checks the tag against the wheel version, writes `SHA256SUMS`, attests provenance, creates the GitHub release (a pre-release when the version has a letter, e.g. `3.0.0b1`), and runs `uv publish` last, because a PyPI upload can't be undone.
- Actions are pinned by commit SHA within their current major. Dependabot proposes the bumps.
- The Windows build runs `uv sync --locked --group windows`, so pyinstaller is in `uv.lock`.

## Consequences
- A tag must be exactly `v` plus the normalised version (`v3.0.0b1`, not `v3.0.0-beta1`).
- If PyPI fails after the GitHub release exists, re-running the job updates that release and retries the upload.
- **Alternatives rejected.**
  - PyPI Trusted Publishing: parked until the PyPI side is set up. It then needs `id-token` (already granted) and no `UV_PUBLISH_TOKEN`.
  - Bumping the actions to their latest majors: version updates were out of scope for the audit.
  - Authenticode signing of the exes: costs a certificate; the attestation and checksums cover integrity for now.
