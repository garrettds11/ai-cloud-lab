#!/bin/sh
set -eu
# Existing clusters may reference Debian's snakeoil TLS paths during recovery.
# Keep those paths usable with a unique runtime key, never a shared image key.
if [ ! -s /etc/ssl/private/ssl-cert-snakeoil.key ] || [ ! -s /etc/ssl/certs/ssl-cert-snakeoil.pem ]; then
    umask 077
    openssl req -x509 -newkey rsa:3072 -nodes -days 365 \
        -subj /CN=greenbone-postgresql-migrator \
        -keyout /etc/ssl/private/ssl-cert-snakeoil.key \
        -out /etc/ssl/certs/ssl-cert-snakeoil.pem >/dev/null 2>&1
    chown root:ssl-cert /etc/ssl/private/ssl-cert-snakeoil.key
    chmod 0640 /etc/ssl/private/ssl-cert-snakeoil.key
    chmod 0644 /etc/ssl/certs/ssl-cert-snakeoil.pem
fi
exec /usr/bin/tini -- "$@"
