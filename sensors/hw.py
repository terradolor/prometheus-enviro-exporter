import functools
import logging
import subprocess
import time

from bme280 import BME280
from enviroplus import gas
from pms5003 import PMS5003, ReadTimeoutError as pmsReadTimeoutError

try:
    from smbus2 import SMBus
except ImportError:
    from smbus import SMBus

try:
    # Transitional fix for breaking change in LTR559
    from ltr559 import LTR559
    ltr559 = LTR559()
except ImportError:
    import ltr559

bus = SMBus(1)
bme280 = BME280(i2c_dev=bus)
pms5003 = PMS5003()

# Sometimes the sensors can't be read. Resetting the i2c
def reset_i2c():
    subprocess.run(['i2cdetect', '-y', '1'])
    time.sleep(2)


def get_cpu_temperature():
    """Get the temperature of the CPU for compensation"""
    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        temp = f.read()
        temp = int(temp) / 1000.0
    return temp

def update_weather_sensor(temperature_factor, values):
    """Update values from the weather sensor"""
    try:
        bme280.update_sensor()  # Note: update once and then read instance properties (avoid multiple updates by individual get_... calls)
        temperature = bme280.temperature
        pressure = bme280.pressure
        humidity = bme280.humidity

        # Tuning factor for compensation of heat transfer from the CPU. Value should be <0,1).
        # - When 0 means no correction (CPU is not affecting measured value)
        # - When 1 (resp. approaching to 1) means that all heat is transferred to sensor and CPU temperature is measured
        #   (with the theoretical value of 1 we are just measuring CPU temperature and we can't calculate back original ambient temperature)
        if temperature_factor:
            # Smooth out with some averaging to decrease jitter
            # TODO since CPU temperature is refreshed with lower frequency then this has little effect => use e.g. some long-term EWMA
            cpu_temps = [get_cpu_temperature() for _ in range(5)]
            avg_cpu_temp = sum(cpu_temps) / float(len(cpu_temps))

            # Heat transfer from CPU should be proportional to the difference of ambient vs CPU temperature.
            # i.e.: T_measured = T_ambient + (T_cpu - T_ambient) * factor
            # so ambient temperature is calculated from measured value using ...
            temperature = (temperature - avg_cpu_temp * temperature_factor) / (1 - temperature_factor)

        values['temperature_celsius'] = temperature
        values['pressure_pascals'] = pressure * 100  # hPa to Pa
        values['relative_humidity'] = humidity / 100  # percentage to 0-1 ratio
        return True
    except IOError:
        logging.error("Could not get BME280 readings. Resetting i2c.")
        reset_i2c()
    return False

def update_light_sensor(values):
    """Update all light sensor readings"""
    try:
        ltr559.update_sensor()
        values['light_lux'] = ltr559.get_lux(passive=True)
        values['proximity_raw'] = ltr559.get_proximity(passive=True)
        return True
    except IOError:
        logging.error("Could not get light and proximity readings. Resetting i2c.")
        reset_i2c()
    return False

def update_gas_sensor(values):
    """Update all gas sensor readings"""
    try:
        readings = gas.read_all()
        values['gas_red_ohms'] = readings.reducing
        values['gas_ox_ohms'] = readings.oxidising
        values['gas_nh3_ohms'] = readings.nh3
        return True
    except IOError:
        logging.error("Could not get gas readings. Resetting i2c.")
        reset_i2c()
    return False

def update_particulate_sensor(values):
    """Update the particulate matter sensor readings"""
    try:
        pms_data = pms5003.read()
        values['pm_1u'] = pms_data.pm_ug_per_m3(1.0)
        values['pm_2u5'] = pms_data.pm_ug_per_m3(2.5)
        values['pm_10u'] = pms_data.pm_ug_per_m3(10)
        return True
    except pmsReadTimeoutError:
        logging.error("Failed to read PMS5003")
    except IOError:
        logging.error("Could not get particulate matter readings. Resetting i2c.")
        reset_i2c()
    return False

def create_sensors(enviro=False, temperature_factor=None):
    """Create sensors and return sequence with sensor update functions."""
    sensor_update_functions = [
        functools.partial(update_weather_sensor, temperature_factor),
        update_light_sensor
    ]
    if not enviro:
        sensor_update_functions.append(update_gas_sensor)
        sensor_update_functions.append(update_particulate_sensor)
    return sensor_update_functions
