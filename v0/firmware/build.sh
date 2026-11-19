#!/usr/bin/env bash
# Compile (and optionally upload) the glasses firmware with arduino-cli. Same result as the Arduino IDE.
#   bash build.sh              # compile only
#   bash build.sh /dev/ttyACM0 # compile + upload (macOS: /dev/cu.usbmodem*, Windows: COM5)
set -e
cd "$(dirname "$0")"
FQBN="esp32:esp32:XIAO_ESP32S3:PSRAM=opi"
arduino-cli config add board_manager.additional_urls https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json 2>/dev/null || true
arduino-cli core update-index
arduino-cli core install esp32:esp32            # tested with 3.3.12
arduino-cli lib install "Adafruit SSD1306" "Adafruit GFX Library" "ArduinoJson" "OV5640 Auto Focus for ESP32 Camera"   # tested: 2.5.17 / 1.13.0 / 7.4.3 / 0.1.1
arduino-cli compile -b "$FQBN" --build-path build glasses_v0
if [ -n "${1:-}" ]; then arduino-cli upload -b "$FQBN" -p "$1" --input-dir build glasses_v0 && arduino-cli monitor -p "$1" -c baudrate=115200; fi
