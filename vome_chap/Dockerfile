# Same base as the Vome add-on: base-python ships the interpreter, and the
# worker is standard library only, so nothing is installed at build time.
ARG BUILD_FROM=ghcr.io/home-assistant/base-python:3.13-alpine3.22
FROM ${BUILD_FROM}

COPY rootfs /
COPY chap_sync.py /usr/share/vome_chap/chap_sync.py

RUN chmod +x /etc/services.d/chap-sync/run /usr/share/vome_chap/chap_sync.py

LABEL \
	org.opencontainers.image.title="Vome CHAP" \
	org.opencontainers.image.description="Keeps a Vome CHAP standby in step" \
	org.opencontainers.image.source="https://github.com/Vortitron/VomeSync" \
	org.opencontainers.image.licenses="MIT"
