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

threshold_moderate = 13
threshold_high = 36

class GenericSensorReadError(Exception):
  pass

GAUGE_PM1_0 = Gauge("pm1_0", "PM1.0")
GAUGE_PM2_5 = Gauge("pm2_5", "PM2.5")
GAUGE_PM10 = Gauge("pm10", "PM10")
GAUGE_EAQI = Gauge("eaqi", "Estimated Air Quality Index")
#GUAGE_EAQI_HONORIFIC = Gauge("eaqi_honorific", "Estimated Air Quality Index Honorific")
#GUAGE_EAQI_LABEL = Gauge("eaqi_label", "Estimated Air Quality Index Label")

@contextmanager
def air_monitor_hat_connection(port="/dev/ttyS0", baudrate=9600):
    air_mon = Sensor()
    air_mon.connect_hat(port=port, baudrate=baudrate)
    yield air_mon
    air_mon.disconnect_hat()

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
                         gr03um=values.gr03um,
                         gr10um=values.gr10um,
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

    info['message'] = "{label} - Air Quality {eaqi_honorific} ({eaqi:.2f})".format(**info)

    return info


def info_print_loop(oled_display, air_mon):
  # oled_display.DirImage(path.join(DIR_PATH, "Images/SB.png"))
  # oled_display.DrawRect()
  # oled_display.ShowImage()
  # sleep(1)
  oled_display.PrintText("  Waiting....", FontSize=14)
  oled_display.ShowImage()

  while True:
    info = get_sensor_data_and_aqi(air_mon)
    print_to_oled(oled_display=oled_display, info=info)
    GAUGE_PM1_0.set(info['data']['pm1_0'])
    GAUGE_PM2_5.set(info['data']['pm2_5'])
    GAUGE_PM10.set(info['data']['pm10'])
    GAUGE_EAQI.set(info['eaqi'])

    perf_data = " ".join(["{}={}".format(k, v) for k, v in info["data"].items()])

    print("{} | {}".format(info['message'], perf_data))


if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument("-v", "--verbose", action="append_const", help="Verbosity Controls",
                        const=1, default=[])

    parser.add_argument("-p", "--prometheus-port", help="port for prometheus",
                        type=int,
                        default=8000)
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

    start_http_server(args.prometheus_port)
    oled_display = SSD1306()
    with air_monitor_hat_connection() as air_mon:
        info_print_loop(oled_display, air_mon)
