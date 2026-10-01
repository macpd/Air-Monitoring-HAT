#!/usr/bin/env python3

# Script to Read and Display the Results of a Check
# Designed to be put in a cron

import argparse
import logging
import json
import time
from os import path
import sys
from contextlib import contextmanager

from prometheus_client import start_http_server, Gauge
from serial import SerialException

import pms_a003
from pms_a003 import Sensor
from oled_091 import SSD1306
from time import sleep
from aqi import f_estimateAQI

DIR_PATH = path.abspath(path.dirname(__file__))
DefaultFont = path.join(DIR_PATH, "Fonts/GothamLight.ttf")
DEFAULT_EXIT_MESSAGE = "Monitoring inactive"
DEFAULT_SENSOR_DEV = "/dev/ttyS0"

threshold_moderate = 13
threshold_high = 36

class GenericSensorReadError(Exception):
  pass

GAUGE_NAMESPACE = "sensor"
GAUGE_LABEL = "sensor"

GAUGE_PM1_0_CURRENT_ATMOSPHERE = Gauge("pm1_0_current_atmosphere", "PM1.0 μm/m^3 Current Atmosphere", namespace=GAUGE_NAMESPACE, labelnames=[GAUGE_LABEL])
GAUGE_PM2_5_CURRENT_ATMOSPHERE = Gauge("pm2_5_current_atmosphere", "PM2.5 μm/m^3 Current Atmosphere", namespace=GAUGE_NAMESPACE, labelnames=[GAUGE_LABEL])
GAUGE_PM10_CURRENT_ATMOSPHERE  = Gauge("pm10_current_atmosphere", "PM10 μm/m^3 Current Atmosphere", namespace=GAUGE_NAMESPACE, labelnames=[GAUGE_LABEL])
GAUGE_PM1_0_STANDARD_ATMOSPHERE = Gauge("pm1_0_standard_atmosphere", "PM1.0 μm/m^3 Standard Atmosphere", namespace=GAUGE_NAMESPACE, labelnames=[GAUGE_LABEL])
GAUGE_PM2_5_STANDARD_ATMOSPHERE = Gauge("pm2_5_standard_atmosphere", "PM2.5 μm/m^3 Standard Atmosphere", namespace=GAUGE_NAMESPACE, labelnames=[GAUGE_LABEL])
GAUGE_PM10_STANDARD_ATMOSPHERE  = Gauge("pm10_standard_atmosphere", "PM10 μm/m^3 Standard Atmosphere", namespace=GAUGE_NAMESPACE, labelnames=[GAUGE_LABEL])
GAUGE_GR03UM  = Gauge("gr03um", "Particles > 0.3 μm per 0.1L air", namespace=GAUGE_NAMESPACE, labelnames=[GAUGE_LABEL])
GAUGE_GR05UM  = Gauge("gr05um", "Particles > 0.5 μm per 0.1L air", namespace=GAUGE_NAMESPACE, labelnames=[GAUGE_LABEL])
GAUGE_GR10UM  = Gauge("gr10um", "Particles > 1.0 μm per 0.1L air", namespace=GAUGE_NAMESPACE, labelnames=[GAUGE_LABEL])
GAUGE_GR25UM  = Gauge("gr25um", "Particles > 2.5 μm per 0.1L air", namespace=GAUGE_NAMESPACE, labelnames=[GAUGE_LABEL])
GAUGE_GR50UM  = Gauge("gr50um", "Particles > 5.0 μm per 0.1L air", namespace=GAUGE_NAMESPACE, labelnames=[GAUGE_LABEL])
GAUGE_GR100UM = Gauge("gr100um", "Particles > 10 μm per 0.1L air", namespace=GAUGE_NAMESPACE, labelnames=[GAUGE_LABEL])
GAUGE_EAQI = Gauge("eaqi", "Estimated Air Quality Index", namespace=GAUGE_NAMESPACE, labelnames=[GAUGE_LABEL])
#GUAGE_EAQI_HONORIFIC = Gauge("eaqi_honorific", "Estimated Air Quality Index Honorific", namespace=GAUGE_NAMESPACE)
#GUAGE_EAQI_LABEL = Gauge("eaqi_label", "Estimated Air Quality Index Label", namespace=GAUGE_NAMESPACE)


@contextmanager
def air_monitor_hat_connection(port, baudrate=9600):
    logging.info("Connecting to air monitoring sensor on port %s (baudrate: %s", port, baudrate)
    air_mon = Sensor()
    air_mon.connect_hat(port=port, baudrate=baudrate)
    logging.debug("Successfully connected to air monitoring sensor")
    yield air_mon
    air_mon.disconnect_hat()

@contextmanager
def oled_display_bootstrapper(exit_message=DEFAULT_EXIT_MESSAGE):
  display = SSD1306()
  display.PrintText("  Waiting....", FontSize=14)
  display.ShowImage()
  try:
    yield display
    displya.NoDisplay()
    display.ShowImage()
  except (Exception, KeyboardInterrupt):
    display.PrintText(exit_message, FontSize=12)
    display.ShowImage()
    raise

def collect_data(air_mon, max=5):
    values = None
    try:
        values = air_mon.read()
    except SerialException as se:
        logger.exception("Serial Exception found when requesting Data: {}".format(se))
        raise
    except pms_a003.SensorException as sensor_exception:
        logger.exception("Sensor Exception : {}".format(sensor_exception))
        raise
    except Exception as gen_err:
        logger.exception("General Error: {} {}".format(type(gen_err), gen_err))
        raise

    return values



