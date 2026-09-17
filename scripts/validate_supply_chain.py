from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
IMMUTABLE_REF = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
USES = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)(?:\s+#.*)?$")


def action_references(source: str) -> list[tuple[int, str]]:
    references: list[tuple[int, str]] = []
    for line_number, line in enumerate(source.splitlines(), start=1):
        match = USES.match(line)
        if match:
            references.append((line_number, match.group(1)))
    return references


def main() -> int:
    failures: list[str] = []
    workflow_files = sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])

    for workflow in workflow_files:
        source = workflow.read_text(encoding="utf-8")
        for line_number, reference in action_references(source):
            if reference.startswith("./") or reference.startswith("docker://"):
                continue
            action, separator, ref = reference.rpartition("@")
            if not separator or not action or not IMMUTABLE_REF.fullmatch(ref):
                failures.append(
                    f"{workflow.name}:{line_number} action must use a full 40-character "
                    f"commit SHA: {reference}"
                )

    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    for control, marker in [
        ("known-vulnerability dependency gate", "pip-audit"),
        ("supply-chain policy validation", "validate_supply_chain.py"),
        ("deterministic image identity", "universal-ai-knowledge-graph:${{ github.sha }}"),
        ("High/Critical container scan", "aquasecurity/trivy-action@"),
    ]:
        if marker not in ci:
            failures.append(f"ci.yml is missing required {control} marker: {marker}")

    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    for control, marker in [
        ("dependency vulnerability gate", "pip-audit"),
        ("container vulnerability gate", "aquasecurity/trivy-action@"),
        ("SBOM generation", "anchore/sbom-action@"),
        ("GitHub provenance/SBOM attestation", "actions/attest@"),
        ("keyless Sigstore signing", "sigstore/cosign-installer@"),
        ("release checksums", "SHA256SUMS"),
        ("Sigstore bundles", ".sigstore"),
    ]:
        if marker not in release:
            failures.append(f"release.yml is missing required {control} marker: {marker}")

    if failures:
        print("Software supply-chain policy validation failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(
        "Software supply-chain policy validated: "
        f"{len(workflow_files)} workflows use immutable action refs and required "
        "CI/release controls are present."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
