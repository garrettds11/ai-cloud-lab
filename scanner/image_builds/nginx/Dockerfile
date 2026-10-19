FROM registry.community.greenbone.net/community/nginx@sha256:ff0b45ddfd82375397105ef100f3b2d80b88cd4c7da8601e71b45f006b68c45b
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade --with-new-pkgs \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libcurl4t64)" ge '8.14.1-2+deb13u7' \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' curl)" ge '8.14.1-2+deb13u7' \
    && nginx -v \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# Retain the upstream entrypoint, proxy configuration and worker user.
