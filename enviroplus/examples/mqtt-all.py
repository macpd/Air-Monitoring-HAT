#!/usr/bin/env python3
"""
Run mqtt broker on localhost: sudo apt-get install mosquitto mosquitto-clients

Example run: python3 mqtt-all.py --broker 192.168.1.164 --topic enviro --username xxx --password xxxx
"""

import colorsys
import argparse
import ssl
import time

import st7735
from bme280 import BME280
from pms5003 import PMS5003, ReadTimeoutError, SerialTimeoutError
from enviroplus.aqi import f_estimateAQI

# from enviroplus import gas

try:
    # Transitional fix for breaking change in LTR559
    from ltr559 import LTR559

    ltr559 = LTR559()
except ImportError:
    import ltr559

import json
from subprocess import PIPE, Popen, check_output

import paho.mqtt.client as mqtt
from fonts.ttf import RobotoMedium as UserFont
from PIL import Image, ImageDraw, ImageFont

try:
    from smbus2 import SMBus
except ImportError:
    from smbus import SMBus


DEFAULT_MQTT_BROKER_IP = "localhost"
DEFAULT_MQTT_BROKER_PORT = 1883
DEFAULT_MQTT_TOPIC = "enviroplus"
DEFAULT_READ_INTERVAL = 5
DEFAULT_TLS_MODE = False
DEFAULT_USERNAME = None
DEFAULT_PASSWORD = None

# Create ST7735 LCD display class
disp = st7735.ST7735(
    port=0,
    cs=0,
    dc="GPIO9",
    backlight="GPIO12",
    rotation=270,
    spi_speed_hz=10000000
)

# Initialize display
disp.begin()

WIDTH = disp.width
HEIGHT = disp.height

# Set up canvas and font
img = Image.new("RGB", (WIDTH, HEIGHT), color=(0, 0, 0))
draw = ImageDraw.Draw(img)
font_size_small = 10
font_size_large = 20
font = ImageFont.truetype(UserFont, font_size_large)
smallfont = ImageFont.truetype(UserFont, font_size_small)
x_offset = 2
y_offset = 2

message = ""

# The position of the top bar
TOP_POS = 25

# Create a values dict to store the data
VARIABLES = ["temperature",
             "pressure",
             "humidity",
             "light",
             "oxidised",
             "reduced",
             "nh3",
             "pm1",
             "pm25",
             "pm10"]

UNITS = ["C",
         "hPa",
         "%",
         "Lux",
         "kO",
         "kO",
         "kO",
         "ug/m3",
         "ug/m3",
         "ug/m3"]

# Define your own warning limits
# The limits definition follows the order of the VARIABLES array
# Example limits explanation for temperature:
# [4,18,28,35] means
# [-273.15 .. 4] -> Dangerously Low
# (4 .. 18]      -> Low
# (18 .. 28]     -> Normal
# (28 .. 35]     -> High
# (35 .. MAX]    -> Dangerously High
# DISCLAIMER: The limits provided here are just examples and come
# with NO WARRANTY. The authors of this example code claim
# NO RESPONSIBILITY if reliance on the following values or this
# code in general leads to ANY DAMAGES or DEATH.
LIMITS = [[4, 18, 28, 35],
          [250, 650, 1013.25, 1015],
          [20, 30, 60, 70],
          [-1, -1, 30000, 100000],
          [-1, -1, 40, 50],
          [-1, -1, 450, 550],
          [-1, -1, 200, 300],
          [-1, -1, 50, 100],
          [-1, -1, 50, 100],
          [-1, -1, 50, 100]]

# mqtt callbacks
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("connected OK")
    else:
        print("Bad connection Returned code=", rc)


def on_publish(client, userdata, mid):
    print("mid: " + str(mid))


# Read values from BME280 and return as dict
def read_bme280(bme280):
    # Compensation factor for temperature
    comp_factor = 2.25
    values = {}
    cpu_temp = get_cpu_temperature()
    raw_temp = bme280.get_temperature()  # float
    comp_temp = raw_temp - ((cpu_temp - raw_temp) / comp_factor)
    values["temperature_raw"] = int(raw_temp)
    values["temperature_corrected"] = int(comp_temp)
    values["pressure"] = round(
        int(bme280.get_pressure() * 100), -1
    )  # round to nearest 10
    values["humidity"] = int(bme280.get_humidity())
    # data = gas.read_all()
    # values["oxidised"] = int(data.oxidising / 1000)
    # values["reduced"] = int(data.reducing / 1000)
    # values["nh3"] = int(data.nh3 / 1000)
    values["lux"] = int(ltr559.get_lux())
    return values


# Read values PMS5003 and return as dict
def read_pms5003(pms5003):
    values = {}
    try:
        values = _read_pms5003(pms5003)
    except ReadTimeoutError:
        pms5003.reset()
        values = _read_pms5003(pms5003)
    return values


