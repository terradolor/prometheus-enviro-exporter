#!/usr/bin/env python3
import os
import time
import logging
import argparse
from threading import Thread
from sensors.factory import create_sensors
# Note: code below contains additional imports called only when feature is enabled

from prometheus_client import start_http_server, Counter, Gauge, Histogram

TEMPERATURE = Gauge('enviro_temperature_celsius','Temperature')
PRESSURE = Gauge('enviro_pressure_pascals','Pressure')
HUMIDITY = Gauge('enviro_relative_humidity','Relative humidity')
LIGHT = Gauge('enviro_light_lux', 'Ambient light level')
PROXIMITY = Gauge('enviro_proximity_raw', 'Raw proximity value, with larger numbers being closer and vice versa')
# TODO don't report gas and PM on http prometheus exporter if we are in Enviro device mode
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

LOOP_UPDATE_TIME = Counter('enviro_update_time_seconds', 'Cumulative time spent in sensor values update.')
ERROR_COUNTER = Counter('enviro_errors', 'Counter of processing errors. E.g. failed sensor value updates.')

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

class LoopRateLimiter:
    """Class maintaining defined average duration of iterations inside a loop."""

    def __init__(self, period):
        self._period = period
        self._time_ref = self.now()  # initialized reference time, start of first iteration
        self._sleep_time = 0

    def now(self):
        """Current monothionic time. Might be used for calculating duration, e.g. against return value of iteration_end()."""
        return time.perf_counter()

    def iteration_end(self):
        """Called when iteration is finished. Returns end time."""
        time_ref = self._time_ref + self._period  # get ideal time where we should be at the end of iteration
        time_real = self.now()  # real time at the end of iteration (active part)
        self._sleep_time = time_ref - time_real  # how much we need to sleep to compensate between reference and real time
        self._time_ref = max(time_ref, time_real - 100 * self._period)  # limit max lag behind real time (if processing is too long) => faster recovery
        return time_real

    def sleep(self):
        """Sleep to compensate iteration duration as calculated in iteration_end()."""
        if self._sleep_time > 0:
            time.sleep(self._sleep_time)

    def end_sleep(self):
        """Mark iteration end and sleep."""
        self.iteration_end()
        self.sleep()

class NoLoopRateLimiter:
    """Implementation with no rate limiting."""

    def now(self):
        return time.perf_counter()

    def iteration_end(self):
        return self.now()

    def sleep(self):
        pass

    def end_sleep(self):
        pass

def create_loop_rate_limiter(period):
    if period > 0:
        return LoopRateLimiter(period)
    else:
        return NoLoopRateLimiter()

def post_loop_to_influxdb(influxdb_api, time_between_posts, bucket, sensor_location):
    """Post all sensor data to InfluxDB"""
    while True:
        time.sleep(time_between_posts)
        data_points = [
            Point('enviro').tag('location', sensor_location).field(name, value)
                for name, value in collect_all_data().items()
        ]
        try:
            influxdb_api.write(bucket=bucket, record=data_points)
            logging.debug('InfluxDB response: OK')
        except Exception as exception:
            logging.error('Exception sending to InfluxDB: {}'.format(exception))

