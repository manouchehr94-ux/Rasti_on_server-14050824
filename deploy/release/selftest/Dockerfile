FROM ubuntu:24.04
ARG DEBIAN_FRONTEND=noninteractive
# A mirror can replace a package between index fetch and install. Retry the
# pair with fresh indexes; retrying the stale package URL alone cannot fix it.
RUN set -eu; \
    sources=/etc/apt/sources.list.d/ubuntu.sources; \
    cp "$sources" /tmp/ubuntu-original.sources; \
    for attempt in 1 2 3; do \
      if [ "$attempt" -eq 2 ]; then \
        cp /tmp/ubuntu-original.sources "$sources"; \
      else \
        sed 's@http://\(archive\|security\)\.ubuntu\.com/ubuntu@http://azure.archive.ubuntu.com/ubuntu@g' \
          /tmp/ubuntu-original.sources > "$sources"; \
      fi; \
      rm -rf /var/lib/apt/lists/*; \
      if timeout 180 apt-get -o APT::Update::Error-Mode=any -o Acquire::Retries=1 -o Acquire::http::Timeout=20 update -qq \
         && timeout 300 apt-get -o Acquire::Retries=1 -o Acquire::http::Timeout=20 install -y --no-install-recommends \
            python3 python3-venv python3-pip openssh-server openssh-client \
            postgresql-16 postgresql-client-16 redis-server restic sudo ca-certificates git curl nginx procps; then \
        rm -rf /var/lib/apt/lists/* /tmp/ubuntu-original.sources; exit 0; \
      fi; \
      echo "apt setup attempt ${attempt}/3 failed; discarding stale indexes" >&2; \
      if [ "$attempt" -lt 3 ]; then sleep "$((attempt * 5))"; fi; \
    done; exit 1
RUN python3 -m venv /opt/test-venv
ENV PATH=/opt/test-venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1
