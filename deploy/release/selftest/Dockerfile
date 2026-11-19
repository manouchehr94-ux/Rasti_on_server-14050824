FROM ubuntu:24.04
COPY install-ubuntu-test-packages.sh /tmp/install-ubuntu-test-packages.sh
RUN bash /tmp/install-ubuntu-test-packages.sh \
      python3 python3-venv python3-pip openssh-server openssh-client \
      postgresql-16 postgresql-client-16 redis-server restic sudo ca-certificates git curl nginx procps \
    && rm /tmp/install-ubuntu-test-packages.sh \
    && python3 -m venv /opt/test-venv
ENV PATH=/opt/test-venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1
