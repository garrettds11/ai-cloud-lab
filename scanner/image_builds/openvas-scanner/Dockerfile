FROM registry.community.greenbone.net/community/openvas-scanner@sha256:525c48bb93e39218d4de360a90d991a2ff735e1fe08f97ef35c5cbc375a08408
USER root
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade --with-new-pkgs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# Preserve configure-openvas, OpenVAS and Notus service commands from Compose.
