# https://developers.home-assistant.io/docs/add-ons/configuration#add-on-dockerfile
ARG BUILD_FROM
FROM $BUILD_FROM

# custom_components/vomesync is synced from the repo root by build.sh / CI and
# must be present in git — Supervisor builds with this folder as the context.
COPY rootfs /
COPY custom_components/vomesync/ /usr/share/vome/custom_components/vomesync/
COPY panel/ /usr/share/vome/panel/

RUN chmod +x \
	/etc/cont-init.d/*.sh \
	/etc/services.d/vome/run \
	/usr/share/vome/panel/server.py
