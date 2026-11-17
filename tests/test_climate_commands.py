"""Climate commands and states against the Loxone structure file spec."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.loxone.climate import (ActiveMode, ActiveState, HVACAction, HVACMode,
                                              LoxoneRoomController,
                                              LoxoneRoomControllerV2,
                                              OperatingMode)
from custom_components.loxone.const import PRESET_SCHEDULE


def _room_controller_v2(sent: list, operating_mode, active_mode, **state_values):
    rc = LoxoneRoomControllerV2.__new__(LoxoneRoomControllerV2)
    rc.hass = SimpleNamespace(
        bus=SimpleNamespace(fire=lambda domain, data: sent.append(data["value"]))
    )
    rc.uuidAction = "rc-uuid"
    rc.operating_mode = operating_mode
    rc.active_state = ActiveState(mode=active_mode, value=0)
    rc._range_possible = False
    rc._demand = None
    rc._states = {name: f"uuid-{name}" for name in state_values}
    rc._state_attr_values = {f"uuid-{name}": v for name, v in state_values.items()}
    rc.schedule_update_ha_state = lambda: None
    return rc


def test_irc_v2_schedule_preset_without_override_switches_to_automatic():
    """Bug: the fallback sent 'setOperationMode/0' - a typo, this command
    does not exist in the spec, so the Miniserver ignores it and the
    "Schedule" preset silently did nothing.

    Spec (Intelligent Room Controller v2): `setOperatingMode/{opMode}`,
    0 = automatic, heating and cooling allowed."""
    sent: list = []
    rc = _room_controller_v2(
        sent, OperatingMode.AUTO_HEAT_COOL, ActiveMode.COMFORT, overrideReason=0
    )
    rc._modeList = [{"id": "stop", "name": PRESET_SCHEDULE}]

    rc.set_preset_mode(PRESET_SCHEDULE)

    assert sent == ["setOperatingMode/0"]


def test_irc_v2_schedule_preset_while_overridden_stops_override():
    sent: list = []
    rc = _room_controller_v2(
        sent, OperatingMode.AUTO_HEAT_COOL, ActiveMode.COMFORT, overrideReason=3
    )
    rc._modeList = [{"id": "stop", "name": PRESET_SCHEDULE}]

    rc.set_preset_mode(PRESET_SCHEDULE)

    assert sent == ["stopOverride"]


@pytest.mark.parametrize(
    ("states", "requested", "command"),
    [
        # Issue #544: comfort 24 °C, 24.5 °C requested. The absolute value
        # sent before ended up as a target of 24 + 24.5 = 48.5 °C.
        (
            {"comfortTemperature": 24.0, "comfortTemperatureOffset": 0, "tempTarget": 24.0},
            24.5,
            "setComfortModeTemp/0.5",
        ),
        # Same temperature again: the offset stays 0 (was 48.0 °C).
        (
            {"comfortTemperature": 24.0, "comfortTemperatureOffset": 0, "tempTarget": 24.0},
            24.0,
            "setComfortModeTemp/0.0",
        ),
        # An offset that is already adopted is replaced, not added twice.
        (
            {"comfortTemperature": 24.0, "comfortTemperatureOffset": 0.5, "tempTarget": 24.5},
            23.0,
            "setComfortModeTemp/-1.0",
        ),
        # Economy: the target is below the comfort temperature.
        (
            {"comfortTemperature": 22.0, "comfortTemperatureOffset": 0, "tempTarget": 19.0},
            20.0,
            "setComfortModeTemp/1.0",
        ),
        # No offset state (yet): counts as 0.
        ({"comfortTemperature": 21.0, "tempTarget": 21.0}, 22.0, "setComfortModeTemp/1.0"),
        # No target state yet: the comfort temperature is the reference.
        ({"comfortTemperature": 21.0}, 22.0, "setComfortModeTemp/1.0"),
    ],
)
def test_comfort_mode_temp_is_the_comfort_temperature_offset(states, requested, command):
    """The Miniserver takes the argument of setComfortModeTemp as the new
    comfortTemperatureOffset (issue #544), not as an absolute temperature."""
    sent: list = []
    rc = _room_controller_v2(sent, OperatingMode.AUTO_HEAT, ActiveMode.COMFORT, **states)
    rc.set_temperature(temperature=requested)
    assert sent == [command]


def test_comfort_mode_temp_is_not_sent_without_a_reference():
    sent: list = []
    rc = _room_controller_v2(sent, OperatingMode.AUTO_HEAT, ActiveMode.COMFORT)
    rc.set_temperature(temperature=22.0)
    assert sent == []


def _room_controller_v1(sent: list, details=None, **state_values):
    rc = LoxoneRoomController.__new__(LoxoneRoomController)
    rc.hass = SimpleNamespace(
        bus=SimpleNamespace(fire=lambda domain, data: sent.append(data["value"]))
    )
    rc.uuidAction = "irc-uuid"
    rc._attr_name = "Room"
    rc.details = details or {}
    rc._stateAttribUuids = {name: f"uuid-{name}" for name in state_values}
    rc._stateAttribValues = {f"uuid-{name}": v for name, v in state_values.items()}
    rc.schedule_update_ha_state = lambda: None
    return rc


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (0, HVACMode.AUTO),
        (1, HVACMode.AUTO),  # automatic, currently heating
        (2, HVACMode.AUTO),  # automatic, currently cooling
        (3, HVACMode.HEAT),
        (4, HVACMode.COOL),  # was shown as OFF
        (5, HVACMode.HEAT),  # manual heating, was shown as OFF
        (6, HVACMode.COOL),  # manual cooling, was shown as OFF
    ],
)
def test_irc_mode_states(mode, expected):
    assert _room_controller_v1([], mode=mode, serviceMode=0).hvac_mode == expected


