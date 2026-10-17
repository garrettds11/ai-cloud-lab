FROM registry.community.greenbone.net/community/redis-server@sha256:6da6a7c7221aa3ab231961fd6ca001d552b6a2c7727ac6201c6d8a360baa963a
USER root
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade --with-new-pkgs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# Restore the upstream service user; inherit its entrypoint, command and volumes.
USER redis
