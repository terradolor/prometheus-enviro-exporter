from .core import SensorCollection
from .hw import *

def create_sensors(enviro=False, temperature_factor=None):
    """Create sensors and return Sensor instance."""
    sensors = [
        BME280Sensor(temperature_factor),
        LTR559Sensor()
    ]
    if not enviro:
        sensors.append(MICS6814Sensor())
        sensors.append(PMS5003Sensor())

    if len(sensors) == 1:
        return sensors[0]
    return SensorCollection(sensors)
