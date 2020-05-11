"""Support for MyRenault services."""

import asyncio
import logging
import time
import json
import aiohttp
import traceback
from datetime import datetime, timedelta
from pyze.api import Gigya, Kamereon, Vehicle, CredentialStore, ChargeMode, ChargeState, PlugState

import voluptuous as vol

from homeassistant.helpers.entity import Entity

from homeassistant.helpers import config_validation as cv, entity_platform, service
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD, CONF_NAME

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

ATTR_CHARGING = 'charging'
ATTR_CHARGE_STATUS = 'charge_status'
ATTR_PLUGGED = 'plugged'
ATTR_PLUG_STATUS = 'plug_status'
ATTR_CHARGE_LEVEL = 'charge_level'
ATTR_CHARGING_POWER = 'charging_power'
ATTR_CHARGING_REMAINING_TIME = 'charging_remaining_time'
ATTR_REMAINING_RANGE = 'remaining_range'
ATTR_LAST_UPDATE = 'last_update'
ATTR_BATTERY_TEMPERATURE = 'battery_temperature'
ATTR_BATTERY_AVAILABLE_ENERGY = 'battery_available_energy'
ATTR_MILEAGE = 'mileage'
ATTR_HVAC_STATUS = 'hvac_status'
ATTR_OUTSIDE_TEMPERATURE = 'outside_temperature'
ATTR_CHARGE_MODE = 'charge_mode'
ATTR_WHEN = 'when'
ATTR_TEMPERATURE = 'temperature'

CONF_VIN = 'vin'
CONF_ANDROID_LNG = 'android_lng'
CONF_K_ACCOUNTID = 'k_account_id'

SCAN_INTERVAL = timedelta(seconds=60)

SERVICE_AC_START = "ac_start"
SERVICE_AC_CANCEL = "ac_cancel"
SERVICE_CHARGE_START = "charge_start"
SERVICE_CHARGE_SET_MODE = "charge_set_mode"
SERVICE_CHARGE_SET_SCHEDULES = "charge_set_schedules"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_USERNAME): cv.string,
    vol.Required(CONF_PASSWORD): cv.string,
    vol.Required(CONF_VIN): cv.string,
    vol.Optional(CONF_ANDROID_LNG, default='fr_FR'): cv.string,
    vol.Optional(CONF_NAME, default=None): cv.string,
    vol.Optional(CONF_K_ACCOUNTID, default=''): cv.string,
})


async def async_setup_platform(hass, config, async_add_entities,
                               discovery_info=None):
    """Setup the sensor platform."""
    _LOGGER.debug("Initialising renaultze platform")
    
    g_url = None
    g_key = None
    k_url = None
    k_key = None
    k_account_id = config.get(CONF_K_ACCOUNTID, '')

    cred = CredentialStore()
    cred.clear()

    url = 'https://renault-wrd-prod-1-euw1-myrapp-one.s3-eu-west-1.amazonaws.com/configuration/android/config_%s.json' % config.get(CONF_ANDROID_LNG)
    async with aiohttp.ClientSession(
            ) as session:
        async with session.get(url) as response:
            responsetext = await response.text()
            if responsetext == '':
                responsetext = '{}'
            jsonresponse = json.loads(responsetext)
            
            g_url = jsonresponse['servers']['gigyaProd']['target']
            g_key = jsonresponse['servers']['gigyaProd']['apikey']
            k_url = jsonresponse['servers']['wiredProd']['target']
            k_key = jsonresponse['servers']['wiredProd']['apikey']

    g = Gigya(api_key=g_key,root_url=g_url)
    if not g.login(config.get(CONF_USERNAME),
                          config.get(CONF_PASSWORD)):
        raise RenaultZEError("Login failed")
    g.account_info()
    
    k = Kamereon(api_key=k_key,root_url=k_url,gigya=g)
    if k_account_id != '':
        k.set_account_id(k_account_id)

    v = Vehicle(config.get(CONF_VIN), k)

    devices = [
        RenaultZESensor(v,
                        config.get(CONF_NAME, config.get(CONF_VIN))
                        )
        ]
    async_add_entities(devices)
    
    platform = entity_platform.current_platform.get()

    platform.async_register_entity_service(
        SERVICE_AC_START,
        {
            vol.Optional(ATTR_WHEN): cv.datetime,
            vol.Optional(ATTR_TEMPERATURE): cv.positive_int,
        },
        "ac_start",
    )
    platform.async_register_entity_service(
        SERVICE_AC_CANCEL,
        {},
        "ac_cancel",
    )
    platform.async_register_entity_service(
        SERVICE_CHARGE_START,
        {},
        "charge_start",
    )
    platform.async_register_entity_service(
        SERVICE_CHARGE_SET_MODE,
        {
            vol.Required(ATTR_CHARGE_MODE): cv.enum(ChargeMode),
        },
        "charge_set_mode",
    )
    platform.async_register_entity_service(
        SERVICE_CHARGE_SET_SCHEDULES,
        {
            vol.Optional(ATTR_WHEN): cv.datetime,
            vol.Optional(ATTR_TEMPERATURE): cv.positive_int,
        },
        "charge_set_schedules",
    )


