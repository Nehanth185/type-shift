// secrets.example.h - TEMPLATE. Copy this file to secrets.h (same folder) and fill it in.
// secrets.h is git-ignored so your Wi-Fi password never gets committed.
// If secrets.h is missing the firmware still compiles using these placeholders (and warns).
#pragma once

#define WIFI_SSID     "YOUR_WIFI_NAME"      // 2.4 GHz network (ESP32 cannot join 5 GHz-only)
#define WIFI_PASSWORD "YOUR_WIFI_PASSWORD"

// Laptop running server/ (python server.py). Find the laptop IP:
//   Windows: ipconfig  | macOS: ipconfig getifaddr en0  | Linux: hostname -I
// Phone hotspot works too: join laptop + glasses to the same hotspot.
#define SERVER_URL    "http://192.168.1.50:8000"

// Must match DEVICE_TOKEN on the server (blank on both = no check).
#define DEVICE_TOKEN  ""
