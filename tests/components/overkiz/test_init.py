"""Tests for Overkiz integration init."""

from unittest.mock import AsyncMock, Mock, patch

from pyoverkiz.enums import UIClass

from homeassistant.components.overkiz.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from .test_config_flow import (
    TEST_EMAIL,
    TEST_GATEWAY_ID,
    TEST_HOST,
    TEST_PASSWORD,
    TEST_SERVER,
    TEST_TOKEN,
)

from tests.common import MockConfigEntry, RegistryEntryWithDefaults, mock_registry

ENTITY_SENSOR_DISCRETE_RSSI_LEVEL = "sensor.zipscreen_woonkamer_discrete_rssi_level"
ENTITY_ALARM_CONTROL_PANEL = "alarm_control_panel.alarm"
ENTITY_SWITCH_GARAGE = "switch.garage"
ENTITY_SENSOR_TARGET_CLOSURE_STATE = "sensor.zipscreen_woonkamer_target_closure_state"
ENTITY_SENSOR_TARGET_CLOSURE_STATE_2 = (
    "sensor.zipscreen_woonkamer_target_closure_state_2"
)


async def test_unique_id_migration(hass: HomeAssistant) -> None:
    """Test migration of sensor unique IDs."""

    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_GATEWAY_ID,
        data={"username": TEST_EMAIL, "password": TEST_PASSWORD, "hub": TEST_SERVER},
    )

    mock_entry.add_to_hass(hass)

    mock_registry(
        hass,
        {
            # This entity will be migrated to "io://1234-5678-1234/3541212-core:DiscreteRSSILevelState"
            ENTITY_SENSOR_DISCRETE_RSSI_LEVEL: RegistryEntryWithDefaults(
                entity_id=ENTITY_SENSOR_DISCRETE_RSSI_LEVEL,
                unique_id="io://1234-5678-1234/3541212-OverkizState.CORE_DISCRETE_RSSI_LEVEL",
                platform=DOMAIN,
                config_entry_id=mock_entry.entry_id,
            ),
            # This entity will be migrated to "internal://1234-5678-1234/alarm/0-TSKAlarmController"
            ENTITY_ALARM_CONTROL_PANEL: RegistryEntryWithDefaults(
                entity_id=ENTITY_ALARM_CONTROL_PANEL,
                unique_id="internal://1234-5678-1234/alarm/0-UIWidget.TSKALARM_CONTROLLER",
                platform=DOMAIN,
                config_entry_id=mock_entry.entry_id,
            ),
            # This entity will be migrated to "io://1234-5678-1234/0-OnOff"
            ENTITY_SWITCH_GARAGE: RegistryEntryWithDefaults(
                entity_id=ENTITY_SWITCH_GARAGE,
                unique_id="io://1234-5678-1234/0-UIClass.ON_OFF",
                platform=DOMAIN,
                config_entry_id=mock_entry.entry_id,
            ),
            # This entity will be removed since "io://1234-5678-1234/3541212-core:TargetClosureState" already exists
            ENTITY_SENSOR_TARGET_CLOSURE_STATE: RegistryEntryWithDefaults(
                entity_id=ENTITY_SENSOR_TARGET_CLOSURE_STATE,
                unique_id="io://1234-5678-1234/3541212-OverkizState.CORE_TARGET_CLOSURE",
                platform=DOMAIN,
                config_entry_id=mock_entry.entry_id,
            ),
            # This entity will not be migrated"
            ENTITY_SENSOR_TARGET_CLOSURE_STATE_2: RegistryEntryWithDefaults(
                entity_id=ENTITY_SENSOR_TARGET_CLOSURE_STATE_2,
                unique_id="io://1234-5678-1234/3541212-core:TargetClosureState",
                platform=DOMAIN,
                config_entry_id=mock_entry.entry_id,
            ),
        },
    )
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)

    unique_id_map = {
        ENTITY_SENSOR_DISCRETE_RSSI_LEVEL: "io://1234-5678-1234/3541212-core:DiscreteRSSILevelState",
        ENTITY_ALARM_CONTROL_PANEL: "internal://1234-5678-1234/alarm/0-TSKAlarmController",
        ENTITY_SWITCH_GARAGE: "io://1234-5678-1234/0-OnOff",
        ENTITY_SENSOR_TARGET_CLOSURE_STATE_2: "io://1234-5678-1234/3541212-core:TargetClosureState",
    }

    # Test if entities will be removed
    assert set(ent_reg.entities.keys()) == set(unique_id_map)

    # Test if unique ids are migrated
    for entity_id, unique_id in unique_id_map.items():
        entry = ent_reg.async_get(entity_id)
        assert entry.unique_id == unique_id


