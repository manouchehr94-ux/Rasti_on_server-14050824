#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
here="$PWD"
sha=5732e12cf21703624ee6acb51aa4c81b6ee54900
image=rastisi-local-p0:5732e12
# Never use a remote or ambient Docker endpoint for this laptop-only tool.
if [[ -n "${DOCKER_HOST:-}" ]]; then echo 'Clear DOCKER_HOST and select a local context.' >&2; exit 1; fi
context="$(docker context show)"
endpoint="$(docker context inspect "$context" --format '{{(index .Endpoints "docker").Host}}')"
case "$endpoint" in
    unix://*|npipe://*) ;;
    *) echo 'A local Docker context is required.' >&2; exit 1 ;;
esac
d() { docker --context "$context" "$@"; }
dc() { d compose --env-file "$here/compose.empty.env" --project-name rastisi-local-p0 --file "$here/compose.yml" "$@"; }
case "${1:-}" in
    check)
        d version --format '{{.Server.Os}}/{{.Server.Arch}}'
        d compose version
        git --version
        ;;
    prepare)
        [[ "$(d version --format '{{.Server.Os}}/{{.Server.Arch}}')" = linux/amd64 ]] ||
            { echo 'This verified wheel lock requires Linux AMD64.' >&2; exit 1; }
        git cat-file -e "$sha^{commit}" 2>/dev/null || git fetch origin "$sha"
        mkdir -p .build
        context_dir="$(mktemp -d "$here/.build/context.XXXXXXXX")"
        trap 'rm -rf "$context_dir"' EXIT
        mkdir "$context_dir/source" "$context_dir/wheelhouse"
        git -C "$(git rev-parse --show-toplevel)" archive "$sha" apps shop_core templates static manage.py deploy/release/examples | tar -xf - -C "$context_dir/source"
        cp Dockerfile requirements-linux-amd64.lock smoke.py "$context_dir/"
        offline=0
        if [[ $# -gt 1 ]]; then
            cp -- "$2"/*.whl "$context_dir/wheelhouse/"
            offline=1
        fi
        d build --build-arg "STAGING_OFFLINE=$offline" --label "org.opencontainers.image.revision=$sha" -t "$image" "$context_dir"
        dc pull postgres redis nginx
        mkdir -p .build/source/deploy/release/examples
        cp "$context_dir/source/deploy/release/examples/nginx-availability-http.conf" .build/source/deploy/release/examples/
        ;;
    up)
        [[ "${2:-}" == --approve-local ]] || { echo 'Use up --approve-local after reviewing the local setup.' >&2; exit 2; }
        [[ "$(d image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" == "$sha" ]]
        test -f .build/source/deploy/release/examples/nginx-availability-http.conf
        dc up -d --pull never --wait --wait-timeout 120 postgres redis
        dc run --rm --no-deps initializer
        dc up -d --pull never --wait --wait-timeout 120 app-a app-b nginx
        ;;
    stop) dc down ;;
    status) dc ps -a ;;
    test)
        [[ "$(d network inspect rastisi-local-p0_isolated --format '{{.Internal}}')" == true ]]
        dc exec -T app-b python /usr/local/share/staging-smoke.py smoke
        dc stop app-a
        trap 'dc up -d --pull never --wait app-a' EXIT
        for _ in {1..6}; do dc exec -T app-b python /usr/local/share/staging-smoke.py smoke; done
        echo 'PASS public routes with app-a fully stopped'
        dc up -d --pull never --wait app-a
        trap - EXIT
        result="$(dc exec -T app-a python /usr/local/share/staging-smoke.py worker)"
        printf '%s\n' "$result"
        pid="${result##* }"
        dc logs --no-color --no-log-prefix app-a | grep -F "WORKER TIMEOUT (pid:$pid)"
        dc exec -T app-b python /usr/local/share/staging-smoke.py smoke
        dc logs --no-color --no-log-prefix nginx app-a app-b |
            dc exec -T app-b python /usr/local/share/staging-smoke.py logs
        ;;
    soak)
        dc exec -T app-b python /usr/local/share/staging-smoke.py soak | tee soak.jsonl
        ;;
    *) echo 'Usage: stage.sh check|prepare [wheelhouse]|up --approve-local|status|test|soak|stop'; exit 2 ;;
esac