def print_to_oled(oled_display, info):
  oled_display.PrintText("PM1.0= {:2d}".format(info['data']['pm1_0']), cords=(2, 2), FontSize=10)
  oled_display.PrintText("PM2.5= {:2d}".format(info['data']['pm2_5']), cords=(65, 2), FontSize=10)
  oled_display.PrintText("AQI= {:.2f}".format(info['eaqi']), cords=(25, 20), FontSize=13)
  oled_display.ShowImage()


def get_sensor_data_and_aqi(air_mon):
    info = dict(okay=False, data={})

    try:
      values = collect_data(air_mon)

      eaqi, eaqi_honorific = f_estimateAQI(values)
      info["eaqi"] = eaqi
      info["eaqi_honorific"] = eaqi_honorific

      info["data"] = dict(pm1_0=values.pm10_cf1,
                         pm2_5=values.pm25_cf1,
                         pm10=values.pm100_cf1,
                         pm1_0_std=values.pm10_std,
                         pm2_5_std=values.pm25_std,
                         pm10_std=values.pm100_std,
                         gr03um=values.gr03um,
                         gr05um=values.gr05um,
                         gr10um=values.gr10um,
                         gr25um=values.gr25um,
                         gr50um=values.gr50um,
                         gr100um=values.gr100um)

    except Exception as e:
      logger.exception("Error Reading From Sensor : {}".format(e))
      msg = "Unknown - I don't know what has happened"
      raise GenericSensorReadError(msg) from e

    logger.debug(info)

    if info["data"]["pm2_5"] > threshold_high:
        info['label'] = 'Critical'
    elif info["data"]["pm2_5"] > threshold_moderate:
        info['label'] = 'Warning'
    else:
        info['label'] = 'OK'

    return info


def print_message_to_stdout(info):
    message = "{label} - Air Quality {eaqi_honorific} ({eaqi:.2f})".format(**info)

    perf_data = " ".join(["{}={}".format(k, v) for k, v in info["data"].items()])

    print("{} | {}".format(message, perf_data))


def do_air_monitoring(oled_display, air_mon, metric_label):
  logging.info("Starting value read loop")
  oled_display.PrintText("  Waiting....", FontSize=14)
  oled_display.ShowImage()

  while True:
    info = get_sensor_data_and_aqi(air_mon)
    print_to_oled(oled_display=oled_display, info=info)
    data = info['data']
    GAUGE_PM1_0_CURRENT_ATMOSPHERE.set(data['pm1_0'], metric_label)
    GAUGE_PM2_5_CURRENT_ATMOSPHERE.set(data['pm2_5'], metric_label)
    GAUGE_PM10_CURRENT_ATMOSPHERE.set(data['pm10'], metric_label)
    GAUGE_PM1_0_STANDARD_ATMOSPHERE.set(data['pm1_0_std'], metric_label)
    GAUGE_PM2_5_STANDARD_ATMOSPHERE.set(data['pm2_5_std'], metric_label)
    GAUGE_PM10_STANDARD_ATMOSPHERE.set(data['pm10_std'], metric_label)
    GAUGE_GR03UM.set(data['gr03um'], metric_label)
    GAUGE_GR05UM.set(data['gr05um'], metric_label)
    GAUGE_GR10UM.set(data['gr10um'], metric_label)
    GAUGE_GR25UM.set(data['gr25um'], metric_label)
    GAUGE_GR50UM.set(data['gr50um'], metric_label)
    GAUGE_GR100UM.set(data['gr100um'], metric_label)
    GAUGE_EAQI.set(info['eaqi'], metric_label)
    if args.print_to_stdout:
      print_message_to_stdout(info)

if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument("-v", "--verbose", action="append_const", help="Verbosity Controls",
                        const=1, default=[])

    parser.add_argument("--print-to-stdout", action="store_true", help="Print AQI and other read and calculated values to STDOUT")
    parser.add_argument("-d", "--dev", help="Sensor device path", default=DEFAULT_SENSOR_DEV)

    parser.add_argument("-p", "--prometheus-port", help="port for prometheus",
                        type=int,
                        default=8000)
    parser.add_arguemnt("-n", "--sensor-name",
                        help="name of sensor added to metrics sensor label",
                        type=str)
    # parser.add_argument("-j", "--json", help="JSON, Write Out", default=None)
    # parser.add_argument("-n", "--nrpe", help="NRPE Write out", default=False, action="store_true")

    args = parser.parse_args()

    VERBOSE = len(args.verbose)

    if VERBOSE == 0:
        logging.basicConfig(level=logging.ERROR)
    elif VERBOSE == 1:
        logging.basicConfig(level=logging.WARNING)
    elif VERBOSE == 2:
        logging.basicConfig(level=logging.INFO)
    elif VERBOSE > 2:
        logging.basicConfig(level=logging.DEBUG)

    logger = logging.getLogger()
    logger.info("Running rad_loop.py")

    logger.info("Starting prometheus client on port %d", args.prometheus_port)
    start_http_server(args.prometheus_port)
    #  oled_display = SSD1306()
    with oled_display_bootstrapper() as oled_display, air_monitor_hat_connection(port=args.dev) as air_mon:
        do_air_monitoring(oled_display, air_mon, metric_label=args.sensor_name)
