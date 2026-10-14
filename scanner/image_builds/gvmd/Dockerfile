FROM registry.community.greenbone.net/community/gvmd@sha256:2d4191418d8998a872954ab30292a18c4eae74ffb8f15aec70d8eb2d16ee9b8c
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade --with-new-pkgs \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libcurl4t64)" ge '8.14.1-2+deb13u7' \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' curl)" ge '8.14.1-2+deb13u7' \
    && gvmd --version \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# Retain the upstream entrypoint, database migration and service-user selection.
