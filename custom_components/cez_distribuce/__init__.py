"""Home Assistant integration for ČEZ Distribuce.

This module wires the integration into Home Assistant, defines configuration
schema (both YAML legacy and Config Entry), and sets up a hub (CezHub) that
coordinates data fetching from the ČEZ Distribuce web portal using a simple
REST client.

Key entities:
- CezHub: central object that logs in once and manages refresh coordinators
  for signals (HDO), outages and meter readings.
- async_setup/async_setup_entry: standard HA entry points for YAML and UI flows.
- async_unload_entry: unloads the integration platforms and cleans up stored data.

The implementation avoids direct network I/O in the event loop by delegating
blocking work to the executor thread pool.
"""
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import voluptuous as vol
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DOMAIN,
    CONF_USERNAME,
    CONF_PASSWORD,
    DEFAULT_SCAN_INTERVAL_OUTAGES_MIN,
    DEFAULT_SCAN_INTERVAL_READINGS_MIN,
    DEFAULT_SCAN_INTERVAL_SIGNALS_MIN,
)
from .rest_client.rest_client import CezDistribuceRestClient
from .statistics import async_import_readings_statistics

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["calendar", "sensor", "binary_sensor"]

DOMAIN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional("scan_interval_signals", default=DEFAULT_SCAN_INTERVAL_SIGNALS_MIN): cv.positive_int,
        vol.Optional("scan_interval_outages", default=DEFAULT_SCAN_INTERVAL_OUTAGES_MIN): cv.positive_int,
        vol.Optional("scan_interval_readings", default=DEFAULT_SCAN_INTERVAL_READINGS_MIN): cv.positive_int,
    }
)

CONFIG_SCHEMA = vol.Schema({DOMAIN: DOMAIN_SCHEMA}, extra=vol.ALLOW_EXTRA)


@dataclass
class CezData:
    header: Dict[str, Any]
    supply_point: Dict[str, Any]
    supply_point_detail: Dict[str, Any]
    readings: List[Dict[str, Any]]
    signals: Dict[str, Any]
    outages_for_ean: List[Dict[str, Any]]