def test_irc_service_mode_heating_cooling_off_is_off():
    assert _room_controller_v1([], mode=3, serviceMode=1).hvac_mode == HVACMode.OFF


@pytest.mark.parametrize(
    ("hvac_mode", "command"),
    [(HVACMode.AUTO, "mode/0"), (HVACMode.HEAT, "mode/3"), (HVACMode.COOL, "mode/4")],
)
def test_irc_set_mode_uses_the_mode_command(hvac_mode, command):
    sent: list = []
    _room_controller_v1(sent, mode=0, serviceMode=0).set_hvac_mode(hvac_mode)
    assert sent == [command]


def test_irc_off_switches_heating_and_cooling_off():
    """There is no "off" mode; mode 4 would be automatic cooling."""
    sent: list = []
    _room_controller_v1(sent, mode=3, serviceMode=0).set_hvac_mode(HVACMode.OFF)
    assert sent == ["service/1"]


def test_irc_leaving_off_ends_the_service_mode():
    sent: list = []
    _room_controller_v1(sent, mode=3, serviceMode=1).set_hvac_mode(HVACMode.HEAT)
    assert sent == ["service/0", "mode/3"]


def test_irc_settemp_uses_cooling_index_in_cooling_modes():
    sent: list = []
    rc = _room_controller_v1(sent, mode=4, currHeatTempIx=1, currCoolTempIx=2)
    rc.set_temperature(temperature=24.0)
    assert sent == ["settemp/2/24.0"]


def test_irc_hvac_modes_respect_restricted_to_mode():
    assert HVACMode.COOL not in _room_controller_v1([], {"restrictedToMode": 2}).hvac_modes
    assert HVACMode.HEAT not in _room_controller_v1([], {"restrictedToMode": 1}).hvac_modes
    assert HVACMode.HEAT_COOL not in _room_controller_v1([]).hvac_modes


@pytest.mark.parametrize(
    "states",
    [
        {},  # nothing received yet
        {"comfortTemperature": 21.0},  # offsets missing
        {"absentMinOffset": 2.0, "absentMaxOffset": 2.0},  # no comfortTemperatureCool (< 12.1)
    ],
)
def test_economy_range_with_missing_states_is_unknown(states):
    rc = _room_controller_v2([], OperatingMode.AUTO_HEAT_COOL, ActiveMode.ECONOMY, **states)
    assert rc.target_temperature_high is None
    assert rc.target_temperature_low is None


def test_economy_range_from_comfort_and_offsets():
    rc = _room_controller_v2(
        [],
        OperatingMode.AUTO_HEAT_COOL,
        ActiveMode.ECONOMY,
        comfortTemperature=21.0,
        comfortTemperatureCool=24.0,
        absentMinOffset=2.0,
        absentMaxOffset=3.0,
    )
    assert rc.target_temperature_low == 19.0
    assert rc.target_temperature_high == 27.0


def test_irc_v2_active_heat_cool_room_below_range_is_heating():
    """Basement, live: no ClimateController in the structure file, so no
    demand events; ``active`` was 1 at 69.8 in a 70-74 comfort range while
    its radiant loop ran. Was reported idle."""
    rc = _room_controller_v2(
        [], OperatingMode.AUTO_HEAT_COOL, ActiveMode.COMFORT,
        operatingMode=0, active=1.0, tempActual=69.8, tempTarget=70.0,
        comfortTemperature=70.0, comfortTemperatureCool=74.0,
    )
    assert rc.hvac_action == HVACAction.HEATING


def test_irc_v2_active_heat_cool_room_above_range_is_cooling():
    rc = _room_controller_v2(
        [], OperatingMode.AUTO_HEAT_COOL, ActiveMode.COMFORT,
        operatingMode=0, active=1.0, tempActual=74.6,
        comfortTemperature=70.0, comfortTemperatureCool=74.0,
    )
    assert rc.hvac_action == HVACAction.COOLING


def test_irc_v2_active_heating_mode_is_heating():
    """Den, live: heating-only automatic, fixed setpoint 74 at 71.2."""
    rc = _room_controller_v2(
        [], OperatingMode.AUTO_HEAT, ActiveMode.FIXED_DYNAMIC,
        operatingMode=1, active=1.0, tempActual=71.24, tempTarget=74.0,
    )
    assert rc.hvac_action == HVACAction.HEATING


