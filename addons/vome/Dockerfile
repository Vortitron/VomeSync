# https://developers.home-assistant.io/docs/add-ons/configuration#add-on-dockerfile
ARG BUILD_FROM
FROM $BUILD_FROM

# Integration sources are staged next to this Dockerfile by build.sh
# (copied from ../../custom_components/vomesync — single source of truth).
COPY rootfs /
COPY staged_integration/ /usr/share/vome/custom_components/vomesync/

RUN chmod +x /etc/cont-init.d/*.sh /etc/services.d/vome/run