def _read_pms5003(pms5003):
    pm_values = pms5003.read()  # int
    values = {}
    values["pm1"] = pm_values.pm_ug_per_m3(1)
    values["pm25"] = pm_values.pm_ug_per_m3(2.5)
    values["pm10"] = pm_values.pm_ug_per_m3(10)
    values['pm1_0_current_atmosphere'] = pm_values.pm_ug_per_m3(1.0)
    values['pm2_5_current_atmosphere'] = pm_values.pm_ug_per_m3(2.5)
    values['pm10_0_current_atmosphere'] = pm_values.pm_ug_per_m3(10)
    values['pm1_0_standard_atmosphere'] = pm_values.pm_ug_per_m3(1.0, atmospheric_environment=True)
    values['pm2_5_standard_atmosphere'] = pm_values.pm_ug_per_m3(2.5, atmospheric_environment=True)
    values['gr03um'] = pm_values.pm_per_1l_air(0.3)
    values['gr05um'] = pm_values.pm_per_1l_air(0.5)
    values['gr10um'] = pm_values.pm_per_1l_air(1.0)
    values['gr25um'] = pm_values.pm_per_1l_air(2.5)
    values['gr50um'] = pm_values.pm_per_1l_air(5)
    values['gr100um'] = pm_values.pm_per_1l_air(10)
    eaqi, eaqi_honorific = f_estimateAQI(values['pm2_5_current_atmosphere'])
    values['eaqi'] = eaqi
    return values

# Get CPU temperature to use for compensation
def get_cpu_temperature():
    process = Popen(
        ["vcgencmd", "measure_temp"], stdout=PIPE, universal_newlines=True
    )
    output, _error = process.communicate()
    return float(output[output.index("=") + 1:output.rindex("'")])


# Get Raspberry Pi serial number to use as ID
def get_serial_number():
    with open("/proc/cpuinfo", "r") as f:
        for line in f:
            if line[0:6] == "Serial":
                return line.split(":")[1].strip()


# Check for Wi-Fi connection
def check_wifi():
    if check_output(["hostname", "-I"]):
        return True
    else:
        return False


# Display Raspberry Pi serial and Wi-Fi status on LCD
def display_status(mqtt_broker):
    # Width and height to calculate text position
    #  WIDTH = disp.width
    #  HEIGHT = disp.height
    # Text settings
    font_size = 12
    font = ImageFont.truetype(UserFont, font_size)

    wifi_status = "connected" if check_wifi() else "disconnected"
    text_colour = (255, 255, 255)
    back_colour = (0, 170, 170) if check_wifi() else (85, 15, 15)
    device_serial_number = get_serial_number()
    message = f"Serial: {device_serial_number}\nWi-Fi: {wifi_status}\nmqtt-broker: {mqtt_broker}"
    img = Image.new("RGB", (WIDTH, HEIGHT), color=(0, 0, 0))
    draw = ImageDraw.Draw(img)
    x1, y1, x2, y2 = draw.textbbox((0,0), message, font=font)
    size_x = x2 - x1
    size_y = y2 - y1
    x = (WIDTH - size_x) / 2
    y = (HEIGHT / 2) - (size_y / 2)
    draw.rectangle((0, 0, 160, 80), back_colour)
    draw.text((x, y), message, font=font, fill=text_colour)
    disp.display(img)


# Displays data and text on the 0.96" LCD
def display_text(values, variable, unit):
    data = values[-1]
    # Scale the values for the variable between 0 and 1
    vmin = min(values)
    vmax = max(values)
    colours = [(v - vmin + 1) / (vmax - vmin + 1) for v in values]
    # Format the variable name and value
    message = f"{variable[:4]}: {data:.1f} {unit}"
    # TODO(macpd): use logging
    print(message)
    draw.rectangle((0, 0, WIDTH, HEIGHT), (255, 255, 255))
    for i in range(len(colours)):
        # Convert the values to colours from red to blue
        colour = (1.0 - colours[i]) * 0.6
        r, g, b = [int(x * 255.0) for x in colorsys.hsv_to_rgb(colour, 1.0, 1.0)]
        # Draw a 1-pixel wide rectangle of colour
        draw.rectangle((i, TOP_POS, i + 1, HEIGHT), (r, g, b))
        # Draw a line graph in black
        line_y = HEIGHT - (TOP_POS + (colours[i] * (HEIGHT - TOP_POS))) + TOP_POS
        draw.rectangle((i, line_y, i + 1, line_y + 1), (0, 0, 0))
    # Write the text at the top in black
    draw.text((0, 0), message, font=font, fill=(0, 0, 0))
    disp.display(img)


def celsius_to_farenheit(c):
    return (c * 1.8) + 32


