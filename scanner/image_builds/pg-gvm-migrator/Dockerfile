FROM registry.community.greenbone.net/community/pg-gvm-migrator@sha256:201da953f12dd26c84db8aa0117465de0aac9916f4e1d2d60b4fcb6f86176d4e
USER root
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade --with-new-pkgs \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/ssl/private/ssl-cert-snakeoil.key /etc/ssl/certs/ssl-cert-snakeoil.pem
COPY entrypoint.sh /usr/local/bin/ai-cloud-migrator-entrypoint
RUN chmod 0755 /usr/local/bin/ai-cloud-migrator-entrypoint
ENTRYPOINT ["/usr/local/bin/ai-cloud-migrator-entrypoint"]
CMD ["/usr/local/bin/upgrade.sh"]
