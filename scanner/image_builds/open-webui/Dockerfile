FROM ghcr.io/open-webui/open-webui@sha256:1565038903151ced924b5a3226f73f40f16963df4b910441f6391f10144afdaa
USER root
COPY build-sqlite.py /opt/security-build-sqlite.py
COPY sqlite-smoke.py /opt/security-sqlite-smoke.py
# Use the base's existing compiler before removing build dependencies below.
# The installed .deb records the actual upstream version and source checksums.
RUN python3 /opt/security-build-sqlite.py && rm /opt/security-build-sqlite.py
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
RUN python3 /opt/security-sqlite-smoke.py
# Candidate only: production remains on its configured image until promotion.
