#!/command/with-contenv bashio
# Install / refresh the Vome custom component from the image into HA config.
# Same Python as HACS — this path just keeps Supervisor users up to date
# without requiring HACS, and leaves a marker for add-on-only companions.
set -e

SOURCE=/usr/share/vome/custom_components/vomesync
TARGET=/config/custom_components/vomesync

if [[ ! -d "$SOURCE" ]]; then
	bashio::log.error "Bundled integration missing at ${SOURCE}"
	exit 1
fi

mkdir -p /config/custom_components
rm -rf "${TARGET}"
mkdir -p "${TARGET}"
cp -a "${SOURCE}/." "${TARGET}/"

mkdir -p /config/vome
echo "1" > /config/vome/addon.marker

bashio::log.info "Vome integration installed at ${TARGET}"
bashio::log.info "Restart Home Assistant (or reload the integration) if this was an update."
