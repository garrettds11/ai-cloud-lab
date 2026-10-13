FROM registry.community.greenbone.net/community/ospd-openvas@sha256:31a85272abc77521c98451ce6834ed7d2f887e98ff1b26ff3fd94f00d000b169
USER root
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade --with-new-pkgs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# Inherit scanner startup and its runtime privilege handling unchanged.
