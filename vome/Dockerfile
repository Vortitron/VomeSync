# https://developers.home-assistant.io/docs/add-ons/configuration#add-on-dockerfile
#
# Supervisor 2026.04+ no longer injects BUILD_FROM unless build.yaml is still
# present (and that path is going away). A non-empty default keeps local and
# Store builds working on old and new Supervisors without --build-arg.
#
# base-python already ships an interpreter. Do not apk-add python3: Alpine 3.20
# (the previous pin) reached EOL in April 2026, and apk against that index is
# what broke Store installs on machines that were not pre-imaged with Vome.
ARG BUILD_FROM=ghcr.io/home-assistant/base-python:3.13-alpine3.22
FROM ${BUILD_FROM}

# custom_components/vomesync is synced from the repo root by build.sh / CI and
# must be present in git — Supervisor builds with this folder as the context.
COPY rootfs /
COPY config.yaml /usr/share/vome/config.yaml
COPY custom_components/vomesync/ /usr/share/vome/custom_components/vomesync/
COPY panel/ /usr/share/vome/panel/

# Scripts are committed executable; chmod here is a local safety net and needs
# no network (unlike apk).
RUN chmod +x \
	/etc/cont-init.d/*.sh \
	/etc/services.d/vome/run \
	/usr/share/vome/panel/server.py

LABEL \
	org.opencontainers.image.title="Vome" \
	org.opencontainers.image.description="Share virtual switches and remote access for Home Assistant" \
	org.opencontainers.image.source="https://github.com/Vortitron/VomeSync" \
	org.opencontainers.image.licenses="MIT"