class RenaultZESensor(Entity):
    """Representation of a Sensor."""

    def __init__(self, vehicle, name):
        """Initialize the sensor."""
        _LOGGER.debug("Initialising RenaultZESensor %s" % name)
        self._state = None
        self._vehicle = vehicle
        self._name = name
        self._attrs = {}
        self._lastdeepupdate = 0

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def device_state_attributes(self):
        """Return the device state attributes."""
        return self._attrs

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return '%'

    def process_battery_response(self, jsonresult):
        """Update new state data for the sensor."""
        if 'batteryLevel' in jsonresult:
            self._state = jsonresult.get('batteryLevel')
            
        if 'batteryAvailableEnergy' in jsonresult:
            self._attrs[ATTR_BATTERY_AVAILABLE_ENERGY] = jsonresult['batteryAvailableEnergy'] > 0
        if 'chargingStatus' in jsonresult:
            self._attrs[ATTR_CHARGING] = jsonresult['chargingStatus'] > 0
            
            try:
                charge_state = ChargeState(jsonresult['chargingStatus'])
            except ValueError:
                charge_state = ChargeState.NOT_AVAILABLE
            self._attrs[ATTR_CHARGE_STATUS] = charge_state.name
        if 'timestamp' in jsonresult:
            self._attrs[ATTR_LAST_UPDATE] = jsonresult['timestamp']
        if 'plugStatus' in jsonresult:
            self._attrs[ATTR_PLUGGED] = jsonresult['plugStatus'] > 0
            
            try:
                plug_state = PlugState(jsonresult['plugStatus'])
            except ValueError:
                plug_state = PlugState.NOT_AVAILABLE
            self._attrs[ATTR_PLUG_STATUS] = plug_state.name
        if 'batteryTemperature' in jsonresult:
            self._attrs[ATTR_BATTERY_TEMPERATURE] = jsonresult['batteryTemperature']
        if 'batteryAutonomy' in jsonresult:
            self._attrs[ATTR_REMAINING_RANGE] = jsonresult['batteryAutonomy']
        if 'chargingInstantaneousPower' in jsonresult:
            self._attrs[ATTR_CHARGING_POWER] = jsonresult['chargingInstantaneousPower'] / 1000
        else:
            self._attrs[ATTR_CHARGING_POWER] = 0
        if 'chargingRemainingTime' in jsonresult:
            self._attrs[ATTR_CHARGING_REMAINING_TIME] = jsonresult['chargingRemainingTime']
        else:
            self._attrs[ATTR_CHARGING_REMAINING_TIME] = None

    def process_mileage_response(self, jsonresult):
        """Update new state data for the sensor."""
        if 'totalMileage' in jsonresult:
            self._attrs[ATTR_MILEAGE] = jsonresult['totalMileage']
            
    def process_hvac_response(self, jsonresult):
        """Update new state data for the sensor."""
        if 'hvacStatus' in jsonresult:
            self._attrs[ATTR_HVAC_STATUS] = jsonresult['hvacStatus']
        if 'externalTemperature' in jsonresult:
            self._attrs[ATTR_OUTSIDE_TEMPERATURE] = jsonresult['externalTemperature']

    def process_chargemode_response(self, jsonresult):
        """Update new state data for the sensor."""
        self._attrs[ATTR_CHARGE_MODE] = jsonresult.name

    def update(self):
        """Fetch new state data for the sensor.

        This is the only method that should fetch new data for Home Assistant.
        """
        # Run standard update
        try:
            jsonresult = self._vehicle.battery_status()
            _LOGGER.debug("Battery update result: %s" % jsonresult)
            self.process_battery_response(jsonresult)
        except Exception as e:
            _LOGGER.warning("Battery update failed: %s" % traceback.format_exc())

        try:
            jsonresult =  self._vehicle.mileage()
            _LOGGER.debug("Mileage update result: %s" % jsonresult)
            self.process_mileage_response(jsonresult)
        except Exception as e:
            _LOGGER.warning("Mileage update failed: %s" % traceback.format_exc())
            
        try:
            jsonresult =  self._vehicle.hvac_status()
            _LOGGER.debug("HVAC update result: %s" % jsonresult)
            self.process_hvac_response(jsonresult)
        except Exception as e:
            _LOGGER.warning("HVAC update failed: %s" % traceback.format_exc())

        try:
            jsonresult =  self._vehicle.charge_mode()
            _LOGGER.debug("Charge mode update result: %s" % jsonresult)
            self.process_chargemode_response(jsonresult)
        except Exception as e:
            _LOGGER.warning("Charge mode update failed: %s" % traceback.format_exc())

    def ac_start(self, when=None, temperature=21):
        self._vehicle.ac_start(when, temperature)

    def ac_cancel(self):
        self._vehicle.cancel_ac()

    def charge_set_schedules(self, schedules):
        self._vehicle.set_charge_schedules(schedules)

    def charge_set_mode(self, charge_mode):
        self._vehicle.set_charge_mode(charge_mode)

    def charge_start(self):
        self._vehicle.charge_start()

class RenaultZEError(Exception):
    pass
