#!/bin/sh
set -eu
# The upstream image's shared snakeoil key is deleted during the build.
# Generate private material in the container's writable layer, never the image.
if [ ! -s /etc/ssl/private/ssl-cert-snakeoil.key ] || [ ! -s /etc/ssl/certs/ssl-cert-snakeoil.pem ]; then
    umask 077
    openssl req -x509 -newkey rsa:3072 -nodes -days 365 \
        -subj /CN=greenbone-postgresql \
        -keyout /etc/ssl/private/ssl-cert-snakeoil.key \
        -out /etc/ssl/certs/ssl-cert-snakeoil.pem >/dev/null 2>&1
    chown root:ssl-cert /etc/ssl/private/ssl-cert-snakeoil.key
    chmod 0640 /etc/ssl/private/ssl-cert-snakeoil.key
    chmod 0644 /etc/ssl/certs/ssl-cert-snakeoil.pem
fi
exec /usr/local/bin/entrypoint "$@"
