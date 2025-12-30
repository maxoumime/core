"""The Overkiz (by Somfy) integration."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from aiohttp import ClientError
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.enums import APIType, OverkizState, UIClass, UIWidget
from pyoverkiz.exceptions import (
    BadCredentialsException,
    MaintenanceException,
    NotAuthenticatedException,
    NotSuchTokenException,
    TooManyRequestsException,
)
from pyoverkiz.models import Device, OverkizServer, Scenario
from pyoverkiz.utils import generate_local_server

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import (
    CONF_API_TYPE,
    CONF_HUB,
    DOMAIN,
    LOGGER,
    OVERKIZ_DEVICE_TO_PLATFORM,
    PLATFORMS,
    UPDATE_INTERVAL_ALL_ASSUMED_STATE,
    UPDATE_INTERVAL_LOCAL,
)
from .coordinator import OverkizDataUpdateCoordinator


@dataclass
class HomeAssistantOverkizData:
    """Overkiz data stored in the runtime data object."""

    coordinator: OverkizDataUpdateCoordinator
    platforms: defaultdict[Platform, list[Device]]
    scenarios: list[Scenario]


type OverkizDataConfigEntry = ConfigEntry[HomeAssistantOverkizData]


def _get_local_devices_for_gateway(
    hass: HomeAssistant, gateway_id: str, exclude_entry_id: str
) -> set[str]:
    """Get device URLs managed by a local entry for the same gateway.

    When both cloud and local entries exist for the same gateway, this returns
    the device URLs that are already managed by the local entry so they can be
    excluded from the cloud entry.
    """
    local_device_urls: set[str] = set()

    for config_entry in hass.config_entries.async_entries(DOMAIN):
        # Skip the current entry being set up
        if config_entry.entry_id == exclude_entry_id:
            continue

        # Only consider loaded local entries for the same gateway
        if (
            config_entry.state is ConfigEntryState.LOADED
            and config_entry.data.get(CONF_API_TYPE) == APIType.LOCAL
            and config_entry.unique_id == gateway_id
        ):
            # Get device URLs from the local entry's coordinator
            runtime_data = config_entry.runtime_data
            if runtime_data and hasattr(runtime_data, "coordinator"):
                local_device_urls.update(runtime_data.coordinator.devices.keys())

    return local_device_urls


async def async_setup_entry(hass: HomeAssistant, entry: OverkizDataConfigEntry) -> bool:
    """Set up Overkiz from a config entry."""
    client: OverkizClient | None = None
    api_type = entry.data.get(CONF_API_TYPE, APIType.CLOUD)

    # Local API
    if api_type == APIType.LOCAL:
        client = create_local_client(
            hass,
            host=entry.data[CONF_HOST],
            token=entry.data[CONF_TOKEN],
            verify_ssl=entry.data[CONF_VERIFY_SSL],
        )

    # Overkiz Cloud API
    else:
        client = create_cloud_client(
            hass,
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
            server=SUPPORTED_SERVERS[entry.data[CONF_HUB]],
        )

    await _async_migrate_entries(hass, entry)

    try:
        await client.login()
        setup = await client.get_setup()

        # Local API does expose scenarios, but they are not functional.
        # Tracked in https://github.com/Somfy-Developer/Somfy-TaHoma-Developer-Mode/issues/21
        if api_type == APIType.CLOUD:
            scenarios = await client.get_scenarios()
        else:
            scenarios = []
    except (
        BadCredentialsException,
        NotSuchTokenException,
        NotAuthenticatedException,
    ) as exception:
        raise ConfigEntryAuthFailed("Invalid authentication") from exception
    except TooManyRequestsException as exception:
        raise ConfigEntryNotReady("Too many requests, try again later") from exception
    except (TimeoutError, ClientError) as exception:
        raise ConfigEntryNotReady("Failed to connect") from exception
    except MaintenanceException as exception:
        raise ConfigEntryNotReady("Server is down for maintenance") from exception

    coordinator = OverkizDataUpdateCoordinator(
        hass,
        entry,
        LOGGER,
        client=client,
        devices=setup.devices,
        places=setup.root_place,
    )

    await coordinator.async_config_entry_first_refresh()

    # When cloud entry is loaded and a local entry exists for the same gateway,
    # remove devices that are already managed by the local entry to avoid duplicates
    if api_type == APIType.CLOUD and entry.unique_id:
        local_device_urls = _get_local_devices_for_gateway(
            hass, entry.unique_id, entry.entry_id
        )
        if local_device_urls:
            # Remove devices managed by local entry from the coordinator
            for device_url in local_device_urls:
                coordinator.devices.pop(device_url, None)

            LOGGER.debug(
                "Removed %d devices from cloud entry that are managed by local entry: %s",
                len(local_device_urls),
                local_device_urls,
            )

            # Also remove these devices from the device registry if they exist
            device_registry = dr.async_get(hass)
            for device_url in local_device_urls:
                base_device_url = device_url.split("#")[0]
                if device_entry := device_registry.async_get_device(
                    identifiers={(DOMAIN, base_device_url)}
                ):
                    # Only remove if this device is linked to the cloud entry
                    if entry.entry_id in device_entry.config_entries:
                        device_registry.async_update_device(
                            device_entry.id,
                            remove_config_entry_id=entry.entry_id,
                        )

    if coordinator.is_stateless:
        LOGGER.debug(
            "All devices have an assumed state. Update interval has been reduced to: %s",
            UPDATE_INTERVAL_ALL_ASSUMED_STATE,
        )
        coordinator.set_update_interval(UPDATE_INTERVAL_ALL_ASSUMED_STATE)

    if api_type == APIType.LOCAL:
        LOGGER.debug(
            "Devices connect via Local API. Update interval has been reduced to: %s",
            UPDATE_INTERVAL_LOCAL,
        )
        coordinator.set_update_interval(UPDATE_INTERVAL_LOCAL)

    platforms: defaultdict[Platform, list[Device]] = defaultdict(list)

    entry.runtime_data = HomeAssistantOverkizData(
        coordinator=coordinator, platforms=platforms, scenarios=scenarios
    )

    # Map Overkiz entities to Home Assistant platform
    for device in coordinator.data.values():
        LOGGER.debug(
            (
                "The following device has been retrieved. Report an issue if not"
                " supported correctly (%s)"
            ),
            device,
        )

        if platform := OVERKIZ_DEVICE_TO_PLATFORM.get(
            device.widget
        ) or OVERKIZ_DEVICE_TO_PLATFORM.get(device.ui_class):
            platforms[platform].append(device)

    device_registry = dr.async_get(hass)

    for gateway in setup.gateways:
        LOGGER.debug("Added gateway (%s)", gateway)

        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, gateway.id)},
            model=gateway.sub_type.beautify_name if gateway.sub_type else None,
            manufacturer=client.server.manufacturer,
            name=gateway.type.beautify_name if gateway.type else gateway.id,
            sw_version=gateway.connectivity.protocol_version,
            configuration_url=client.server.configuration_url,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: OverkizDataConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_migrate_entries(
    hass: HomeAssistant, config_entry: OverkizDataConfigEntry
) -> bool:
    """Migrate old entries to new unique IDs."""
    entity_registry = er.async_get(hass)

    @callback
    def update_unique_id(entry: er.RegistryEntry) -> dict[str, str] | None:
        # Python 3.11 treats (str, Enum) and StrEnum in a different way
        # Since pyOverkiz switched to StrEnum, we need to rewrite the unique ids once to the new style
        #
        # io://xxxx-xxxx-xxxx/3541212-OverkizState.CORE_DISCRETE_RSSI_LEVEL -> io://xxxx-xxxx-xxxx/3541212-core:DiscreteRSSILevelState
        # internal://xxxx-xxxx-xxxx/alarm/0-UIWidget.TSKALARM_CONTROLLER -> internal://xxxx-xxxx-xxxx/alarm/0-TSKAlarmController
        # io://xxxx-xxxx-xxxx/xxxxxxx-UIClass.ON_OFF -> io://xxxx-xxxx-xxxx/xxxxxxx-OnOff
        if (key := entry.unique_id.split("-")[-1]).startswith(
            ("OverkizState", "UIWidget", "UIClass")
        ):
            state = key.split(".")[1]
            new_key = ""

            if key.startswith("UIClass"):
                new_key = UIClass[state]
            elif key.startswith("UIWidget"):
                new_key = UIWidget[state]
            else:
                new_key = OverkizState[state]

            new_unique_id = entry.unique_id.replace(key, new_key)

            LOGGER.debug(
                "Migrating entity '%s' unique_id from '%s' to '%s'",
                entry.entity_id,
                entry.unique_id,
                new_unique_id,
            )

            if existing_entity_id := entity_registry.async_get_entity_id(
                entry.domain, entry.platform, new_unique_id
            ):
                LOGGER.debug(
                    "Cannot migrate to unique_id '%s', already exists for '%s'. Entity will be removed",
                    new_unique_id,
                    existing_entity_id,
                )
                entity_registry.async_remove(entry.entity_id)

                return None

            return {
                "new_unique_id": new_unique_id,
            }

        return None

    await er.async_migrate_entries(hass, config_entry.entry_id, update_unique_id)

    return True


def create_local_client(
    hass: HomeAssistant, host: str, token: str, verify_ssl: bool
) -> OverkizClient:
    """Create Overkiz local client."""
    session = async_create_clientsession(hass, verify_ssl=verify_ssl)

    return OverkizClient(
        username="",
        password="",
        token=token,
        session=session,
        server=generate_local_server(host=host),
        verify_ssl=verify_ssl,
    )


def create_cloud_client(
    hass: HomeAssistant, username: str, password: str, server: OverkizServer
) -> OverkizClient:
    """Create Overkiz cloud client."""
    # To allow users with multiple accounts/hubs, we create a new session so they have separate cookies
    session = async_create_clientsession(hass)

    return OverkizClient(
        username=username, password=password, session=session, server=server
    )
