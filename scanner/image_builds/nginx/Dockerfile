FROM registry.community.greenbone.net/community/nginx@sha256:2218c3a09dde107ef999cac1f47e5c39b9a87fd2728fe7db3c930ff354392958
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade --with-new-pkgs \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libcurl4t64)" ge '8.14.1-2+deb13u7' \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' curl)" ge '8.14.1-2+deb13u7' \
    && nginx -v \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# Retain the upstream entrypoint, proxy configuration and worker user.
