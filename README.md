# prometheus-enviro-exporter

Table of Contents

* [Features](#features)
* [Setup](#setup)
  * [Prerequisites](#prerequisites)
  * [Installation](#installation)
* [External Components](#external-components)
  * [Prometheus Server](#prometheus-server)
  * [Visualization](#visualization)
* [Acknowledgements](#acknowledgements)

## Features

* Reading values from Raspberry Pi [Pimoroni Enviro or Enviro+ (with PM)](https://shop.pimoroni.com/products/enviro) sensor
  * All sensors are supported except: MEMS microphone, additional input of ADS1015 analog to digital converter (ADC Enviro+ board pin)
  * Display is not used and free for other applications
* Exposing values as a Prometheus exporter (creates a HTTP server)
* Provided systemd service file for easier integration as a system daemon
* Configurable refresh rate (e.g. to reduce CPU load)
* Optional posting of values also to Luftdaten or to InfluxDB

## Setup

We will be installing system prerequisites of Enviro boards and then installing this Python-based project.

But you most likely also need other components not provided by this project, like the Prometheus server and some way how to visualize gathered data. See section [External Components](#external-components).

### Prerequisites

* Python3 with PIP
* installed system prerequisites of Pimoroni enviroplus-python library, including system reconfiguration
  * follow [pimoroni/enviroplus-python](https://github.com/pimoroni/enviroplus-python/#installing)
  * or use installation script from this project: `scripts/install_enviro_prerequisites`

Note: exporter is tested with [Raspberry Pi OS](https://www.raspberrypi.org/software/), but as long as you provide access to sensor devices and system dependencies of Python libraries then feel free to use Debian/LXC/LXD/Docker/...

### Installation

We're going to run the exporter as the user `enviro` in the directory `/home/enviro`. Adjust values as you wish or reuse existing user, but it is recommended to use a separate user for security reasons.

1. (as root) Create the user with access to sensor devices

    ```sh
    useradd -m -G dialout,i2c,gpio enviro
    ```

2. (as a user, switched by su from root) Clone and install the exporter

    ```sh
    su - enviro
    git clone https://github.com/terradolor/prometheus-enviro-exporter.git
    cd prometheus-enviro-exporter
    pip3 install --user -r requirements.txt
    exit
    ```

    * Tune setup by editing `systemd/prometheus-enviro-exporter.service`
      * If you use different user, group or directory ...
      * If you are an Enviro user or you would like to change parameters then adjust `ExecStart`. Call `python3 prometheus-enviro-exporter.py --help` to list all available arguments.

3. (as root) Install and start systemd service

    ```sh
    systemctl enable /home/enviro/prometheus-enviro-exporter/systemd/prometheus-enviro-exporter.service
    systemctl start prometheus-enviro-exporter
    systemctl status prometheus-enviro-exporter
    ```

    * Status should be reporting no errors or failures
    * Also exported values are available in HTTP browser after specifying address `http://<RPI_IP_address>:9848/metrics`

## External Components

Discussion of other components required for displaying long-term graphs ...

### Prometheus Server

Purpose of the Prometheus server is to periodically scrape this exporter by reading values from exposed HTTP server, saving the data and providing API for visualization.

Note: It is probably not the best idea to save Prometheus server data to RPI SD card because IO traffic might reduce SD card lifetime.
But feel free to use it as a test and later relocate the server storage to another device or relocate the entire server.

Prometheus installation:

* [Debian Wiki](https://wiki.debian.org/Prometheus)
* [Prometheus documentation](https://prometheus.io/docs/prometheus/latest/installation/)

To enable scraping of this exporter instance edit `/etc/prometheus/prometheus.yml` and add IP address and port of the exporter to `targets` list.

E.g. create a new `job_name: enviro` in `scrape_configs` section as in the abbreviated example below (last three lines).

```yaml
...
scrape_configs:
  ...
  - job_name: node
    static_configs:
    - targets: ['localhost:9100']
  - job_name: enviro
    static_configs:
    - targets: ['<RPI_IP_address>:9848']
```

Note: List RPI IP address by calling `ip addr show`. Or use a domain name instead of an IP if you have one. Or simply use `localhost` if server is running on the same machine as the exporter.

### Visualization

Visualization of Enviro exporter values from Prometheus server DB:

* [Prometheus built-in graph browser](https://prometheus.io/docs/visualization/browser/) for manual queries
  * Check [prometheus-enviro-consoles](https://github.com/terradolor/prometheus-enviro-consoles) for examples of queries related to this exporter
* [Prometheus Consoles](https://prometheus.io/docs/visualization/consoles/)
  * Reference consoles are available in [prometheus-enviro-consoles](https://github.com/terradolor/prometheus-enviro-consoles)
* [Grafana](https://grafana.com/) or any other visualization service with Prometheus support
  * Note: Currently there are no predefined Grafana templates visualizing values generated by this exporter. So you have to create your own and share it with the community if you like.

## Docker

There is a Dockerfile available if you'd like to run as a docker container.

1. Build: `docker build -t prometheus-enviro-exporter .`
2. Run: `docker run -d prometheus-enviro-exporter -d -p 9848:9848 --device=/dev/i2c-1 --device=/dev/gpiomem --device=/dev/ttyAMA0 prometheus-enviro-exporter`

## Acknowledgements

This project was originally forked from [tijmenvandenbrink/enviroplus_exporter](https://github.com/tijmenvandenbrink/enviroplus_exporter) but code was significantly rewritten:

* metrics have different names and values (so it is not compatible with the original)
* new metrics are added
* new features like reduced and constant refresh rate
* changes in algorithms and fixes

Code is also using following projects:

* [pimoroni/enviroplus-python](https://github.com/pimoroni/enviroplus-python)
* [Prometheus](https://prometheus.io/)
