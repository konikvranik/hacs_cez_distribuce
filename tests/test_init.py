"""HA-style tests for setup / unload of the ČEZ Distribuce integration.

Drives the real ``async_setup_entry`` / ``async_unload_entry`` machinery with
the REST client monkeypatched, so the calendar and sensor platforms actually
get wired up and entities are created.

Entries are added directly to ``hass.config_entries`` (bypassing the user flow)
so the test does not depend on flow-level credential validation, which itself
needs a separate client mock.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from custom_components.cez_distribuce import DOMAIN, CezHub

# Sample portal data used to populate the hub without any network access.
SUPPLY_POINTS_PAYLOAD = {
    "vstelleBlocks": {
        "blocks": [
            {"vstelles": [{"uid": "U1", "ean": "8591824000000011"}]}
        ]
    }
}
DETAIL_PAYLOAD = {
    "ean": "8591824000000011",
    "hdo": True,
    "probihajiciOdstavka": False,
    "stavSmlouvyDistribuce": "AKTIV",
    "stavSmlouvyDistribuceTxt": "Aktivní",
    "anlage_Dist": {
        "tydenOdectu": 23,
        "tydenOdectuFormatted": "23 (01.06.2026 - 07.06.2026)",
        "cislo": "0004381597",
        "sazba": "D57D",
        "distributor": "CEZ-DI",
        "distributorTxt": "ČEZ Distribuce",
        "mesicOdectu": 0,
    },
    "faktaOM": {
        "napetovaHladina": "NN",
        "typMereni": "C",
        "jisticCharakteristika": "B",
        "datumPoslednihoOdectu": "2025-01-01T00:00:00",
        # casoveRezy intentionally unsorted (D57D is newer but listed first).
        "casoveRezy": [
            {"sazbaDistribuce": "D57D", "sazbaDistribuceOd": "2019-05-27T00:00:00",
             "hlavniJistic": 40.0, "pocetFazi": 3.0, "typMereni": "C", "typOdberu": "T"},
            {"sazbaDistribuce": "D25D", "sazbaDistribuceOd": "2008-09-26T00:00:00",
             "hlavniJistic": 25.0, "pocetFazi": 3.0, "typMereni": "C", "typOdberu": "T"},
        ],
    },
}
READINGS_PAYLOAD = [{"datumOdectu": "2025-01-01", "stavVt": 100.0, "stavNt": 50.0, "sernr": "S1"}]
SIGNALS_PAYLOAD = {"signals": [{"datum": "01.01.2025", "casy": "00:00 - 08:00", "nazevSignalu": "NT"}]}
OUTAGES_PAYLOAD = []


@pytest.fixture()
def mock_client():
    """A MagicMock REST client whose data methods return canned payloads.

    Patches ``CezDistribuceRestClient`` in the integration package so both
    CezHub (used during setup) and config_flow (used during validation) get
    the same fake instance.
    """
    client = MagicMock()
    client.login.return_value = None
    client.common_header.return_value = {"user": "test"}
    client.get_supply_points.return_value = SUPPLY_POINTS_PAYLOAD
    client.get_supply_point_detail.return_value = DETAIL_PAYLOAD
    client.get_readings.return_value = READINGS_PAYLOAD
    client.get_signals.return_value = SIGNALS_PAYLOAD
    client.get_outages.return_value = OUTAGES_PAYLOAD
    with patch(
        "custom_components.cez_distribuce.CezDistribuceRestClient",
        return_value=client,
    ), patch(
        "custom_components.cez_distribuce.config_flow.CezDistribuceRestClient",
        return_value=client,
    ):
        yield client


async def _add_loaded_entry(hass: HomeAssistant) -> ConfigEntry:
    """Add a config entry directly and run its setup. Returns the entry."""
    entry = ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="ČEZ Distribuce (test-user)",
        data={CONF_USERNAME: "test-user", CONF_PASSWORD: "test-pass"},
        source="user",
        options={},
        unique_id="test-user",
        subentries_data=(),
        discovery_keys={},
    )
    hass.config_entries._entries[entry.entry_id] = entry
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_entry_loads_and_sets_up_platforms(
    hass: HomeAssistant, enable_custom_integrations, mock_client
):
    """async_setup_entry should load data and create calendar + sensor entities."""
    entry = await _add_loaded_entry(hass)

    assert entry.state is ConfigEntryState.LOADED

    # The hub is stored in hass.data under the entry id
    hub: CezHub = hass.data[DOMAIN][entry.entry_id]
    assert isinstance(hub, CezHub)
    assert hub.points, "static data should have populated hub.points"
    assert hub.ean == "8591824000000011"

    # The client should have been called for login + each data method
    mock_client.login.assert_called_once_with("test-user", "test-pass")
    mock_client.get_supply_points.assert_called_once()
    mock_client.get_supply_point_detail.assert_called_with("U1")
    mock_client.get_readings.assert_called_with("U1")
    mock_client.get_signals.assert_called_with("8591824000000011")
    mock_client.get_outages.assert_called_with(ean="8591824000000011")


async def test_setup_entry_creates_expected_entities(
    hass: HomeAssistant, enable_custom_integrations, mock_client
):
    """All three platforms should expose the right entities.

    Entity IDs derive from the Czech display names, so we assert on substrings
    rather than exact ids to stay robust to HA's slug normalization.

    Per supply point we expect: 2 calendars + 5 sensors + 2 binary sensors.
    """
    await _add_loaded_entry(hass)

    entity_ids = list(hass.states.async_entity_ids())
    # 2 calendars (outage + current signal)
    calendar_ids = [eid for eid in entity_ids if eid.startswith("calendar.")]
    sensor_ids = [eid for eid in entity_ids if eid.startswith("sensor.")]
    binary_ids = [eid for eid in entity_ids if eid.startswith("binary_sensor.")]
    assert any("odstavky" in eid for eid in calendar_ids), calendar_ids
    assert any("hdo" in eid and "signal" in eid for eid in calendar_ids), calendar_ids

    # Reading sensors (VT, NT, total)
    vt_ids = [eid for eid in sensor_ids if "vt" in eid]
    nt_ids = [eid for eid in sensor_ids if "nt" in eid and "celkem" not in eid]
    total_ids = [eid for eid in sensor_ids if "celkem" in eid]
    assert vt_ids and nt_ids and total_ids, sensor_ids

    # New detail-derived sensors. HA slugifies "Týden odečtu" -> tyden_odectu.
    week_ids = [eid for eid in sensor_ids if "odec" in eid]
    tariff_ids = [eid for eid in sensor_ids if "sazba" in eid or "tariff" in eid]
    assert week_ids, sensor_ids
    assert tariff_ids, sensor_ids

    # New binary sensors
    assert any("odstavka" in eid for eid in binary_ids), binary_ids
    assert any("smlouva" in eid for eid in binary_ids), binary_ids

    # Reading sensor numeric values from READINGS_PAYLOAD (VT=100, NT=50 -> 150).
    vt_state = hass.states.get(vt_ids[0])
    nt_state = hass.states.get(nt_ids[0])
    total_state = hass.states.get(total_ids[0])
    assert vt_state is not None and float(vt_state.state) == 100.0
    assert nt_state is not None and float(nt_state.state) == 50.0
    assert total_state is not None and float(total_state.state) == 150.0


async def test_setup_entry_week_reading_sensor_value(
    hass: HomeAssistant, enable_custom_integrations, mock_client
):
    """The week-of-reading sensor reflects anlage_Dist.tydenOdectu."""
    await _add_loaded_entry(hass)
    sensor_ids = [eid for eid in hass.states.async_entity_ids() if eid.startswith("sensor.") and "odec" in eid]
    assert sensor_ids
    state = hass.states.get(sensor_ids[0])
    assert state is not None
    assert int(state.state) == 23  # DETAIL_PAYLOAD.anlage_Dist.tydenOdectu
    attrs = state.attributes
    assert attrs.get("interval") == "23 (01.06.2026 - 07.06.2026)"
    assert attrs.get("sazba") == "D57D"


async def test_setup_entry_tariff_sensor_value(
    hass: HomeAssistant, enable_custom_integrations, mock_client
):
    """The tariff sensor picks the current (newest) tariff slice."""
    await _add_loaded_entry(hass)
    sensor_ids = [
        eid for eid in hass.states.async_entity_ids()
        if eid.startswith("sensor.") and ("sazba" in eid or "tariff" in eid)
    ]
    assert sensor_ids
    state = hass.states.get(sensor_ids[0])
    assert state is not None
    assert state.state == "D57D"  # current tariff (D25D is older)
    # hlavni_jistic comes through as int 40, not float 40.0
    assert state.attributes.get("hlavni_jistic") == 40
    assert state.attributes.get("pocet_fazi") == 3


async def test_setup_entry_outage_binary_sensor_off(
    hass: HomeAssistant, enable_custom_integrations, mock_client
):
    """The outage-in-progress binary sensor is off when no outage is active."""
    await _add_loaded_entry(hass)
    ids = [eid for eid in hass.states.async_entity_ids()
           if eid.startswith("binary_sensor.") and "odstavka" in eid]
    assert ids
    state = hass.states.get(ids[0])
    assert state is not None
    assert state.state == "off"


async def test_setup_entry_contract_binary_sensor_on(
    hass: HomeAssistant, enable_custom_integrations, mock_client
):
    """The contract-active binary sensor is on when stavSmlouvyDistribuce == AKTIV."""
    await _add_loaded_entry(hass)
    ids = [eid for eid in hass.states.async_entity_ids()
           if eid.startswith("binary_sensor.") and "smlouva" in eid]
    assert ids
    state = hass.states.get(ids[0])
    assert state is not None
    assert state.state == "on"
    assert state.attributes.get("stav_text") == "Aktivní"


async def test_unload_entry_removes_hub_data(
    hass: HomeAssistant, enable_custom_integrations, mock_client
):
    """async_unload_entry should clean up the hub from hass.data."""
    entry = await _add_loaded_entry(hass)
    entry_id = entry.entry_id

    ok = await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert ok is True
    assert entry.state is ConfigEntryState.NOT_LOADED
    # The per-entry hub is gone; the domain key may remain (empty) or be removed
    if DOMAIN in hass.data:
        assert entry_id not in hass.data[DOMAIN]


async def test_setup_entry_failure_surfaces_as_setup_error(
    hass: HomeAssistant, enable_custom_integrations, mock_client
):
    """If login fails during setup, the entry ends up in SETUP_RETRY/ERROR."""
    mock_client.login.side_effect = Exception("portal down")

    entry = await _add_loaded_entry(hass)

    # Login blew up inside _login_and_load_static -> async_setup raised
    assert entry.state in (ConfigEntryState.SETUP_RETRY, ConfigEntryState.SETUP_ERROR)


@pytest.mark.parametrize("expected_lingering_timers", [True])  # YAML setup has no unload
async def test_yaml_setup_loads_hub_and_creates_entities(
    hass: HomeAssistant, enable_custom_integrations, mock_client
):
    """Legacy YAML configuration must set up without a config entry (issue #7).

    It used to fail with "Detected code that uses async_config_entry_first_refresh,
    which is only supported for coordinators with a config entry".
    """
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(
        hass, DOMAIN, {DOMAIN: {CONF_USERNAME: "test-user", CONF_PASSWORD: "test-pass"}}
    )
    await hass.async_block_till_done()

    hub: CezHub = hass.data[DOMAIN]["legacy"]
    assert hub.signals_coordinator.data == {"8591824000000011": SIGNALS_PAYLOAD}
    assert any(s.entity_id.startswith("calendar.") for s in hass.states.async_all())
    assert _schedule_state(hass).attributes["signals"] == SIGNALS_PAYLOAD["signals"]


def _schedule_state(hass: HomeAssistant):
    """The "HDO – rozpis" sensor: raw 7-day schedule + time of the last good download."""
    states = [s for s in hass.states.async_all("sensor") if "signals" in s.attributes]
    assert len(states) == 1, "expected exactly one HDO schedule sensor"
    return states[0]


async def test_schedule_sensor_exposes_raw_signals_and_download_time(
    hass: HomeAssistant, enable_custom_integrations, mock_client
):
    await _add_loaded_entry(hass)

    state = _schedule_state(hass)
    assert state.attributes["signals"] == SIGNALS_PAYLOAD["signals"]
    assert state.attributes["device_class"] == "timestamp"
    assert state.state not in ("unknown", "unavailable")


async def test_schedule_sensor_keeps_last_good_schedule_when_refresh_fails(
    hass: HomeAssistant, enable_custom_integrations, mock_client, freezer
):
    entry = await _add_loaded_entry(hass)
    hub: CezHub = hass.data[DOMAIN][entry.entry_id]
    newer = {"signals": [{"datum": "02.01.2025", "casy": "00:00 - 06:00", "nazevSignalu": "NT"}]}

    freezer.tick(3600)
    mock_client.get_signals.return_value = newer
    await hub.signals_coordinator.async_refresh()
    await hass.async_block_till_done()
    good = _schedule_state(hass)
    assert good.attributes["signals"] == newer["signals"]

    freezer.tick(3600)
    mock_client.get_signals.side_effect = Exception("session expired")
    await hub.signals_coordinator.async_refresh()
    await hass.async_block_till_done()
    after_failure = _schedule_state(hass)
    assert after_failure.attributes["signals"] == newer["signals"]
    assert after_failure.state == good.state


async def test_failed_signals_refresh_logs_the_ean(
    hass: HomeAssistant, enable_custom_integrations, mock_client, caplog
):
    """The warning names the supply point instead of '?' (issue #8)."""
    entry = await _add_loaded_entry(hass)
    hub: CezHub = hass.data[DOMAIN][entry.entry_id]

    mock_client.get_signals.side_effect = Exception("session expired")
    await hub.signals_coordinator.async_refresh()
    await hass.async_block_till_done()

    assert "Failed refreshing signals for 8591824000000011: session expired" in caplog.text


async def test_schedule_timestamp_moves_on_success_but_not_on_empty_schedule(
    hass: HomeAssistant, enable_custom_integrations, mock_client, freezer
):
    """An empty schedule from the portal is not a successful download."""
    entry = await _add_loaded_entry(hass)
    hub: CezHub = hass.data[DOMAIN][entry.entry_id]
    first = _schedule_state(hass).state

    freezer.tick(3600)
    await hub.signals_coordinator.async_refresh()
    await hass.async_block_till_done()
    moved = _schedule_state(hass)
    assert moved.state != first

    freezer.tick(3600)
    mock_client.get_signals.return_value = {"signals": []}
    await hub.signals_coordinator.async_refresh()
    await hass.async_block_till_done()
    after_empty = _schedule_state(hass)
    assert after_empty.attributes["signals"] == SIGNALS_PAYLOAD["signals"]
    assert after_empty.state == moved.state


async def test_schedule_timestamp_set_by_startup_load_even_if_first_refresh_fails(
    hass: HomeAssistant, enable_custom_integrations, mock_client
):
    mock_client.get_signals.side_effect = [SIGNALS_PAYLOAD, Exception("portal down")]

    await _add_loaded_entry(hass)

    state = _schedule_state(hass)
    assert state.state not in ("unknown", "unavailable")
    assert state.attributes["signals"] == SIGNALS_PAYLOAD["signals"]
