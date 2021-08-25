from abc import ABC, abstractmethod

class Sensor(ABC):
    """Sensor interface."""

    @abstractmethod
    def update(self, values):
        """Update provided 'values' mapping type with values and return True on success or False if some update failed and is missing."""

class SensorCollection(Sensor):
    def __init__(self, sensors):
        super().__init__()
        self._sensors = sensors

    def update(self, values):
        return all(sensor.update(values) for sensor in self._sensors)
