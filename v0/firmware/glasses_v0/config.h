// config.h - pins and behaviour for smart-glasses prototype v0
// Board: Seeed Studio XIAO ESP32-S3 Sense (camera board clicked on).
// Pin sources: https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/ (pin map)
//              https://wiki.seeedstudio.com/xiao_esp32s3_camera_usage/   (camera GPIOs)
#pragma once

// ---------------- External parts (see ../../WIRING.md) ----------------
// Pins the Sense board already uses internally (do NOT wire to these):
//   camera GPIO10-18, 38, 39, 40, 47, 48 | mic GPIO41, 42 | SD card GPIO7, 8, 9, 21
#define PIN_BUTTON      1    // D0 / GPIO1  push button to GND (RTC pin -> can wake from deep sleep)
#define PIN_I2S_BCLK    2    // D1 / GPIO2  -> MAX98357A BCLK
#define PIN_I2S_LRC     3    // D2 / GPIO3  -> MAX98357A LRC (word select)
#define PIN_AMP_SD      4    // D3 / GPIO4  -> MAX98357A SD (HIGH = amp on, LOW = amp off, saves power)
#define PIN_I2C_SDA     5    // D4 / GPIO5  -> OLED SDA (STEMMA QT blue)
#define PIN_I2C_SCL     6    // D5 / GPIO6  -> OLED SCL (STEMMA QT yellow)
#define PIN_I2S_DOUT    43   // D6 / GPIO43 -> MAX98357A DIN
// Built-in on the Sense board:
#define PIN_MIC_CLK     42   // PDM mic clock (internal)
#define PIN_MIC_DATA    41   // PDM mic data  (internal)
#define PIN_USER_LED    21   // orange LED, active LOW (shared with SD card CS; SD not used)

// ---------------- OLED ----------------
#define OLED_ADDR       0x3D // Adafruit 326 STEMMA QT default. Some boards are 0x3C: firmware tries both.
#define OLED_W          128
#define OLED_H          64
// Beam-splitter viewing shows the screen mirrored. Set the default here;
// double-tap the button (or type 'm' in Serial Monitor) to toggle at runtime (saved).
#define HUD_MIRROR_X_DEFAULT  true   // mirror left-right (typical for a 45-degree beam splitter)
#define HUD_FLIP_Y_DEFAULT    false  // flip upside-down (if your optics invert it)
#define HUD_SHOW_MS     25000        // answer stays on screen this long, then the OLED turns off
#define HUD_MAX_LINES   3            // large font: 3 lines x ~12 characters

// ---------------- Camera ----------------
#define CAM_FRAME_SIZE  FRAMESIZE_VGA  // 640x480: enough for labels/gauges, ~30-60 KB JPEG
#define CAM_JPEG_QUALITY 12            // 0-63, lower = better quality, bigger file
#define CAM_VFLIP       false          // set true if photos come out upside down
#define CAM_HMIRROR     false

// ---------------- Audio ----------------
#define AUDIO_VOLUME_PCT   80      // software volume for speech (0-100)
#define MIC_SAMPLE_RATE    16000
#define HOLD_TO_TALK_MS    450     // hold the button longer than this = record a spoken question while held
#define MAX_TALK_SECONDS   6

// ---------------- Button / power ----------------
#define DOUBLE_TAP_MS      350     // second tap within this = toggle HUD mirror
#define IDLE_TO_SLEEP_MS   60000   // no taps for this long -> deep sleep (tap the button to wake + ask)
#define WIFI_TIMEOUT_MS    15000
#define HTTP_TIMEOUT_MS    45000   // vision model + speech can take 5-20 s
#define CPU_MHZ            160     // 240 runs hotter for no real gain here
