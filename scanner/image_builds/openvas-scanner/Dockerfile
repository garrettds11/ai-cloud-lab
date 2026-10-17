FROM registry.community.greenbone.net/community/openvas-scanner@sha256:030fed8566ccca3bc1d5ff3a22adf3d3ad2904def71f0ca58039d1e4c286e14a
USER root
COPY remove-headers.py /tmp/remove-headers.py
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade --with-new-pkgs \
    && python3 /tmp/remove-headers.py \
    && cp "$(command -v openvas)" /tmp/openvas-check \
    && cmp "$(command -v openvas)" /tmp/openvas-check \
    && /tmp/openvas-check --version \
    && rm /tmp/remove-headers.py /tmp/openvas-check \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# Preserve configure-openvas, OpenVAS and Notus service commands from Compose.
# Temporary byte-identical copies omit file capabilities for build-only checks.
# Keep the original scanner binaries and their upstream capabilities intact.
