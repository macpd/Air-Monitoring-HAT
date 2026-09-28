#!/usr/bin/env python3

import math
import logging

#
# Attempt to Caluculate AQI from Data
# https://forum.airnowtech.org/t/the-aqi-equation/169
#

def f_estimateAQI(collection):

    logger = logging.getLogger("f_estimateAQI")

    _pm25_table = {"Good": {"cl": 0, "ch": 12,
                            "aqil": 0, "aqih": 50},
                   "Moderate": {"cl": 12, "ch": 35,
                                "aqil": 51, "aqih": 100},
                   "Unhealthy for Sensitive Groups": {"cl": 35, "ch": 55,
                                                      "aqil": 101, "aqih": 150},
                   "Unhealthy": {"cl": 55, "ch": 150,
                                 "aqil": 150, "aqih": 250},
                   "Very Unhealthy": {"cl": 150, "ch": 250,
                                      "aqil": 201, "aqih": 300},
                   "Hazardous": {"cl": 250, "ch": 500,
                                 "aqil": 301, "aqih": 500}}

    pm25_rounded = round(collection.pm25_cf1)

    eaqi = None
    eaqi_honorrific = None

    if pm25_rounded >= 500:
        eaqi = 501
        eaqi_honorrific = "Ludicrous"

    for honorrific, table in _pm25_table.items():

        if pm25_rounded >= table["cl"] and pm25_rounded < table["ch"]:
            # use
            eaqi_honorrific = honorrific

            logger.debug("Found PM2 in Range for {}".format(honorrific))

            left = (table["aqih"] - table["aqil"]) / (table["ch"] - table["cl"])
            right = (collection.pm25_cf1 - table["cl"])
            eaqi = left * right + table["aqil"]

            break
    if not eaqi:
      logging.error("eaqi not set. pm25_rounded: %s", pm25_rounded)

    return eaqi, eaqi_honorrific


