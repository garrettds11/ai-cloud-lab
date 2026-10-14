FROM registry.community.greenbone.net/community/openvas-scanner@sha256:525c48bb93e39218d4de360a90d991a2ff735e1fe08f97ef35c5cbc375a08408
USER root
COPY remove-headers.py /tmp/remove-headers.py
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade --with-new-pkgs \
    && python3 /tmp/remove-headers.py \
    && openvas --version \
    && printf 'display("header-removal-parse-check");\n' > /tmp/header-check.nasl \
    && openvas-nasl -p -X /tmp/header-check.nasl \
    && rm /tmp/remove-headers.py /tmp/header-check.nasl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# Preserve configure-openvas, OpenVAS and Notus service commands from Compose.
