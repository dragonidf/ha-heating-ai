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
    "grid": "input_select.indikator_merezhi_stan",
    "fireplace": "sensor.heating_fireplace",
    "kws_l1": "sensor.energy_meter_kotel_potuzhnist_a",
    "kws_l2": "sensor.energy_meter_kotel_potuzhnist_b",
    "kws_l3": "sensor.energy_meter_kotel_potuzhnist_c",
    "kws_energy": "sensor.energy_meter_kotel_energiia",
}
