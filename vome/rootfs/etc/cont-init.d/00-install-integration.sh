#!/command/with-contenv bashio
# Install / refresh the Vome custom component from the image into HA config.
# Same Python as HACS — this path just keeps Supervisor users up to date
# without requiring HACS, and leaves a marker for add-on-only companions.
set -e

SOURCE=/usr/share/vome/custom_components/vomesync

# Where Home Assistant's config is mounted depends on the map type:
# `homeassistant_config` (modern) -> /homeassistant, legacy `config` -> /config.
if [[ -d /homeassistant ]]; then
	CONFIG_ROOT=/homeassistant
elif [[ -d /config ]]; then
	CONFIG_ROOT=/config
else
	bashio::log.error "No Home Assistant config mount found (/homeassistant or /config)."
	bashio::log.error "The integration CANNOT be installed — check the add-on's 'map' config."
	exit 1
fi

TARGET="${CONFIG_ROOT}/custom_components/vomesync"

if [[ ! -d "$SOURCE" ]]; then
	bashio::log.error "Bundled integration missing at ${SOURCE}"
	exit 1
fi

mkdir -p "${CONFIG_ROOT}/custom_components"
rm -rf "${TARGET}"
mkdir -p "${TARGET}"
cp -a "${SOURCE}/." "${TARGET}/"

if [[ ! -f "${TARGET}/manifest.json" ]]; then
	bashio::log.error "Copy to ${TARGET} did not stick — integration NOT installed."
	exit 1
fi

mkdir -p "${CONFIG_ROOT}/vome"
echo "1" > "${CONFIG_ROOT}/vome/addon.marker"

VERSION=$(python3 -c "import json;print(json.load(open('${TARGET}/manifest.json'))['version'])" 2>/dev/null || echo "unknown")
bashio::log.info "Vome integration ${VERSION} installed at ${TARGET} (config root: ${CONFIG_ROOT})"
bashio::log.info "Home Assistant loads integration code at startup — restart HA if this was an update."
