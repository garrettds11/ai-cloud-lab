FROM registry.community.greenbone.net/community/pg-gvm@sha256:b81bc5d7a750203ea25c6e4dd086751ecee8e388d1cb2501c60b3805e0056204
USER root
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade --with-new-pkgs \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/ssl/private/ssl-cert-snakeoil.key /etc/ssl/certs/ssl-cert-snakeoil.pem
COPY entrypoint.sh /usr/local/bin/ai-cloud-pg-entrypoint
RUN chmod 0755 /usr/local/bin/ai-cloud-pg-entrypoint
ENTRYPOINT ["/usr/local/bin/ai-cloud-pg-entrypoint"]
CMD ["/usr/local/bin/start-postgresql"]
