import logging
import subprocess
import time
# Note: code below contains additional imports called only when feature is enabled

def _reset_i2c():
    """Reset I2C bus. E.g. as a recovery attempt when there is an issue with sensor communication."""
    logging.info("Resetting I2C.")
    subprocess.run(['i2cdetect', '-y', '1'])
    time.sleep(2)

def _get_cpu_temperature():
    """Get the temperature of the CPU for compensation"""
    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        temp = f.read()
        temp = int(temp) / 1000.0
    return temp


class BME280Sensor:
    """BME280 temperature, pressure and humidity sensor."""

    def __init__(self, temperature_factor):
        self._temperature_factor = temperature_factor

        try:
            from smbus2 import SMBus
        except ImportError:
            from smbus import SMBus
        from bme280 import BME280
        self._bme280 = BME280(i2c_dev=SMBus(1))

    def update(self, values):
        try:
            self._bme280.update_sensor()  # Note: update once and then read instance properties (avoid multiple updates by individual get_... calls)
            temperature = self._bme280.temperature
            pressure = self._bme280.pressure
            humidity = self._bme280.humidity

            # Tuning factor for compensation of heat transfer from the CPU. Value should be <0,1).
            # - When 0 means no correction (CPU is not affecting measured value)
            # - When 1 (resp. approaching to 1) means that all heat is transferred to sensor and CPU temperature is measured
            #   (with the theoretical value of 1 we are just measuring CPU temperature and we can't calculate back original ambient temperature)
            if self._temperature_factor:
                # Smooth out with some averaging to decrease jitter
                # TODO since CPU temperature is refreshed with lower frequency then this has little effect => use e.g. some long-term EWMA
                cpu_temps = [_get_cpu_temperature() for _ in range(5)]
                avg_cpu_temp = sum(cpu_temps) / float(len(cpu_temps))

                # Heat transfer from CPU should be proportional to the difference of ambient vs CPU temperature.
                # i.e.: T_measured = T_ambient + (T_cpu - T_ambient) * factor
                # so ambient temperature is calculated from measured value using ...
                temperature = (temperature - avg_cpu_temp * self._temperature_factor) / (1 - self._temperature_factor)

            values['temperature_celsius'] = temperature
            values['pressure_pascals'] = pressure * 100  # hPa to Pa
            values['relative_humidity'] = humidity / 100  # percentage to 0-1 ratio
            return True
        except IOError:
            logging.error("Could not get BME280 weather readings. Resetting I2C.")
            _reset_i2c()
        return False

class LTR559Sensor:
    """LTR559 light and proximity sensor."""

    def __init__(self):
        try:
            # Transitional fix for breaking change in LTR559
            from ltr559 import LTR559
            self._ltr559 = LTR559()
        except ImportError:
            import ltr559
            self._ltr559 = ltr559

    def update(self, values):
        try:
            self._ltr559.update_sensor()
            values['light_lux'] = self._ltr559.get_lux(passive=True)
            values['proximity_raw'] = self._ltr559.get_proximity(passive=True)
            return True
        except IOError:
            logging.error("Could not get LTR559 light readings. Resetting I2C.")
            _reset_i2c()
        return False

class MICS6814Sensor:
    """MICS6814 gas sensor."""

    def __init__(self):
        from enviroplus import gas
        self._gas_sensor = gas

    def update(self, values):
        """Update all gas sensor readings"""
        try:
            readings = self._gas_sensor.read_all()
            values['gas_red_ohms'] = readings.reducing
            values['gas_ox_ohms'] = readings.oxidising
            values['gas_nh3_ohms'] = readings.nh3
            return True
        except IOError:
            logging.error("Could not get MICS6814 gas readings. Resetting I2C.")
            _reset_i2c()
        return False

class PMS5003Sensor:
    """PMS5003 pariculate matter sensor."""

    def __init__(self):
        from pms5003 import PMS5003, ReadTimeoutError as pmsReadTimeoutError
        self._pms5003 = PMS5003()
        self._pms5003_read_timeout_error = pmsReadTimeoutError

    def update(self, values):
        """Update the particulate matter sensor readings"""
        try:
            pms_data = self._pms5003.read()
            values['pm_1u'] = pms_data.pm_ug_per_m3(1.0)
            values['pm_2u5'] = pms_data.pm_ug_per_m3(2.5)
            values['pm_10u'] = pms_data.pm_ug_per_m3(10)
            return True
        except self._pms5003_read_timeout_error:
            logging.error("Failed to read PMS5003 particulate matter sensor")
        except IOError:
            logging.error("Could not get PMS5003 particulate matter readings. Resetting I2C.")
            _reset_i2c()
        return False

def create_sensors(enviro=False, temperature_factor=None):
    """Create sensors and return sequence with sensor update functions."""
    sensor_update_functions = [
        BME280Sensor(temperature_factor).update,
        LTR559Sensor().update
    ]
    if not enviro:
        sensor_update_functions.append(MICS6814Sensor().update)
        sensor_update_functions.append(PMS5003Sensor().update)
    return sensor_update_functions
