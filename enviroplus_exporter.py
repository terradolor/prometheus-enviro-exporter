#!/usr/bin/env python3
import os
import requests
import time
import logging
import argparse
import subprocess
from threading import Thread

from prometheus_client import start_http_server, Gauge, Histogram

from bme280 import BME280
from enviroplus import gas
from pms5003 import PMS5003, ReadTimeoutError as pmsReadTimeoutError

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS


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

logging.basicConfig(
    format='%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s',
    level=logging.INFO,
    handlers=[logging.FileHandler("enviroplus_exporter.log"),
              logging.StreamHandler()],
    datefmt='%Y-%m-%d %H:%M:%S')

logging.info("""enviroplus_exporter.py - Expose readings from the Pimoroni Enviro or Enviro+ sensor in Prometheus format

Press Ctrl+C to exit!

""")

DEBUG = os.getenv('DEBUG', 'false') == 'true'

bus = SMBus(1)
bme280 = BME280(i2c_dev=bus)
pms5003 = PMS5003()

TEMPERATURE = Gauge('enviro_temperature_celsius','Temperature')
PRESSURE = Gauge('enviro_pressure_pascals','Pressure')
HUMIDITY = Gauge('enviro_relative_humidity','Relative humidity')
LIGHT = Gauge('enviro_light_lux', 'Ambient light level')
PROXIMITY = Gauge('enviro_proximity_raw', 'Raw proximity value, with larger numbers being closer and vice versa')
GAS_RED = Gauge('enviro_gas_red_ohms', 'Gas RED sensor: CO, H2S, Ethanol, Hydrogen, Ammonia, Methane, Propane, Iso-butane')
GAS_OX = Gauge('enviro_gas_ox_ohms','Gas OX sensor: NO2, NO, Hydrogen')
GAS_NH3 = Gauge('enviro_gas_nh3_ohms', 'Gas NH3 sensor: Hydrogen, Ethanol, Amonia, Propane, Iso-butane')
PM1 = Gauge('enviro_pm_1u', 'Particulate Matter of diameter less than 1 micron. Measured in micrograms per cubic metre (ug/m3)')
PM25 = Gauge('enviro_pm_2u5', 'Particulate Matter of diameter less than 2.5 microns. Measured in micrograms per cubic metre (ug/m3)')
PM10 = Gauge('enviro_pm_10u', 'Particulate Matter of diameter less than 10 microns. Measured in micrograms per cubic metre (ug/m3)')

GAS_RED_HIST = Histogram('enviro_gas_red_hist_ohms', 'Histogram of gas RED measurements', buckets=tuple(range(100_000, 1_500_000 + 1, 100_000)))
GAS_OX_HIST = Histogram('enviro_gas_ox_hist_ohms', 'Histogram of gas OX measurements', buckets=tuple(range(5_000, 100_000 + 1, 5_000)))
GAS_NH3_HIST = Histogram('enviro_gas_nh3_hist_ohms', 'Histogram of gas NH3 measurements', buckets=tuple(range(100_000, 2_000_000 + 1, 100_000)))
PM1_HIST = Histogram('enviro_pm_1u_hist', 'Histogram of Particulate Matter of diameter less than 1 micron', buckets=tuple(range(5, 100 + 1, 5)))
PM25_HIST = Histogram('enviro_pm_2u5_hist', 'Histogram of Particulate Matter of diameter less than 2.5 microns', buckets=tuple(range(5, 100 + 1, 5)))
PM10_HIST = Histogram('enviro_pm_10u_hist', 'Histogram of Particulate Matter of diameter less than 10 microns', buckets=tuple(range(5, 100 + 1, 5)))

# Setup InfluxDB
# You can generate an InfluxDB Token from the Tokens Tab in the InfluxDB Cloud UI
INFLUXDB_URL = os.getenv('INFLUXDB_URL', '')
INFLUXDB_TOKEN = os.getenv('INFLUXDB_TOKEN', '')
INFLUXDB_ORG_ID = os.getenv('INFLUXDB_ORG_ID', '')
INFLUXDB_BUCKET = os.getenv('INFLUXDB_BUCKET', '')
INFLUXDB_SENSOR_LOCATION = os.getenv('INFLUXDB_SENSOR_LOCATION', 'Adelaide')
INFLUXDB_TIME_BETWEEN_POSTS = int(os.getenv('INFLUXDB_TIME_BETWEEN_POSTS', '5'))
influxdb_client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG_ID)
influxdb_api = influxdb_client.write_api(write_options=SYNCHRONOUS)

# Setup Luftdaten
LUFTDATEN_TIME_BETWEEN_POSTS = int(os.getenv('LUFTDATEN_TIME_BETWEEN_POSTS', '30'))

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

