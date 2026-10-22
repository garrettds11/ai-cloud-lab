FROM ghcr.io/aquasecurity/trivy@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c
# Official Trivy release image. The lab applies only Alpine's own package updates and
# does not rebuild Trivy from source or change its Go toolchain (#113).
USER root
RUN apk upgrade --no-cache
