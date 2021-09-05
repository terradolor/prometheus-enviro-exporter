#!/usr/bin/env -S python3 -B
import os
import sys
import time
import logging
import argparse
from sensors.factory import add_sensor_arguments, create_sensors
from exporters import add_exporter_arguments, create_exporters, LOOP_UPDATE_TIME

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

def str_to_bool(value):
    if value.lower() in {'false', 'f', '0', 'no', 'n'}:
        return False
    elif value.lower() in {'true', 't', '1', 'yes', 'y'}:
        return True
    raise ValueError('{} is not a valid boolean value'.format(value))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Prometheus exporter for Pimoroni Enviro boards",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("-e", "--enviro", metavar='ENVIRO', type=str_to_bool, default='false',
        help="Device is an Enviro (not Enviro+) so don't fetch data from gas and PM sensors as they don't exist")
    parser.add_argument("--update-period", metavar='PERIOD_SECONDS', type=float, default=5,
        help="Limit update rate of sensor values to defined period in seconds.")
    parser.add_argument("-d", "--debug", metavar='DEBUG', type=str_to_bool, default='false',
        help="Turns on more verbose logging, showing sensor output and post responses")
    add_sensor_arguments(parser.add_argument_group('Sensors', 'Setup of sensors providing values to exporters'))
    add_exporter_arguments(parser.add_argument_group('Exporters', 'Setup how sensor values are published'))
    args = parser.parse_args()

    try:  # last-resort error logging (applied after successful (or failing) parsing of cmd-line args)
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

        sensor = create_sensors(args, args.enviro)
        exporter_fn = create_exporters(args, args.enviro)

        # TODO Enabled rate limiting is causing that values reported by Prometheus HTTP server or posted to Luftdaten/InfluxDB are older.
        #   In worst case by update_time + update_period, instead of just update_time when loop is running at max speed.
        #   Investigate reading sensor values on demand after http Prometheus request and/or posting right when sensor values are acquired.
        rate_limiter = create_loop_rate_limiter(args.update_period)
        logging.info('Starting sensor reading loop. Press Ctrl+C to exit!')
        while True:
            update_start = rate_limiter.now()

            values = {}
            sensor_error = not sensor.update(values)
            exporter_fn(values, sensor_error=sensor_error)
            logging.debug('Sensor data: %s', values)

            update_end = rate_limiter.iteration_end()
            LOOP_UPDATE_TIME.inc(update_end - update_start)  # TODO delegate this hardcoded functionality to exporters; TODO include self-update time
            rate_limiter.sleep()
    except:
        logging.exception('Unhandled exception', exc_info=sys.exc_info())
        raise