def _create_mock_device(device_url: str, label: str, ui_class: UIClass) -> Mock:
    """Create a mock device for testing."""
    device = Mock()
    device.device_url = device_url
    device.label = label
    device.ui_class = ui_class
    device.widget = None
    device.available = True
    device.states = {}
    device.protocol = Mock()
    device.protocol.value = "io"
    return device


def _create_mock_setup(gateway_id: str, devices: list[Mock]) -> Mock:
    """Create a mock setup response."""
    gateway = Mock()
    gateway.id = gateway_id
    gateway.type = Mock()
    gateway.type.beautify_name = "TaHoma Switch"
    gateway.sub_type = Mock()
    gateway.sub_type.beautify_name = "TaHoma Switch"
    gateway.connectivity = Mock()
    gateway.connectivity.protocol_version = "2023.4.4"

    setup = Mock()
    setup.devices = devices
    setup.gateways = [gateway]
    setup.root_place = None
    return setup


async def test_cloud_removes_local_devices(hass: HomeAssistant) -> None:
    """Test that cloud entry removes devices already managed by local entry."""
    # Create mock devices - some will be in both local and cloud
    local_device_url = f"io://{TEST_GATEWAY_ID}/12345"
    cloud_only_device_url = f"io://{TEST_GATEWAY_ID}/67890"
    shared_device_url = f"io://{TEST_GATEWAY_ID}/11111"

    local_device = _create_mock_device(
        local_device_url, "Local Cover", UIClass.ROLLER_SHUTTER
    )
    shared_device = _create_mock_device(
        shared_device_url, "Shared Cover", UIClass.ROLLER_SHUTTER
    )
    cloud_device = _create_mock_device(
        cloud_only_device_url, "Climate Device", UIClass.HEATING_SYSTEM
    )

    # First, set up the local entry
    local_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_GATEWAY_ID,
        data={
            "host": TEST_HOST,
            "token": TEST_TOKEN,
            "verify_ssl": True,
            "hub": TEST_SERVER,
            "api_type": "local",
        },
    )
    local_entry.add_to_hass(hass)

    local_setup = _create_mock_setup(TEST_GATEWAY_ID, [local_device, shared_device])

    with patch.multiple(
        "pyoverkiz.client.OverkizClient",
        login=AsyncMock(return_value=True),
        get_setup=AsyncMock(return_value=local_setup),
        get_scenarios=AsyncMock(return_value=[]),
        fetch_events=AsyncMock(return_value=[]),
    ):
        await hass.config_entries.async_setup(local_entry.entry_id)
        await hass.async_block_till_done()

    # Verify local entry is loaded with its devices
    assert local_entry.runtime_data is not None
    assert local_device_url in local_entry.runtime_data.coordinator.devices
    assert shared_device_url in local_entry.runtime_data.coordinator.devices

    # Now set up the cloud entry for the same gateway
    cloud_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_GATEWAY_ID,
        data={
            "username": TEST_EMAIL,
            "password": TEST_PASSWORD,
            "hub": TEST_SERVER,
            "api_type": "cloud",
        },
    )
    cloud_entry.add_to_hass(hass)

    # Cloud returns all devices including ones managed by local
    cloud_setup = _create_mock_setup(
        TEST_GATEWAY_ID, [local_device, shared_device, cloud_device]
    )

    with patch.multiple(
        "pyoverkiz.client.OverkizClient",
        login=AsyncMock(return_value=True),
        get_setup=AsyncMock(return_value=cloud_setup),
        get_scenarios=AsyncMock(return_value=[]),
        fetch_events=AsyncMock(return_value=[]),
    ):
        await hass.config_entries.async_setup(cloud_entry.entry_id)
        await hass.async_block_till_done()

    # Verify cloud entry only has the cloud-only device
    assert cloud_entry.runtime_data is not None
    cloud_devices = cloud_entry.runtime_data.coordinator.devices

    # Local devices should be removed from cloud
    assert local_device_url not in cloud_devices
    assert shared_device_url not in cloud_devices
    # Cloud-only device should remain
    assert cloud_only_device_url in cloud_devices


