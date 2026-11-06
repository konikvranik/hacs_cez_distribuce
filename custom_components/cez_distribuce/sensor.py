from __future__ import annotations

from typing import Any, Dict, List, Optional

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import CezHub
from .const import DOMAIN


def _extract_readings_list(data: Any, uid: Optional[str]) -> List[Dict[str, Any]]:
    """Return a flat list of reading dicts for given UID from coordinator data.

    Expects coordinator.data to be a dict keyed by UID -> list of readings.
    """
    if not isinstance(data, dict) or not uid:
        return []
    val = data.get(uid)
    return val if isinstance(val, list) else []


def _detail_for_uid(hub: CezHub, uid: Optional[str]) -> Dict[str, Any]:
    """Return the supply_point_detail dict for the given UID, or {}."""
    if not uid:
        return {}
    for p in getattr(hub, "points", []) or []:
        if (p.supply_point or {}).get("uid") == uid:
            return p.supply_point_detail or {}
    return {}


def _latest_reading(readings: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not readings:
        return None
    # readings already appear sorted newest first; ensure by max on date
    try:
        return sorted(readings, key=lambda r: r.get("datumOdectu", ""), reverse=True)[0]
    except Exception:
        return readings[0]


def _build_reading_entities(hub: CezHub, uid: str, ean: Optional[str]) -> list[Entity]:
    name_suffix = f" – EAN {ean}" if ean else ""
    return [
        CezReadingSensor(hub, uid, ean, "VT", f"Poslední stav VT{name_suffix}", "last_reading_vt"),
        CezReadingSensor(hub, uid, ean, "NT", f"Poslední stav NT{name_suffix}", "last_reading_nt"),
        CezTotalSensor(hub, uid, ean),
    ]


def _build_detail_entities(hub: CezHub, uid: str, ean: Optional[str]) -> list[Entity]:
    """Static entities derived from the supply-point detail (no extra HTTP)."""
    entities: list[Entity] = [
        CezWeekReadingSensor(hub, uid, ean),
        CezTariffSensor(hub, uid, ean),
    ]
    if ean and _detail_for_uid(hub, uid).get("hdo"):
        entities.append(CezSignalScheduleSensor(hub, ean))
    return entities


async def async_setup_platform(hass: HomeAssistant, config, async_add_entities, discovery_info=None):
    # Legacy YAML path
    hub: CezHub = hass.data[DOMAIN]["legacy"] if isinstance(hass.data.get(DOMAIN), dict) and "legacy" in hass.data[
        DOMAIN] else hass.data[DOMAIN]
    entities: list[Entity] = []
    for p in getattr(hub, "points", []) or []:
        uid = (p.supply_point or {}).get("uid")
        ean = (p.supply_point_detail or {}).get("ean")
        if not uid:
            continue
        entities.extend(_build_reading_entities(hub, uid, ean))
        entities.extend(_build_detail_entities(hub, uid, ean))
    async_add_entities(entities)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    hub: CezHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[Entity] = []
    for p in getattr(hub, "points", []) or []:
        uid = (p.supply_point or {}).get("uid")
        ean = (p.supply_point_detail or {}).get("ean")
        if not uid:
            continue
        entities.extend(_build_reading_entities(hub, uid, ean))
        entities.extend(_build_detail_entities(hub, uid, ean))
    async_add_entities(entities)


class CezReadingSensor(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    # state_class is intentionally omitted so HA does not generate real-time
    # statistics on delayed meter readings. Long-term statistics are imported
    # via async_add_external_statistics with the actual reading date.

    def __init__(self, hub: CezHub, uid: str, ean: Optional[str], kind: str, name: str, unique_suffix: str) -> None:
        super().__init__(hub.readings_coordinator)
        self._hub = hub
        self._uid = uid
        self._ean = ean
        self._kind = kind  # "VT" or "NT"
        self._attr_name = name
        base = ean or uid
        self._attr_unique_id = f"{base}_{unique_suffix}"

    @property
    def native_value(self) -> Optional[float]:
        readings = _extract_readings_list(self.coordinator.data, self._uid)
        latest = _latest_reading(readings)
        if not latest:
            return None
        key = "stavVt" if self._kind == "VT" else "stavNt"
        try:
            val = float(str(latest.get(key, "0")).strip())
            return round(val, 3)
        except Exception:
            return None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        latest = _latest_reading(_extract_readings_list(self.coordinator.data, self._uid)) or {}
        attrs = {
            "ean": self._ean,
            "uid": self._uid,
            "serial": latest.get("sernr"),
            "date": latest.get("datumOdectu"),
            "reason": latest.get("duvodOdectuText"),
        }
        # Augment with the meter's last read date from the static detail, if known.
        detail = _detail_for_uid(self._hub, self._uid)
        attrs["datum_posledniho_odectu"] = _last_read_date(detail)
        return attrs


class CezTotalSensor(CezReadingSensor):
    def __init__(self, hub: CezHub, uid: str, ean: Optional[str]) -> None:
        name = f"Poslední stav celkem{' – EAN ' + ean if ean else ''}"
        super().__init__(hub, uid, ean, "TOTAL", name, "last_reading_total")

    @property
    def native_value(self) -> Optional[float]:
        readings = _extract_readings_list(self.coordinator.data, self._uid)
        latest = _latest_reading(readings)
        if not latest:
            return None
        try:
            vt = float(str(latest.get("stavVt", "0")).strip())
        except Exception:
            vt = 0.0
        try:
            nt = float(str(latest.get("stavNt", "0")).strip())
        except Exception:
            nt = 0.0
        return round(vt + nt, 3)


class CezDetailSensor(SensorEntity):
    """Base for sensors derived from the (static) supply-point detail.

    No coordinator: the detail is fetched once at hub setup. These entities do
    not poll; reload the integration to refresh.
    """

    _attr_should_poll = False

    def __init__(self, hub: CezHub, uid: str, ean: Optional[str], name: str, unique_suffix: str) -> None:
        self._hub = hub
        self._uid = uid
        self._ean = ean
        self._attr_name = name
        base = ean or uid
        self._attr_unique_id = f"{base}_{unique_suffix}"

    @property
    def detail(self) -> Dict[str, Any]:
        return _detail_for_uid(self._hub, self._uid)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        return {"ean": self._ean, "uid": self._uid}


class CezWeekReadingSensor(CezDetailSensor):
    """Week number of the next planned meter reading (distribution contract)."""

    def __init__(self, hub: CezHub, uid: str, ean: Optional[str]) -> None:
        super().__init__(hub, uid, ean, "Týden odečtu", "week_reading")

    @property
    def native_value(self) -> Optional[int]:
        anlage = self.detail.get("anlage_Dist") or {}
        val = anlage.get("tydenOdectu")
        try:
            return int(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        attrs = super().extra_state_attributes
        anlage = self.detail.get("anlage_Dist") or {}
        attrs.update({
            "interval": anlage.get("tydenOdectuFormatted"),
            "mesic_odectu": anlage.get("mesicOdectu"),
            "cislo_smlouvy": anlage.get("cislo"),
            "sazba": anlage.get("sazba"),
            "distributor": anlage.get("distributorTxt") or anlage.get("distributor"),
        })
        return attrs


class CezTariffSensor(CezDetailSensor):
    """Current distribution tariff code (e.g. ``D57D``) and main fuse rating.

    The portal returns a list of time-sliced tariff entries (``casoveRezy``)
    that is NOT chronologically ordered, so the "current" entry is the one with
    the latest ``sazbaDistribuceOd``.
    """

    def __init__(self, hub: CezHub, uid: str, ean: Optional[str]) -> None:
        super().__init__(hub, uid, ean, "Sazba distribuce", "tariff")

    @property
    def native_value(self) -> Optional[str]:
        rez = _current_tariff_slice(self.detail)
        return rez.get("sazbaDistribuce") if rez else None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        attrs = super().extra_state_attributes
        rez = _current_tariff_slice(self.detail) or {}
        fakta = self.detail.get("faktaOM") or {}
        attrs.update({
            "hlavni_jistic": _as_int(rez.get("hlavniJistic")),
            "pocet_fazi": _as_int(rez.get("pocetFazi")),
            "typ_mereni": rez.get("typMereni") or fakta.get("typMereni"),
            "typ_odberu": rez.get("typOdberu"),
            "platnost_od": _iso_date(rez.get("sazbaDistribuceOd")),
            "napetova_hladina": fakta.get("napetovaHladina"),
            "jistic_charakteristika": fakta.get("jisticCharakteristika"),
        })
        return attrs


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _current_tariff_slice(detail: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the currently-valid entry from faktaOM.casoveRezy.

    The list is not chronologically ordered, so we pick the slice with the
    newest ``sazbaDistribuceOd``. Returns None if the list is empty/missing.
    """
    fakta = detail.get("faktaOM") or {}
    slices = fakta.get("casoveRezy") or []
    if not slices:
        return None
    try:
        return max(slices, key=lambda c: c.get("sazbaDistribuceOd") or "")
    except (TypeError, ValueError):
        return slices[-1]


def _as_int(val: Any) -> Optional[int]:
    """Coerce portal values (floats like 40.0, numeric strings) to int."""
    if val is None:
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _iso_date(val: Any) -> Optional[str]:
    """Return just the date part of an ISO datetime string ('2026-06-30')."""
    if not isinstance(val, str) or not val:
        return None
    return val.split("T", 1)[0] or None


def _last_read_date(detail: Dict[str, Any]) -> Optional[str]:
    """Best-effort last meter read date from the static detail.

    Prefers ``faktaOM.datumPoslednihoOdectu``; falls back to the first meter's
    ``lastReadDate``. Returns the date part only.
    """
    fakta = detail.get("faktaOM") or {}
    val = fakta.get("datumPoslednihoOdectu")
    if not val:
        meters = detail.get("elektromery") or []
        if meters and isinstance(meters[0], dict):
            val = meters[0].get("lastReadDate")
    return _iso_date(val)


class CezSignalScheduleSensor(CoordinatorEntity, SensorEntity):
    """HDO switching schedule for the coming days, as returned by the portal.

    State is the time of the last successful download. The ``signals``
    attribute carries the raw list (``datum``, ``den``, ``signal``, ``casy``) so
    templates can build their own tariff schedule. When a refresh fails, the
    last good schedule stays and the state does not move, which makes a stale
    schedule easy to detect.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, hub: CezHub, ean: str) -> None:
        super().__init__(hub.signals_coordinator)
        self._hub = hub
        self._ean = ean
        self._attr_name = "HDO – rozpis"
        self._attr_unique_id = f"{ean}_hdo_schedule"

    @property
    def native_value(self):
        return self._hub.signals_updated.get(self._ean)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        data = self.coordinator.data or {}
        return {"ean": self._ean, "signals": (data.get(self._ean) or {}).get("signals", [])}