class CezHub:
    def __init__(self, hass: HomeAssistant, username: str, password: str,
                 scan_signals_min: int, scan_outages_min: int, scan_readings_min: int) -> None:
        self.hass = hass
        self._client = CezDistribuceRestClient()
        self._username = username
        self._password = password

        # All supply points for this account
        self.points: List[CezData] = []

        self.signals_coordinator: Optional[DataUpdateCoordinator] = None
        self.outages_coordinator: Optional[DataUpdateCoordinator] = None
        self.readings_coordinator: Optional[DataUpdateCoordinator] = None

        # EAN -> time of the last successful HDO schedule download
        self.signals_updated: Dict[str, dt.datetime] = {}

        self._scan_signals = dt.timedelta(minutes=scan_signals_min)
        self._scan_outages = dt.timedelta(minutes=scan_outages_min)
        self._scan_readings = dt.timedelta(minutes=scan_readings_min)

    @property
    def ean(self) -> Optional[str]:
        """Return EAN of the first supply point if available (may be None)."""
        try:
            if self.points:
                return (self.points[0].supply_point_detail or {}).get("ean")
            return None
        except Exception:
            return None

    async def _login_and_load_static(self) -> None:
        def _blocking_login_and_load():
            self._client.login(self._username, self._password)
            header = self._client.common_header()
            supply_points = self._client.get_supply_points()
            # Build list of all visible supply points (across all blocks and vstelles)
            self.points = []
            for sp_block in (supply_points.get("vstelleBlocks", {}).get("blocks", []) or []):
                for sp in (sp_block.get("vstelles", []) or []):
                    try:
                        uid = sp["uid"]
                        sp_detail = self._client.get_supply_point_detail(uid)
                        readings = self._client.get_readings(uid)
                        ean = sp_detail.get("ean") or sp.get("ean")
                        signals = self._client.get_signals(ean) if ean and sp_detail.get("hdo") else {"signals": []}
                        outages = self._client.get_outages(ean=ean) or [] if ean else []
                        self.points.append(CezData(header, sp, sp_detail, readings, signals, outages))
                    except Exception as e:
                        _LOGGER.warning("Failed to load supply point %s: %s", sp, e)
            # If nothing was loaded, attempt to load the first available point as a single entry in self.points
            if not self.points:
                sp_block = supply_points["vstelleBlocks"]["blocks"][0]
                sp = sp_block["vstelles"][0]
                uid = sp["uid"]
                sp_detail = self._client.get_supply_point_detail(uid)
                readings = self._client.get_readings(uid)
                signals = self._client.get_signals(sp_detail["ean"]) if sp_detail.get("hdo") else {"signals": []}
                outages = self._client.get_outages(ean=sp_detail["ean"]) or []
                self.points.append(CezData(header, sp, sp_detail, readings, signals, outages))

        await self.hass.async_add_executor_job(_blocking_login_and_load)

    async def async_setup(self) -> None:
        await self._login_and_load_static()

        # Import historical statistics for initially loaded readings
        for p in self.points:
            ean = (p.supply_point_detail or {}).get("ean") or (p.supply_point or {}).get("ean")
            if ean and p.readings:
                async_import_readings_statistics(self.hass, ean, p.readings)
            if ean and (p.signals or {}).get("signals"):
                self.signals_updated[ean] = dt.datetime.now(dt.timezone.utc)

        async def _update_signals() -> Dict[str, Any]:
            def _blocking():
                result: Dict[str, Any] = {}
                points = self.points
                if not points:
                    return result
                for p in points:
                    try:
                        ean = p.supply_point_detail.get("ean")
                        if not ean or not p.supply_point_detail.get("hdo"):
                            continue
                        signals = self._client.get_signals(ean)
                        if not (signals or {}).get("signals"):
                            # Prázdný rozpis u OM s HDO není úspěch, poslední dobrý zůstává
                            raise ValueError("portal returned an empty HDO schedule")
                        p.signals = result[ean] = signals
                        self.signals_updated[ean] = dt.datetime.now(dt.timezone.utc)
                    except Exception as e:
                        _LOGGER.warning("Failed refreshing signals for %s: %s", ean, e)
                        # Fallback to last known good schedule
                        if ean:
                            result[ean] = p.signals
                return result
            return await self.hass.async_add_executor_job(_blocking)

        async def _update_outages() -> Dict[str, List[Dict[str, Any]]]:
            def _blocking():
                result: Dict[str, List[Dict[str, Any]]] = {}
                points = self.points
                if not points:
                    return result
                for p in points:
                    try:
                        ean = p.supply_point_detail.get("ean")
                        if not ean:
                            continue
                        result[ean] = self._client.get_outages(ean=ean) or []
                    except Exception as e:
                        _LOGGER.warning("Failed refreshing outages for %s: %s", ean, e)
                        if ean:
                            result[ean] = p.outages_for_ean
                return result
            return await self.hass.async_add_executor_job(_blocking)

        async def _update_readings() -> Dict[str, List[Dict[str, Any]]]:
            def _blocking():
                result: Dict[str, List[Dict[str, Any]]] = {}
                points = self.points
                if not points:
                    return result
                for p in points:
                    try:
                        uid = p.supply_point.get("uid")
                        if not uid:
                            continue
                        result[uid] = self._client.get_readings(uid)
                    except Exception as e:
                        _LOGGER.warning("Failed refreshing readings for %s: %s", uid, e)
                        if uid:
                            result[uid] = p.readings
                return result

            readings_map = await self.hass.async_add_executor_job(_blocking)
            for p in self.points:
                uid = (p.supply_point or {}).get("uid")
                ean = (p.supply_point_detail or {}).get("ean") or (p.supply_point or {}).get("ean")
                if uid and ean and uid in readings_map:
                    async_import_readings_statistics(self.hass, ean, readings_map[uid])
            return readings_map

        self.signals_coordinator = DataUpdateCoordinator(
            self.hass,
            _LOGGER,
            name="ČEZ Distribuce signals",
            update_method=_update_signals,
            update_interval=self._scan_signals,
        )
        self.outages_coordinator = DataUpdateCoordinator(
            self.hass,
            _LOGGER,
            name="ČEZ Distribuce outages",
            update_method=_update_outages,
            update_interval=self._scan_outages,
        )
        self.readings_coordinator = DataUpdateCoordinator(
            self.hass,
            _LOGGER,
            name="ČEZ Distribuce readings",
            update_method=_update_readings,
            update_interval=self._scan_readings,
        )

        # Prime the coordinators. YAML setup has no config entry, where
        # async_config_entry_first_refresh is refused (#7).
        for coordinator in (self.signals_coordinator, self.outages_coordinator, self.readings_coordinator):
            if coordinator.config_entry is None:
                await coordinator.async_refresh()
            else:
                await coordinator.async_config_entry_first_refresh()


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    # YAML setup (legacy). If present, set up a single hub and load platforms via discovery.
    if DOMAIN not in config:
        return True

    conf = config[DOMAIN]
    username = conf[CONF_USERNAME]
    password = conf[CONF_PASSWORD]
    scan_signals = conf.get("scan_interval_signals", DEFAULT_SCAN_INTERVAL_SIGNALS_MIN)
    scan_outages = conf.get("scan_interval_outages", DEFAULT_SCAN_INTERVAL_OUTAGES_MIN)
    scan_readings = conf.get("scan_interval_readings", DEFAULT_SCAN_INTERVAL_READINGS_MIN)

    hub = CezHub(hass, username, password, scan_signals, scan_outages, scan_readings)
    await hub.async_setup()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["legacy"] = hub

    for platform in PLATFORMS:
        hass.async_create_task(async_load_platform(hass, platform, DOMAIN, {}, config))

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = entry.data
    username = data[CONF_USERNAME]
    password = data[CONF_PASSWORD]
    scan_signals = data.get("scan_interval_signals", DEFAULT_SCAN_INTERVAL_SIGNALS_MIN)
    scan_outages = data.get("scan_interval_outages", DEFAULT_SCAN_INTERVAL_OUTAGES_MIN)
    scan_readings = data.get("scan_interval_readings", DEFAULT_SCAN_INTERVAL_READINGS_MIN)

    hub = CezHub(hass, username, password, scan_signals, scan_outages, scan_readings)
    await hub.async_setup()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = hub

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
            hass.data[DOMAIN].pop(entry.entry_id)
        # Clean up domain dict if empty
        if DOMAIN in hass.data and not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
    return unload_ok