def update_weather_sensor(temperature_factor):
    """Update values from the weather sensor"""
    try:
        bme280.update_sensor()  # Note: update once and then read instance properties (avoid multiple updates by individual get_... calls)
        temperature = bme280.temperature
        pressure = bme280.pressure
        humidity = bme280.humidity

        # Tuning factor for compensation. Decrease this number to adjust the
        # temperature down, and increase to adjust up
        if temperature_factor:
            cpu_temps = [get_cpu_temperature()] * 5
            cpu_temp = get_cpu_temperature()
            # Smooth out with some averaging to decrease jitter
            cpu_temps = cpu_temps[1:] + [cpu_temp]
            avg_cpu_temp = sum(cpu_temps) / float(len(cpu_temps))
            temperature = temperature - ((avg_cpu_temp - temperature) / temperature_factor)

        TEMPERATURE.set(temperature)
        PRESSURE.set(pressure * 100)  # hPa to Pa
        HUMIDITY.set(humidity / 100)  # percentage to 0-1 ratio
    except IOError:
        logging.error("Could not get BME280 readings. Resetting i2c.")
        reset_i2c()

def update_light_sensor():
    """Update all light sensor readings"""
    try:
        ltr559.update_sensor()
        light = ltr559.get_lux(passive=True)
        proximity = ltr559.get_proximity(passive=True)
        LIGHT.set(light)
        PROXIMITY.set(proximity)
    except IOError:
        logging.error("Could not get light and proximity readings. Resetting i2c.")
        reset_i2c()

def update_gas_sensor():
    """Update all gas sensor readings"""
    try:
        readings = gas.read_all()
        GAS_RED.set(readings.reducing)
        GAS_RED_HIST.observe(readings.reducing)
        GAS_OX.set(readings.oxidising)
        GAS_OX_HIST.observe(readings.oxidising)
        GAS_NH3.set(readings.nh3)
        GAS_NH3_HIST.observe(readings.nh3)
    except IOError:
        logging.error("Could not get gas readings. Resetting i2c.")
        reset_i2c()

def update_particulate_sensor():
    """Update the particulate matter sensor readings"""
    try:
        pms_data = pms5003.read()
        pm010 = pms_data.pm_ug_per_m3(1.0)
        pm025 = pms_data.pm_ug_per_m3(2.5)
        pm100 = pms_data.pm_ug_per_m3(10)
        PM1.set(pm010)
        PM25.set(pm025)
        PM10.set(pm100)
        PM1_HIST.observe(pm010)
        PM25_HIST.observe(pm025 - pm010)
        PM10_HIST.observe(pm100 - pm025)
    except pmsReadTimeoutError:
        logging.warning("Failed to read PMS5003")
    except IOError:
        logging.error("Could not get particulate matter readings. Resetting i2c.")
        reset_i2c()

def collect_all_data():
    """Collects all the data currently set"""
    return {
        'temperature': TEMPERATURE.collect()[0].samples[0].value,
        'pressure': PRESSURE.collect()[0].samples[0].value,
        'humidity': HUMIDITY.collect()[0].samples[0].value,
        'light': LIGHT.collect()[0].samples[0].value,
        'proximity': PROXIMITY.collect()[0].samples[0].value,
        'gas_red': GAS_RED.collect()[0].samples[0].value,
        'gas_ox': GAS_OX.collect()[0].samples[0].value,
        'gas_nh3': GAS_NH3.collect()[0].samples[0].value,
        'pm1': PM1.collect()[0].samples[0].value,
        'pm25': PM25.collect()[0].samples[0].value,
        'pm10': PM10.collect()[0].samples[0].value
    }

def post_to_influxdb():
    """Post all sensor data to InfluxDB"""
    name = 'enviroplus'
    tag = ['location', 'adelaide']
    while True:
        time.sleep(INFLUXDB_TIME_BETWEEN_POSTS)
        data_points = []
        epoch_time_now = round(time.time())
        sensor_data = collect_all_data()
        for field_name in sensor_data:
            data_points.append(Point('enviroplus').tag('location', INFLUXDB_SENSOR_LOCATION).field(field_name, sensor_data[field_name]))
        try:
            influxdb_api.write(bucket=INFLUXDB_BUCKET, record=data_points)
            if DEBUG:
                logging.info('InfluxDB response: OK')
        except Exception as exception:
            logging.warning('Exception sending to InfluxDB: {}'.format(exception))

