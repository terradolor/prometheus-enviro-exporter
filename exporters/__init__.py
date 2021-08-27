from functools import partial
import logging
import os
from threading import Thread
import time
from prometheus_client import start_http_server, Counter, Gauge, Histogram
# Note: code below contains additional imports called only when feature is enabled

# TODO metric declarations should be based on enabled sensors => not hardcoded, not global
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

def update_prometheus_metrics(enviro, values, sensor_error=False):
    # TODO update metric values atomically (report on http whole set, not mix old/new)
    # (create own registry serving one defined values dict, see https://github.com/prometheus/client_python#custom-collectors)

    if sensor_error:
        ERROR_COUNTER.inc()

    TEMPERATURE.set(values['temperature_celsius'])
    PRESSURE.set(values['pressure_pascals'])
    HUMIDITY.set(values['relative_humidity'])
    LIGHT.set(values['light_lux'])
    PROXIMITY.set(values['proximity_raw'])
    if not enviro:
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

    # TODO update LOOP_UPDATE_TIME ... for now it is updated externally in some hardcoded code

def _collect_all_data():
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

def _post_loop_to_influxdb(influxdb_api, create_point, time_between_posts, bucket):
    """Post all sensor data to InfluxDB"""
    while True:
        time.sleep(time_between_posts)
        data_points = [ create_point().field(name, value) for name, value in _collect_all_data().items() ]
        try:
            influxdb_api.write(bucket=bucket, record=data_points)
            logging.debug('InfluxDB response: OK')
        except Exception as exception:
            logging.error('Exception sending to InfluxDB: {}'.format(exception))

def _post_loop_to_luftdaten(sensor_uid, time_between_posts):
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
            sensor_data = _collect_all_data()
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

def _get_serial_number():
    """Get Raspberry Pi serial number"""
    with open('/proc/cpuinfo', 'r') as f:
        for line in f:
            if line[0:6] == 'Serial':
                return str(line.split(":")[1].strip())

def _str_to_bool(value):
    if value.lower() in {'false', 'f', '0', 'no', 'n'}:
        return False
    elif value.lower() in {'true', 't', '1', 'yes', 'y'}:
        return True
    raise ValueError('{} is not a valid boolean value'.format(value))

def add_exporter_arguments(parser):
    parser.add_argument('--prometheus-port', metavar='PORT', default=9848, type=int,
        help='Port of the Prometheus exporter HTTP server.')
    parser.add_argument('--prometheus-ip', metavar='IP', default='0.0.0.0',
        help='IP address where the Prometheus exporter HTTP server should be available. By default bind on all available network interfaces.')
    parser.add_argument("-i", "--influxdb", metavar='INFLUXDB', type=_str_to_bool, default='false',
        help="Post sensor data to InfluxDB")
    parser.add_argument("-l", "--luftdaten", metavar='LUFTDATEN', type=_str_to_bool, default='false',
        help="Post sensor data to Luftdaten")

def create_exporters(args, enviro=False):
    """
    Creates exporters from parsed arguments and starts exports.

    Returns:
        Function accepting mapping type with name and value pairs of sensor values.
    """
    # TODO replace InfluxDB code with standalone exporter not related to prometheus exporter or metrics
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

        sensor_location = os.getenv('INFLUXDB_SENSOR_LOCATION', 'Adelaide')
        def create_point():
            return Point('enviro').tag('location', sensor_location)

        influx_thread = Thread(target=_post_loop_to_influxdb, args=(
            influxdb_api, create_point,
            int(os.getenv('INFLUXDB_TIME_BETWEEN_POSTS', '5')),
            os.getenv('INFLUXDB_BUCKET', ''),
        ))
        influx_thread.start()

    # TODO replace Luftdaten code with standalone exporter not related to prometheus exporter or metrics
    if args.luftdaten:
        LUFTDATEN_TIME_BETWEEN_POSTS = int(os.getenv('LUFTDATEN_TIME_BETWEEN_POSTS', '30'))
        LUFTDATEN_SENSOR_UID = 'raspi-' + _get_serial_number()
        logging.info("Sensor data will be posted to Luftdaten every {} seconds for the UID {}".format(LUFTDATEN_TIME_BETWEEN_POSTS, LUFTDATEN_SENSOR_UID))
        luftdaten_thread = Thread(target=_post_loop_to_luftdaten, args=(LUFTDATEN_SENSOR_UID, LUFTDATEN_TIME_BETWEEN_POSTS))
        luftdaten_thread.start()

    logging.info("Prometheus exporter listening on http://{}:{}".format(args.prometheus_ip, args.prometheus_port))
    start_http_server(addr=args.prometheus_ip, port=args.prometheus_port)

    return partial(update_prometheus_metrics, enviro)