# Saves the data to be used in the graphs later and prints to the log
def update_data_window(values, data):
    return values[1:] + [data]



def main():
    parser = argparse.ArgumentParser(
        description="Publish enviroplus values over mqtt"
    )
    parser.add_argument(
        "--broker",
        default=DEFAULT_MQTT_BROKER_IP,
        type=str,
        help="mqtt broker IP",
    )
    parser.add_argument(
        "--port",
        default=DEFAULT_MQTT_BROKER_PORT,
        type=int,
        help="mqtt broker port",
    )
    parser.add_argument(
        "--topic", default=DEFAULT_MQTT_TOPIC, type=str, help="mqtt topic"
    )
    parser.add_argument(
        "--interval",
        default=DEFAULT_READ_INTERVAL,
        type=int,
        help="the read interval in seconds",
    )
    parser.add_argument(
        "--tls",
        default=DEFAULT_TLS_MODE,
        action="store_true",
        help="enable TLS"
    )
    parser.add_argument(
        "--username",
        default=DEFAULT_USERNAME,
        type=str,
        help="mqtt username"
    )
    parser.add_argument(
        "--password",
        default=DEFAULT_PASSWORD,
        type=str,
        help="mqtt password"
    )
    args = parser.parse_args()

    # Raspberry Pi ID
    device_serial_number = get_serial_number()
    device_id = "raspi-" + device_serial_number

    print(
        f"""mqtt-all.py - Reads Enviro plus data and sends over mqtt.

    broker: {args.broker}
    client_id: {device_id}
    port: {args.port}
    topic: {args.topic}
    tls: {args.tls}
    username: {args.username}
    password: {args.password}

    Press Ctrl+C to exit!

    """
    )

    mqtt_client = mqtt.Client(client_id=device_id)
    if args.username and args.password:
        mqtt_client.username_pw_set(args.username, args.password)
    mqtt_client.on_connect = on_connect
    mqtt_client.on_publish = on_publish

    if args.tls is True:
        mqtt_client.tls_set(tls_version=ssl.PROTOCOL_TLSv1_2)

    if args.username is not None:
        mqtt_client.username_pw_set(args.username, password=args.password)

    mqtt_client.connect(args.broker, port=args.port)

    bus = SMBus(1)

    # Create BME280 instance
    bme280 = BME280(i2c_dev=bus)

    # Create LCD instance
    #  disp = st7735.ST7735(
        #  port=0,
        #  cs=0,
        #  dc="GPIO9",
        #  backlight="GPIO12",
        #  rotation=270,
        #  spi_speed_hz=10000000
    #  )

    #  # Initialize display
    #  disp.begin()

    # Try to create PMS5003 instance
    HAS_PMS = False
    try:
        pms5003 = PMS5003()
        _ = pms5003.read()
        HAS_PMS = True
        print("PMS5003 sensor is connected")
    except SerialTimeoutError:
        print("No PMS5003 sensor connected")

    #  disp.begin()

    #  WIDTH = disp.width
    #  HEIGHT = disp.height

    # Set up canvas and font
    #  img = Image.new("RGB", (WIDTH, HEIGHT), color=(0, 0, 0))
    #  draw = ImageDraw.Draw(img)
    #  font_size_small = 10
    #  font_size_large = 20
    #  font = ImageFont.truetype(UserFont, font_size_large)
    #  smallfont = ImageFont.truetype(UserFont, font_size_small)
    #  x_offset = 2
    #  y_offset = 2

    #  message = ""


    # RGB palette for values on the combined screen
    palette = [(0, 0, 255),           # Dangerously Low
               (0, 255, 255),         # Low
               (0, 255, 0),           # Normal
               (255, 255, 0),         # High
               (255, 0, 0)]           # Dangerously High

    temperature = [0] * WIDTH
    pressure = [0] * WIDTH
    humidity = [0] * WIDTH
    light = [0] * WIDTH
    proximity = [0] * WIDTH
    #  oxidised = [0] * WIDTH
    #  reduced = [0] * WIDTH
    #  nh3 = [0] * WIDTH
    pm1 = [0] * WIDTH
    pm25 = [0] * WIDTH
    pm10 = [0] * WIDTH
    aqi = [0] * WIDTH

    values = {}


    # Display Raspberry Pi serial and Wi-Fi status
    print(f"RPi serial: {device_serial_number}")
    wifi_status = "connected" if check_wifi() else "disconnected"
    print(f"Wi-Fi: {wifi_status}\n")
    print(f"MQTT broker IP: {args.broker}")

    # Set an initial update time
    update_time = time.time()

    # Main loop to read data, display, and send over mqtt
    mqtt_client.loop_start()

    # Tuning factor for compensation. Decrease this number to adjust the
    # temperature down, and increase to adjust up
    factor = 2.25

    cpu_temps = [get_cpu_temperature()] * 5

    delay = 0.5  # Debounce the proximity tap
    mode = 10    # The starting mode
    last_page = 0
    while True:
        try:
            values = read_bme280(bme280)
            if HAS_PMS:
                pms_values = read_pms5003(pms5003)
                values.update(pms_values)
            time_since_update = time.time() - update_time
            if time_since_update >= args.interval:
                update_time = time.time()
                values["serial"] = device_serial_number
                print(values)
                mqtt_client.publish(args.topic, json.dumps(values), retain=True)
                #  display_status(disp, args.broker)

            proximity = ltr559.get_proximity()

            cpu_temp = get_cpu_temperature()
            # Smooth out with some averaging to decrease jitter
            cpu_temps = cpu_temps[1:] + [cpu_temp]
            avg_cpu_temp = sum(cpu_temps) / float(len(cpu_temps))
            raw_temp = bme280.get_temperature()
            new_temp = raw_temp - ((avg_cpu_temp - raw_temp) / factor)
            temperature = update_data_window(temperature, new_temp)

            pressure = update_data_window(pressure, bme280.get_pressure())

            humidity = update_data_window(humidity, bme280.get_humidity())

            light = update_data_window(light, ltr559.get_lux() if proximity < 10 else 1)

            if HAS_PMS:
                pm1 = update_data_window(pm1, pms_values['pm1'])
                pm25 = update_data_window(pm25, pms_values['pm25'])
                pm10 = update_data_window(pm10, pms_values['pm10'])
                aqi = update_data_window(aqi, pms_values['eaqi'])

            # If the proximity crosses the threshold, toggle the mode
            if proximity > 1500 and time.time() - last_page > delay:
                mode += 1
                mode %= (len(VARIABLES) + 1)
                last_page = time.time()

            # One mode for each variable
            if mode == 0:
                # variable = "temperature"
                #  unit = "°C"
                #  display_text(temperature, 'temperature', unit)
                unit = "°F"
                temp_f = list(map(celsius_to_farenheit, temperature))
                display_text(temperature, 'temperature', unit)

            if mode == 1:
                # variable = "pressure"
                unit = "hPa"
                display_text(pressure, "pressure", unit)

            if mode == 2:
                # variable = "humidity"
                unit = "%"
                display_text(humidity, "humidity", unit)

            if mode == 3:
                # variable = "light"
                unit = "Lux"
                display_text(light, "light", unit)

            if mode == 4:
                # variable = "pm1"
                unit = "ug/m3"
                if HAS_PMS:
                    display_text(pm1, "PM 1", unit)
                else:
                    display_text(["Not available"], "PM 1", unit)

            if mode == 5:
                # variable = "pm25"
                unit = "ug/m3"
                if HAS_PMS:
                    display_text(pm25, "PM 2.5", unit)
                else:
                    display_text(["Not available"], "PM 2.5", unit)

            if mode == 6:
                # variable = "pm10"
                if HAS_PMS:
                    display_text(pm10, "PM 10", unit)
                else:
                    display_text(["Not available"], "PM 10", unit)

            if mode == 7:
                if HAS_PMS:
                    display_text(aqi, "AQI", '')
                else:
                    display_text(["Not available"], "AQI", '')

            if mode == 8:
                display_status(disp, args.broker)

            if mode == 10:
                draw.rectangle((0, 0, WIDTH, HEIGHT), (0, 0, 0))
                column_count = 2
                num_variables = 8 if HAS_PMS else 4
                row_count = (num_variables / column_count)
                display_values = [
                    ("temperature", list(map(celsius_to_farenheit, temperature)), "F"),
                    ("pressure", pressure, "hPa"),
                    ("humidity", humidity, "%"),
                    ("light", light, "lux"),
                    ]
                if HAS_PMS:
                    display_values.extend([
                        ("pm1", pm1, "ug/m3"),
                        ("pm25", pm25, "ug/m3"),
                        ("pm10", pm10, "ug/m3"),
                        ("AQI", aqi, ""),
                        ])
                for i, vals in enumerate(display_values):
                    x = x_offset + ((WIDTH // column_count) * (i // row_count))
                    y = y_offset + ((HEIGHT / row_count) * (i % row_count))
                    data_val = vals[1][-1]
                    msg = "{variable}: {data_value:.1f} {unit}".format(variable=vals[0][:4], data_value=data_val, unit=vals[2])
                    lim = LIMITS[i]
                    rgb = palette[0]
                    for j in range(len(lim)):
                        if data_val > lim[j]:
                            rgb = palette[j + 1]
                    draw.text((x, y), msg, font=smallfont, fill=rgb)
                disp.display(img)


    # The main loop
        except Exception as e:
          print(e)


if __name__ == "__main__":
    main()
