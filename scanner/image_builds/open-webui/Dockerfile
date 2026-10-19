FROM ghcr.io/open-webui/open-webui@sha256:a537bbe1e6b428820f52ed4a029d7fff97062e24d5cf32c365fba36fd0542def
USER root
# Vendor packages only: the lab does not build patched libraries from source (#82).
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
