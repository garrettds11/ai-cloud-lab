FROM registry.community.greenbone.net/community/gsad@sha256:d564fa5d6bd8f98c739be82ba7fb6d6760a9f1ab7827b40c57ac747adcd03428
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade --with-new-pkgs \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libcurl4t64)" ge '8.14.1-2+deb13u7' \
    && gsad --version \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# Retain the upstream entrypoint and its service-user privilege handling.
