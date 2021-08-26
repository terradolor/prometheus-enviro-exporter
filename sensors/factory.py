from .core import SensorCollection
from .hw import *

def add_sensor_arguments(parser):
    """Declare argparse arguments for create_sensors"""
    parser.add_argument("-f", "--temperature-factor", metavar='FACTOR', type=float,
        help="The compensation factor to get better temperature results when the Enviro+ is too close to the Raspberry Pi board. " +
        "Value should be from 0 (no correction) to almost 1 (max heat transfer from CPU and max correction).")

def create_sensors(args, enviro=False):
    """Create sensors from parsed arguments and return Sensor instance."""
    sensors = [
        BME280Sensor(args.temperature_factor),
        LTR559Sensor()
    ]
    if not enviro:
        sensors.append(MICS6814Sensor())
        sensors.append(PMS5003Sensor())

    if len(sensors) == 1:
        return sensors[0]
    return SensorCollection(sensors)