def post_loop_to_luftdaten(sensor_uid, time_between_posts):
    """
    Post relevant sensor data to luftdaten.info

    Code from: https://github.com/sepulworld/balena-environ-plus
    """
    import requests

    def post_pin_values(pin, values):
        return requests.post('https://api.luftdaten.info/v1/push-sensor-data/',
            json={
                "software_version": "prometheus-enviro-exporter 0.0.1",
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
                logging.debug('Luftdaten response: OK')
            else:
                logging.error('Luftdaten response: Failed')
        except Exception as exception:
            logging.error('Exception sending to Luftdaten: {}'.format(exception))

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
    parser = argparse.ArgumentParser(description="Prometheus exporter for Pimoroni Enviro boards",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("-b", "--bind", metavar='ADDRESS', default='0.0.0.0',
        help="Specify alternate bind address")
    parser.add_argument("-p", "--port", metavar='PORT', default=9848, type=int,
        help="Specify alternate port")
    parser.add_argument("-e", "--enviro", metavar='ENVIRO', type=str_to_bool, default='false',
        help="Device is an Enviro (not Enviro+) so don't fetch data from gas and PM sensors as they don't exist")
    parser.add_argument("-f", "--temperature-factor", metavar='FACTOR', type=float,
        help="The compensation factor to get better temperature results when the Enviro+ is too close to the Raspberry Pi board. " +
        "Value should be from 0 (no correction) to almost 1 (max heat transfer from CPU and max correction).")
    parser.add_argument("-i", "--influxdb", metavar='INFLUXDB', type=str_to_bool, default='false',
        help="Post sensor data to InfluxDB")
    parser.add_argument("-l", "--luftdaten", metavar='LUFTDATEN', type=str_to_bool, default='false',
        help="Post sensor data to Luftdaten")
    parser.add_argument("--update-period", metavar='PERIOD_SECONDS', type=float, default=5,
        help="Limit update rate of sensor values to defined period in seconds.")
    parser.add_argument("-d", "--debug", metavar='DEBUG', type=str_to_bool, default='false',
        help="Turns on more verbose logging, showing sensor output and post responses")
    args = parser.parse_args()

    logging.basicConfig(
        format='%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s',
        level=logging.INFO,
        handlers=[logging.FileHandler("prometheus-enviro-exporter.log"), logging.StreamHandler()],
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    if args.debug or os.getenv('DEBUG', 'false') == 'true':
        logging.getLogger().setLevel(logging.DEBUG)

    if args.temperature_factor:
        logging.info("Using compensating (factor={}) to account for heat leakage from Raspberry Pi CPU".format(args.temperature_factor))

    if args.influxdb:
        logging.info("Starting InfluxDB client and posting loop")
        from influxdb_client import InfluxDBClient, Point
        from influxdb_client.client.write_api import SYNCHRONOUS
        influxdb_client = InfluxDBClient(
            url=os.getenv('INFLUXDB_URL', ''),
            token=os.getenv('INFLUXDB_TOKEN', ''),  # You can generate an InfluxDB Token from the Tokens Tab in the InfluxDB Cloud UI
            org=os.getenv('INFLUXDB_ORG_ID', '')
        )
        influxdb_api = influxdb_client.write_api(write_options=SYNCHRONOUS)
        influx_thread = Thread(target=post_loop_to_influxdb, args=(
            influxdb_api,
            int(os.getenv('INFLUXDB_TIME_BETWEEN_POSTS', '5')),
            os.getenv('INFLUXDB_BUCKET', ''),
            os.getenv('INFLUXDB_SENSOR_LOCATION', 'Adelaide')
        ))
        influx_thread.start()

    if args.luftdaten:
        LUFTDATEN_TIME_BETWEEN_POSTS = int(os.getenv('LUFTDATEN_TIME_BETWEEN_POSTS', '30'))
        LUFTDATEN_SENSOR_UID = 'raspi-' + get_serial_number()
        logging.info("Sensor data will be posted to Luftdaten every {} seconds for the UID {}".format(LUFTDATEN_TIME_BETWEEN_POSTS, LUFTDATEN_SENSOR_UID))
        luftdaten_thread = Thread(target=post_loop_to_luftdaten, args=(LUFTDATEN_SENSOR_UID, LUFTDATEN_TIME_BETWEEN_POSTS))
        luftdaten_thread.start()

    logging.info("Listening on http://{}:{}".format(args.bind, args.port))
    start_http_server(addr=args.bind, port=args.port)

    sensor = create_sensors(args.enviro, args.temperature_factor)

    logging.info("Starting sensor reading loop. Press Ctrl+C to exit!")

    # TODO Enabled rate limiting is causing that values reported by Prometheus HTTP server or posted to Luftdaten/InfluxDB are older.
    #   In worst case by update_time + update_period, instead of just update_time when loop is running at max speed.
    #   Investigate reading sensor values on demand after http Prometheus request and/or posting right when sensor values are acquired.
    rate_limiter = create_loop_rate_limiter(args.update_period)
    while True:
        update_start = rate_limiter.now()

        values = {}
        if not sensor.update(values):
            ERROR_COUNTER.inc()

        # TODO move this block to some Prometheus post function or object
        # TODO update metric values atomically (report on http whole set, not mix old/new)
        # (create own registry serving one defined values dict, see https://github.com/prometheus/client_python#custom-collectors)
        TEMPERATURE.set(values['temperature_celsius'])
        PRESSURE.set(values['pressure_pascals'])
        HUMIDITY.set(values['relative_humidity'])
        LIGHT.set(values['light_lux'])
        PROXIMITY.set(values['proximity_raw'])
        if not args.enviro:
            GAS_RED.set(values['gas_red_ohms'])
            GAS_OX.set(values['gas_ox_ohms'])
            GAS_NH3.set(values['gas_nh3_ohms'])
            GAS_RED_HIST.observe(values['gas_red_ohms'])
            GAS_OX_HIST.observe(values['gas_ox_ohms'])
            GAS_NH3_HIST.observe(values['gas_nh3_ohms'])
            PM1.set(values['pm_1u'])
            PM25.set(values['pm_2u5'])
            PM10.set(values['pm_10u'])
            PM1_HIST.observe(values['pm_1u'])
            PM25_HIST.observe(values['pm_2u5'])
            PM10_HIST.observe(values['pm_10u'])

        logging.debug('Sensor data: %s', collect_all_data())
        update_end = rate_limiter.iteration_end()
        LOOP_UPDATE_TIME.inc(update_end - update_start)
        rate_limiter.sleep()
