#!/usr/bin/env bash
# Only for disposable Ubuntu noble CI runners and rehearsal images.
set -euo pipefail
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_CODENAME" == noble ]]
[[ $# -gt 0 ]]
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
chmod 0755 "$scratch"
sources="$scratch/ubuntu.sources"
lists="$scratch/lists"
original=/etc/apt/sources.list.d/ubuntu.sources
options=(-o "Dir::Etc::sourcelist=$sources" -o Dir::Etc::sourceparts=-
         -o "Dir::State::Lists=$lists" -o Acquire::Retries=1
         -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20)
for attempt in 1 2 3; do
    rm -rf "$lists"
    mkdir -p "$lists/partial"
    mirror=azure.archive.ubuntu.com
    if [[ "$attempt" == 2 ]]; then mirror=archive.ubuntu.com; fi
    sed -E "s@^URIs: .*@URIs: http://$mirror/ubuntu/@" "$original" > "$sources"
    if timeout 180 apt-get "${options[@]}" -o APT::Update::Error-Mode=any update -qq &&
        DEBIAN_FRONTEND=noninteractive timeout 300 apt-get "${options[@]}" install -y -qq --no-install-recommends "$@"; then
        exit 0
    fi
    echo "Ubuntu package attempt $attempt/3 failed; discarding indexes" >&2
    if [[ "$attempt" -lt 3 ]]; then sleep "$((attempt * 5))"; fi
done
exit 1
