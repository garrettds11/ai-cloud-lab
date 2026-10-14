FROM registry.community.greenbone.net/community/gvm-tools@sha256:53d294ab34b1a7fe3582826560aa8a6dcdfb395e396b195a03dd7d4b3b9cd885
USER root
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade --with-new-pkgs \
    && python3 -c 'from gvm.connections import UnixSocketConnection; from gvm.protocols.gmp import Gmp; import importlib.metadata; print(importlib.metadata.version("gvm-tools"))' \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# Preserve the upstream entrypoint, which creates and selects its runtime user.
