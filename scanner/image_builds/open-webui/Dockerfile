FROM ghcr.io/open-webui/open-webui@sha256:1565038903151ced924b5a3226f73f40f16963df4b910441f6391f10144afdaa
USER root
# Vendor packages only: the lab does not build patched libraries from source (#82).
# Debian's jq security update (CVE-2026-32316, fixed in 1.6-2.1+deb12u3); upgrades only, installs nothing new.
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --only-upgrade --no-install-recommends jq libjq1 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' jq)" ge '1.6-2.1+deb12u3' \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libjq1)" ge '1.6-2.1+deb12u3' \
    && jq --version \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/security-requirements.txt
RUN python3 -m pip install --no-cache-dir --only-binary=:all: -r /tmp/security-requirements.txt \
    && python3 -m pip check \
    && rm /tmp/security-requirements.txt
COPY remove-build-deps.py /opt/security-remove-build-deps.py
RUN python3 /opt/security-remove-build-deps.py \
    && python3 -m pip check \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
COPY smoke.py /opt/security-dependency-smoke.py
COPY chroma-reachability.py /opt/security-chroma-reachability.py
RUN python3 /opt/security-dependency-smoke.py
# Candidate only: production remains on its configured image until promotion.
