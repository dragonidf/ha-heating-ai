"""Marketplace carrier for the already-running Heating AI V1 AppDaemon app.

This component intentionally registers no automations, entities, panels, or
climate/fan services. Installing it must NOT start a second controller.
Only an explicitly approved one-time migration of the existing AppDaemon
symlinks makes the separately bundled legacy_app code active.
"""


async def async_setup(hass, config):
    """Accept the package without touching the running heating system."""
    return True
