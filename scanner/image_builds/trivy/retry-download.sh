#!/bin/sh
# Bound retries to dependency downloads. Verification and compilation never retry.
attempt=1
while :; do
    if "$@"; then
        exit 0
    else
        result=$?
    fi
    if [ "$attempt" -ge 3 ]; then
        exit "$result"
    fi
    echo "Dependency download failed; retry $attempt of 2 in 5 seconds" >&2
    sleep 5
    attempt=$((attempt + 1))
done