async def test_cloud_keeps_all_devices_without_local(hass: HomeAssistant) -> None:
    """Test that cloud entry keeps all devices when no local entry exists."""
    device_url_1 = f"io://{TEST_GATEWAY_ID}/12345"
    device_url_2 = f"io://{TEST_GATEWAY_ID}/67890"

    device_1 = _create_mock_device(device_url_1, "Cover 1", UIClass.ROLLER_SHUTTER)
    device_2 = _create_mock_device(device_url_2, "Cover 2", UIClass.ROLLER_SHUTTER)

    cloud_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_GATEWAY_ID,
        data={
            "username": TEST_EMAIL,
            "password": TEST_PASSWORD,
            "hub": TEST_SERVER,
            "api_type": "cloud",
        },
    )
    cloud_entry.add_to_hass(hass)

    cloud_setup = _create_mock_setup(TEST_GATEWAY_ID, [device_1, device_2])

    with patch.multiple(
        "pyoverkiz.client.OverkizClient",
        login=AsyncMock(return_value=True),
        get_setup=AsyncMock(return_value=cloud_setup),
        get_scenarios=AsyncMock(return_value=[]),
        fetch_events=AsyncMock(return_value=[]),
    ):
        await hass.config_entries.async_setup(cloud_entry.entry_id)
        await hass.async_block_till_done()

    # Verify all devices are present
    assert cloud_entry.runtime_data is not None
    cloud_devices = cloud_entry.runtime_data.coordinator.devices
    assert device_url_1 in cloud_devices
    assert device_url_2 in cloud_devices


async def test_local_entry_not_affected_by_cloud(hass: HomeAssistant) -> None:
    """Test that local entry is not affected when cloud entry is set up first."""
    shared_device_url = f"io://{TEST_GATEWAY_ID}/12345"

    shared_device = _create_mock_device(
        shared_device_url, "Shared Cover", UIClass.ROLLER_SHUTTER
    )

    # First, set up the cloud entry
    cloud_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_GATEWAY_ID,
        data={
            "username": TEST_EMAIL,
            "password": TEST_PASSWORD,
            "hub": TEST_SERVER,
            "api_type": "cloud",
        },
    )
    cloud_entry.add_to_hass(hass)

    cloud_setup = _create_mock_setup(TEST_GATEWAY_ID, [shared_device])

    with patch.multiple(
        "pyoverkiz.client.OverkizClient",
        login=AsyncMock(return_value=True),
        get_setup=AsyncMock(return_value=cloud_setup),
        get_scenarios=AsyncMock(return_value=[]),
        fetch_events=AsyncMock(return_value=[]),
    ):
        await hass.config_entries.async_setup(cloud_entry.entry_id)
        await hass.async_block_till_done()

    # Cloud entry should have the device since no local entry exists yet
    assert cloud_entry.runtime_data is not None
    assert shared_device_url in cloud_entry.runtime_data.coordinator.devices

    # Now set up the local entry
    local_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_GATEWAY_ID,
        data={
            "host": TEST_HOST,
            "token": TEST_TOKEN,
            "verify_ssl": True,
            "hub": TEST_SERVER,
            "api_type": "local",
        },
    )
    local_entry.add_to_hass(hass)

    local_setup = _create_mock_setup(TEST_GATEWAY_ID, [shared_device])

    with patch.multiple(
        "pyoverkiz.client.OverkizClient",
        login=AsyncMock(return_value=True),
        get_setup=AsyncMock(return_value=local_setup),
        get_scenarios=AsyncMock(return_value=[]),
        fetch_events=AsyncMock(return_value=[]),
    ):
        await hass.config_entries.async_setup(local_entry.entry_id)
        await hass.async_block_till_done()

    # Local entry should have the device
    assert local_entry.runtime_data is not None
    assert shared_device_url in local_entry.runtime_data.coordinator.devices

    # Cloud entry still has the device (it was set up first)
    # Users may want to reload cloud after adding local to trigger self-reparation
    assert shared_device_url in cloud_entry.runtime_data.coordinator.devices