def test_irc_v2_inactive_room_is_idle():
    rc = _room_controller_v2(
        [], OperatingMode.AUTO_HEAT, ActiveMode.COMFORT,
        operatingMode=1, active=0.0, tempActual=73.0, tempTarget=71.0,
    )
    assert rc.hvac_action == HVACAction.IDLE


def test_irc_v2_climate_controller_demand_wins_over_active():
    rc = _room_controller_v2(
        [], OperatingMode.AUTO_HEAT_COOL, ActiveMode.COMFORT,
        operatingMode=0, active=1.0, tempActual=69.0, tempTarget=70.0,
    )
    rc._demand = 0
    assert rc.hvac_action == HVACAction.IDLE
    rc._demand = -1
    assert rc.hvac_action == HVACAction.COOLING


@pytest.mark.parametrize(
    ("prepare", "expected"), [(1, HVACAction.PREHEATING), (-1, HVACAction.COOLING)]
)
def test_irc_v2_prepare_state(prepare, expected):
    rc = _room_controller_v2(
        [], OperatingMode.AUTO_HEAT_COOL, ActiveMode.COMFORT,
        operatingMode=0, prepareState=prepare,
    )
    assert rc.hvac_action == expected


def test_irc_v2_off_operating_mode_is_off():
    rc = _room_controller_v2(
        [], OperatingMode.OFF, ActiveMode.OFF, operatingMode=-1, active=0.0
    )
    assert rc.hvac_action == HVACAction.OFF
    assert rc.hvac_mode == HVACMode.OFF


def test_irc_v2_unknown_before_operating_mode_arrives():
    """Bug: the OFF placeholder was reported until states arrived, drawing
    multi-hour false "off" spans after a reload."""
    rc = _room_controller_v2([], OperatingMode.OFF, ActiveMode.OFF)
    assert rc.hvac_mode is None
    assert rc.hvac_action is None


@pytest.mark.parametrize("reason", [6, 7])
def test_irc_v2_preparing_is_not_an_override(reason):
    """Bug: pre-heating (overrideReason 6) counted as an override, so an
    automatic heating room flipped to "heat" for the preparation window."""
    rc = _room_controller_v2(
        [], OperatingMode.AUTO_HEAT, ActiveMode.COMFORT,
        operatingMode=1, overrideReason=reason,
    )
    assert not rc.is_overridden
    assert rc.hvac_mode == HVACMode.AUTO


@pytest.mark.parametrize(
    ("format_str", "expected"),
    [("%.1f°F", "°F"), ("%.1f°C", "°C"), ("%.1f", "°C"), (None, "°C")],
)
def test_ac_control_temperature_unit(format_str, expected):
    from custom_components.loxone.climate import LoxoneAcControl, UnitOfTemperature

    ac = LoxoneAcControl.__new__(LoxoneAcControl)
    ac.details = {} if format_str is None else {"format": format_str}
    unit = UnitOfTemperature.FAHRENHEIT if expected == "°F" else UnitOfTemperature.CELSIUS
    assert ac.temperature_unit == unit


@pytest.mark.parametrize(
    "kwargs, commands",
    [
        ({"target_temp_low": 8.0}, ["setecoplusmintemperature/8.0"]),
        ({"target_temp_high": 35.0}, ["setecoplusmaxtemperature/35.0"]),
        (
            {"target_temp_low": 8.0, "target_temp_high": 35.0},
            ["setecoplusmaxtemperature/35.0", "setecoplusmintemperature/8.0"],
        ),
        # Unchanged values are compared with the protection temperatures,
        # not with the comfort temperatures.
        ({"target_temp_low": 5.0, "target_temp_high": 92.0}, []),
        ({"target_temp_low": 21.0, "target_temp_high": 24.0},
         ["setecoplusmaxtemperature/24.0", "setecoplusmintemperature/21.0"]),
    ],
)
def test_irc_v2_building_protection_range_uses_the_undocumented_commands(kwargs, commands):
    """setecoplus{min,max}temperature: not in the structure file, accepted by the Miniserver."""
    sent: list = []
    rc = _room_controller_v2(
        sent,
        OperatingMode.AUTO_HEAT_COOL,
        ActiveMode.BUILDING_PROTECT,
        comfortTemperature=21.0,
        comfortTemperatureCool=24.0,
        frostProtectTemperature=5.0,
        heatProtectTemperature=92.0,
    )
    rc._range_possible = True

    rc.set_temperature(**kwargs)

    assert sent == commands


def test_irc_v2_demand_event_without_value_does_not_raise():
    sent: list = []
    rc = _room_controller_v2(sent, OperatingMode.AUTO_HEAT_COOL, ActiveMode.COMFORT)

    rc.climate_handler(SimpleNamespace(data={"uuid": "rc-uuid"}))

    assert rc._demand == 0


def test_irc_v2_demand_event_stores_the_value():
    sent: list = []
    rc = _room_controller_v2(sent, OperatingMode.AUTO_HEAT_COOL, ActiveMode.COMFORT)

    rc.climate_handler(SimpleNamespace(data={"uuid": "rc-uuid", "value": 1}))

    assert rc._demand == 1