def post_to_luftdaten(sensor_uid, time_between_posts):
    """
    Post relevant sensor data to luftdaten.info

    Code from: https://github.com/sepulworld/balena-environ-plus
    """
    def post_pin_values(pin, values):
        return requests.post('https://api.luftdaten.info/v1/push-sensor-data/',
            json={
                "software_version": "enviro-plus 0.0.1",
                "sensordatavalues": [{"value_type": key, "value": val} for key, val in values.items()]
            },
            headers={
                "X-PIN": pin,
                "X-Sensor": sensor_uid,
                "Content-Type": "application/json",
                "cache-control": "no-cache"
            }
        )

    while True:
        time.sleep(time_between_posts)
        try:
            sensor_data = collect_all_data()
            response_pin_1 = post_pin_values("1", {
                "P2": sensor_data['pm25'],
                "P1": sensor_data['pm10']
            })
            response_pin_11 = post_pin_values("11", {
                "temperature": "{:.2f}".format(sensor_data['temperature']),
                "pressure": "{:.2f}".format(sensor_data['pressure']),
                "humidity": "{:.2f}".format(sensor_data['humidity'] * 100)
            })

            if response_pin_1.ok and response_pin_11.ok:
                if DEBUG:
                    logging.info('Luftdaten response: OK')
            else:
                logging.warning('Luftdaten response: Failed')
        except Exception as exception:
            logging.warning('Exception sending to Luftdaten: {}'.format(exception))

def get_serial_number():
    """Get Raspberry Pi serial number to use as LUFTDATEN_SENSOR_UID"""
    with open('/proc/cpuinfo', 'r') as f:
        for line in f:
            if line[0:6] == 'Serial':
                return str(line.split(":")[1].strip())

def str_to_bool(value):
    if value.lower() in {'false', 'f', '0', 'no', 'n'}:
        return False
    elif value.lower() in {'true', 't', '1', 'yes', 'y'}:
        return True
    raise ValueError('{} is not a valid boolean value'.format(value))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-b", "--bind", metavar='ADDRESS', default='0.0.0.0',
        help="Specify alternate bind address [default: 0.0.0.0]")
    parser.add_argument("-p", "--port", metavar='PORT', default=8000, type=int,
        help="Specify alternate port [default: 8000]")
    parser.add_argument("-f", "--temperature-factor", metavar='FACTOR', type=float,
        help="The compensation factor to get better temperature results when the Enviro+ pHAT is too close to the Raspberry Pi board")
    parser.add_argument("-e", "--enviro", metavar='ENVIRO', type=str_to_bool,
        help="Device is an Enviro (not Enviro+) so don't fetch data from gas and particulate sensors as they don't exist")
    parser.add_argument("-d", "--debug", metavar='DEBUG', type=str_to_bool,
        help="Turns on more verbose logging, showing sensor output and post responses [default: false]")
    parser.add_argument("-i", "--influxdb", metavar='INFLUXDB', type=str_to_bool, default='false',
        help="Post sensor data to InfluxDB [default: false]")
    parser.add_argument("-l", "--luftdaten", metavar='LUFTDATEN', type=str_to_bool, default='false',
        help="Post sensor data to Luftdaten [default: false]")
    args = parser.parse_args()

    # Start up the server to expose the metrics.
    start_http_server(addr=args.bind, port=args.port)
    # Generate some requests.

    if args.debug:
        DEBUG = True

    if args.temperature_factor:
        logging.info("Using compensating (factor={}) to account for heat leakage from Raspberry Pi CPU".format(args.temperature_factor))

    if args.influxdb:
        # Post to InfluxDB in another thread
        logging.info("Sensor data will be posted to InfluxDB every {} seconds".format(INFLUXDB_TIME_BETWEEN_POSTS))
        influx_thread = Thread(target=post_to_influxdb)
        influx_thread.start()

    if args.luftdaten:
        # Post to Luftdaten in another thread
        LUFTDATEN_SENSOR_UID = 'raspi-' + get_serial_number()
        logging.info("Sensor data will be posted to Luftdaten every {} seconds for the UID {}".format(LUFTDATEN_TIME_BETWEEN_POSTS, LUFTDATEN_SENSOR_UID))
        luftdaten_thread = Thread(target=post_to_luftdaten, args=(LUFTDATEN_SENSOR_UID, LUFTDATEN_TIME_BETWEEN_POSTS))
        luftdaten_thread.start()

    logging.info("Listening on http://{}:{}".format(args.bind, args.port))

    while True:
        update_weather_sensor(args.temperature_factor)
        update_light_sensor()
        if not args.enviro:
            update_gas_sensor()
            update_particulate_sensor()
        if DEBUG:
            logging.info('Sensor data: {}'.format(collect_all_data()))
