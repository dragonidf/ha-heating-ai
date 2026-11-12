"""Heating AI V1+ native integration identity. Shadow-only until handoff."""
DOMAIN = "heating_ai_legacy"
PLATFORMS = ["sensor"]
SOURCE_UI = "sensor.heating_ui"
MODEL = "sensor.heating_model"
ENVIRONMENT = "sensor.heating_environment"
FORECAST = "sensor.heating_forecast"

# Optional source hints from the original installed house. Every role is
# verified against HA at setup, and unavailable readings are NEVER made zero.
# No credential, personal state or location is bundled in this map.
SOURCE_HINTS = {
    "thermostat": "climate.kotel",
    "outage_calendar": "calendar.svitlo_svitlo_kiivska_oblast_3_1",
    "fireplace_temp": "sensor.t_h_kamin_vitalnia_temperature",
    "grid": "input_select.indikator_merezhi_stan",
    # Names verified in HA: phase 1 = c, phase 2 = b, phase 3 = main.
    # Never confuse their *last display values* with grid availability.
    "voltage_l1": "sensor.vkhidnii_sensor_220_voltage_phase_c",
    "voltage_l2": "sensor.vkhidnii_sensor_220_voltage_phase_b",
    "voltage_l3": "sensor.vkhidnii_sensor_220_voltage",
    "fireplace": "sensor.heating_fireplace",
    "kws_l1": "sensor.energy_meter_kotel_potuzhnist_a",
    "kws_l2": "sensor.energy_meter_kotel_potuzhnist_b",
    "kws_l3": "sensor.energy_meter_kotel_potuzhnist_c",
    "kws_energy": "sensor.energy_meter_kotel_energiia",
    "recuperator": "switch.rekuperator_switch_1",
    "bath": "switch.vitiazhka_vanna_switch_1",
}
