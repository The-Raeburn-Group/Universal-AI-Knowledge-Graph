# Software supply-chain policy

## Purpose

Universal AI Knowledge Graph treats workflow integrity, Python dependency provenance, container security and release provenance as production security boundaries. OCR and PostgreSQL recovery functionality must remain verifiable while these controls are enforced.

## Required controls

All third-party GitHub Actions must be pinned to full 40-character commit SHAs. Mutable tags and branches are rejected by `make supply-chain`.

Pull-request and main CI must:

- install the pinned project dependency set;
- execute the supply-chain policy validator;
- fail on known Python dependency vulnerabilities reported by `pip-audit`;
- retain lint, strict typing, tests and package build checks;
- retain the real pgvector migration, workspace/DSAR isolation and manifest-verified backup/restore checks;
- retain the bounded local Poppler/Tesseract OCR test path;
- build the production container under the exact Git commit SHA; and
- fail on High/Critical runtime-image findings.

Version-tag releases must repeat the quality, dependency, package and container gates before publishing a trust package containing:

- Python wheel and source distribution artifacts;
- SHA-256 checksums;
- SPDX and CycloneDX SBOMs;
- keyless Sigstore signature bundles;
- GitHub provenance and SBOM attestations; and
- verification instructions.

## Runtime minimisation

Build-only compiler tooling must not be carried into the production image. The runtime image contains only the installed application environment and required operational/OCR packages, receives current Debian security updates during build, removes package-install tooling that is not required at runtime where practical, and runs as a non-root user.

## Remediation expectations

Known dependency vulnerabilities block CI and release until remediated or covered by an explicit, time-bounded security exception outside the build. High/Critical container findings block CI and release. Gates must not be weakened merely to obtain a green build.

## Verification

Repository checks can be exercised with:

```sh
make install
make supply-chain
make audit
make lint
make typecheck
make test
make build
```

Container scanning is performed against the exact commit-SHA image in CI. Version-tag signing and GitHub attestations use GitHub OIDC and do not require long-lived signing keys in the repository.
