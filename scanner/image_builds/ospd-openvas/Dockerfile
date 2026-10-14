FROM registry.community.greenbone.net/community/ospd-openvas@sha256:31a85272abc77521c98451ce6834ed7d2f887e98ff1b26ff3fd94f00d000b169
USER root
COPY remove-headers.py /tmp/remove-headers.py
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade --with-new-pkgs \
    && python3 /tmp/remove-headers.py \
    && cp "$(command -v openvas)" /tmp/openvas-check \
    && cmp "$(command -v openvas)" /tmp/openvas-check \
    && /tmp/openvas-check --version && ospd-openvas --version \
    && cp "$(command -v openvas-nasl)" /tmp/nasl-check \
    && cmp "$(command -v openvas-nasl)" /tmp/nasl-check \
    && printf 'display("header-removal-parse-check");\n' > /tmp/header-check.nasl \
    && /tmp/nasl-check -p -X /tmp/header-check.nasl \
    && rm /tmp/remove-headers.py /tmp/header-check.nasl /tmp/openvas-check /tmp/nasl-check \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# Inherit scanner startup and its runtime privilege handling unchanged.
# Temporary byte-identical copies omit file capabilities for build-only checks.
# Keep the original scanner binaries and their upstream capabilities intact.
