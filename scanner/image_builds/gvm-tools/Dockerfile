FROM registry.community.greenbone.net/community/gvm-tools@sha256:a3ec2b8d2281e1a0bea147f1a6fbe00d22eab26db1c114bebf7bb2beed0a5619
USER root
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade --with-new-pkgs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# Preserve the upstream entrypoint, which creates and selects its runtime user.