async def test_cloud_removes_device_added_dynamically_to_local(
    hass: HomeAssistant,
) -> None:
    """Test that cloud entry removes a device when it's dynamically added to local."""
    # Start with a device only in cloud
    shared_device_url = f"io://{TEST_GATEWAY_ID}/12345"
    cloud_only_device_url = f"io://{TEST_GATEWAY_ID}/67890"

    shared_device = _create_mock_device(
        shared_device_url, "Shared Cover", UIClass.ROLLER_SHUTTER
    )
    cloud_device = _create_mock_device(
        cloud_only_device_url, "Climate Device", UIClass.HEATING_SYSTEM
    )

    # First, set up the local entry with NO devices initially
    local_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_GATEWAY_ID,
        data={
            "host": TEST_HOST,
            "token": TEST_TOKEN,
            "verify_ssl": True,
            "hub": TEST_SERVER,
            "api_type": "local",
        },
    )
    local_entry.add_to_hass(hass)

    local_setup = _create_mock_setup(TEST_GATEWAY_ID, [])  # No devices initially

    with patch.multiple(
        "pyoverkiz.client.OverkizClient",
        login=AsyncMock(return_value=True),
        get_setup=AsyncMock(return_value=local_setup),
        get_scenarios=AsyncMock(return_value=[]),
        fetch_events=AsyncMock(return_value=[]),
    ):
        await hass.config_entries.async_setup(local_entry.entry_id)
        await hass.async_block_till_done()

    # Verify local entry is loaded with no devices
    assert local_entry.runtime_data is not None
    assert len(local_entry.runtime_data.coordinator.devices) == 0

    # Now set up the cloud entry with both devices
    cloud_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_GATEWAY_ID,
        data={
            "username": TEST_EMAIL,
            "password": TEST_PASSWORD,
            "hub": TEST_SERVER,
            "api_type": "cloud",
        },
    )
    cloud_entry.add_to_hass(hass)

    cloud_setup = _create_mock_setup(TEST_GATEWAY_ID, [shared_device, cloud_device])

    with patch.multiple(
        "pyoverkiz.client.OverkizClient",
        login=AsyncMock(return_value=True),
        get_setup=AsyncMock(return_value=cloud_setup),
        get_scenarios=AsyncMock(return_value=[]),
        fetch_events=AsyncMock(return_value=[]),
    ):
        await hass.config_entries.async_setup(cloud_entry.entry_id)
        await hass.async_block_till_done()

    # Cloud entry should have both devices since local has none
    assert cloud_entry.runtime_data is not None
    assert shared_device_url in cloud_entry.runtime_data.coordinator.devices
    assert cloud_only_device_url in cloud_entry.runtime_data.coordinator.devices

    # Simulate a new device being added to local entry dynamically
    # (this happens when a user connects a new device to their gateway)
    local_entry.runtime_data.coordinator.devices[shared_device_url] = shared_device

    # Trigger a coordinator update on cloud entry
    # This should detect the new local device and remove it from cloud
    with patch.multiple(
        "pyoverkiz.client.OverkizClient",
        fetch_events=AsyncMock(return_value=[]),
    ):
        await cloud_entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()

    # Now cloud should no longer have the shared device
    assert shared_device_url not in cloud_entry.runtime_data.coordinator.devices
    # But should still have the cloud-only device
    assert cloud_only_device_url in cloud_entry.runtime_data.coordinator.devices
