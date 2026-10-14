FROM ghcr.io/aquasecurity/trivy@sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa
USER root
# Rebuild the same release; OS upgrades cannot patch a statically linked Go binary.
RUN apk add --no-cache --virtual .trivy-build go curl git \
    && mkdir /trivy-build \
    && curl --fail --location --silent --show-error https://codeload.github.com/aquasecurity/trivy/tar.gz/591e9799316a602e703f0b484f6c6d7b234ec8f3 -o /tmp/trivy-source.tar.gz \
    && echo '4a5bb1c16ccc55368843eb95ee9a25519a0bfef26609a8b4295f2060af43077e  /tmp/trivy-source.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/trivy-source.tar.gz -C /trivy-build --strip-components=1 \
    && cd /trivy-build \
    && GOTOOLCHAIN=go1.27.2 go get golang.org/x/net@v0.60.0 \
    && GOTOOLCHAIN=go1.27.2 go mod verify \
    && CGO_ENABLED=0 GOTOOLCHAIN=go1.27.2 go build -p 2 -trimpath -ldflags '-s -w -X github.com/aquasecurity/trivy/pkg/version/app.ver=0.75.0' -o /usr/local/bin/trivy ./cmd/trivy \
    && mkdir -p /usr/local/share \
    && GOTOOLCHAIN=go1.27.2 go version -m /usr/local/bin/trivy > /usr/local/share/trivy-build.txt \
    && cd / \
    && rm -rf /trivy-build /tmp/trivy-source.tar.gz /root/go /root/.cache/go-build \
    && apk del .trivy-build
