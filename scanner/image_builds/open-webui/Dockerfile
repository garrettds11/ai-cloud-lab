FROM ghcr.io/open-webui/open-webui@sha256:1565038903151ced924b5a3226f73f40f16963df4b910441f6391f10144afdaa
USER root
COPY requirements.txt /tmp/security-requirements.txt
RUN python3 -m pip install --no-cache-dir --only-binary=:all: -r /tmp/security-requirements.txt \
    && python3 -m pip check \
    && rm /tmp/security-requirements.txt
COPY smoke.py /opt/security-dependency-smoke.py
RUN python3 /opt/security-dependency-smoke.py
# Candidate only: production remains on its configured image until promotion.
