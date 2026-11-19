# glasses-v0 bench notes

Self-written notes for our own prototype (prototype/v0). Pin facts copied from firmware/glasses_v0/config.h and WIRING.md. Nothing here has been tested on real hardware yet.

<!-- page: 1 -->
## I2C pins on the XIAO ESP32-S3

The OLED uses I2C.
I2C pins: SDA is D4 (GPIO5), SCL is D5 (GPIO6).
STEMMA QT colors: blue is SDA, yellow is SCL.
The OLED address is 0x3D on the Adafruit 326 board. Some boards use 0x3C, so the firmware tries both.
Do not wire anything to GPIO10-18, 38-40, 47 or 48: the camera uses them.

<!-- page: 2 -->
## Blank OLED screen checklist

1. Check the OLED has power: 3V3 and GND on the STEMMA QT cable.
2. Check SDA is on D4 and SCL is on D5, not swapped.
3. Check the OLED address in Serial Monitor: 0x3D or 0x3C.
4. If the text shows but is mirrored, double-tap the button to toggle the mirror.

Untested lead: the camera and the OLED may both use I2C, so a camera problem could block the screen.
