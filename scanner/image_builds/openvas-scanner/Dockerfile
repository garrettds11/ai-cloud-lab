FROM registry.community.greenbone.net/community/openvas-scanner@sha256:b618f328e59f5c8dff400344bf3fc02122364676b3f009e52eec8f2df4c61111
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
