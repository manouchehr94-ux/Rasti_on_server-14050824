"""Bind dependency downloads to the successful producer's immutable ID and digest.

Downstream reruns must use job outputs, never reconstruct a name from run_attempt.
The download action subsequently verifies the archive digest; pip still verifies
each wheel against the shared hashed lock, without dependency resolution.
"""
import json
import os
import re
import urllib.request


def validate_artifact(metadata, *, artifact_id, digest, run_id):
    if not re.fullmatch(r"[1-9][0-9]*", artifact_id or ""):
        raise ValueError("missing or invalid dependency artifact ID from producer")
    if not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", digest or ""):
        raise ValueError("missing or invalid dependency artifact digest from producer")
    digest = "sha256:" + digest.removeprefix("sha256:")
    if metadata.get("id") != int(artifact_id):
        raise ValueError("dependency artifact ID differs from producer output")
    if metadata.get("workflow_run", {}).get("id") != int(run_id):
        raise ValueError("dependency artifact belongs to another workflow run")
    if metadata.get("expired") is not False:
        raise ValueError("dependency artifact is expired or missing expiry status")
    if metadata.get("digest") != digest:
        raise ValueError("dependency artifact SHA256 differs from producer output")


def main():
    artifact_id = os.environ.get("DEPENDENCY_ARTIFACT_ID", "")
    digest = os.environ.get("DEPENDENCY_ARTIFACT_DIGEST", "")
    # Validate inputs before using the ID in a URL; metadata is checked below.
    if not re.fullmatch(r"[1-9][0-9]*", artifact_id):
        raise ValueError("missing or invalid dependency artifact ID from producer")
    api = os.environ["GITHUB_API_URL"].rstrip("/")
    repo = os.environ["GITHUB_REPOSITORY"]
    request = urllib.request.Request(
        f"{api}/repos/{repo}/actions/artifacts/{artifact_id}",
        headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        metadata = json.load(response)
    validate_artifact(metadata, artifact_id=artifact_id, digest=digest,
                      run_id=os.environ["GITHUB_RUN_ID"])
    print(f"DEPENDENCY_ARTIFACT_VERIFIED id={artifact_id} digest={digest}")


if __name__ == "__main__":
    main()
