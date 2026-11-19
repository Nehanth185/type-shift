# Wiring: smart-glasses prototype v0

Five parts plug into the XIAO board. You need **14 wires** in total, plus 2 battery wires. Every wire has one job, explained below.

**Words you'll see**
- **3V3**: the XIAO's 3.3-volt output. It powers the screen and the amp. Red wires.
- **GND (ground)**: the shared "return" path. Every part must share it, or nothing works. Black wires.
- **Signal wire**: carries data, not power.
- **D0-D6**: pin names printed on the XIAO. (The code calls them GPIO numbers, which is why both are listed.)

Pin names come from Seeed's pin map (wiki.seeedstudio.com/xiao_esp32s3_getting_started) and Adafruit's guides for each part.

## 1. Wire table (pin to pin)

| # | From (XIAO pin) | To (part, pin) | Colour idea | What this wire does |
|---|---|---|---|---|
| 1 | **3V3** | OLED **VIN** (STEMMA red) | red | Powers the screen. |
| 2 | **GND** | OLED **GND** (STEMMA black) | black | Screen's return path. |
| 3 | **D4** (GPIO5) | OLED **SDA** (STEMMA blue) | blue | I2C **data**: the text going to the screen. |
| 4 | **D5** (GPIO6) | OLED **SCL** (STEMMA yellow) | yellow | I2C **clock**: a beat that keeps the data in step. |
| 5 | **3V3** | Amp **VIN** | red | Powers the amplifier. |
| 6 | **GND** | Amp **GND** | black | Amp's return path. |
| 7 | **D1** (GPIO2) | Amp **BCLK** | green | Bit clock: one tick per bit of sound data. |
| 8 | **D2** (GPIO3) | Amp **LRC** | white | Word select: marks where each sound sample starts. |
| 9 | **D6** (GPIO43) | Amp **DIN** | orange | The sound data itself (digital audio). |
| 10 | **D3** (GPIO4) | Amp **SD** | purple | On/off switch. The code turns the amp off when it's quiet, which saves battery. |
| 11 | Amp **+** (screw terminal) | Transducer wire 1 | any | Speaker output. The transducer has no + or -, so either wire works. |
| 12 | Amp **-** (screw terminal) | Transducer wire 2 | any | Second speaker wire. **Never connect amp + or - to GND** (that can damage the amp). |
| 13 | **D0** (GPIO1) | Button leg 1 | any | The code watches this pin. Pressing connects it to GND, and the code sees that. |
| 14 | **GND** | Button leg 2 (diagonal from leg 1) | black | The other side of the button. No resistor needed: the chip has a built-in one. |
| B+ | Battery **red** | XIAO **BAT+** pad (back of board) | red | Battery power and charging. |
| B- | Battery **black** | XIAO **BAT-** pad (back, the one **nearer the USB port**) | black | Battery return. **Check this twice: reversed battery wires can kill the board.** |

**Leave the amp's GAIN pin unconnected.** That gives 9 dB of gain, which is a good start. For more volume, run a wire from GAIN to GND (12 dB).

**The OLED's RST pin isn't needed.** The STEMMA QT version resets itself.

## 2. Pins you must NOT use

The camera board uses some pins inside. Using them would break the camera, mic or SD card.

| Used inside the Sense | GPIO |
|---|---|
| Camera | 10, 11, 12, 13, 14, 15, 16, 17, 18, 38, 39, 40, 47, 48 |
| Microphone | 41, 42 (D11, D12 pads) |
| SD card | 7, 8, 9, 21 (D8, D9, D10) |

Free pins left over: D7 (GPIO44). It's spare for a second button or a battery-voltage divider later.

## 3. Picture

```
                     +-------------------------------+
                     |  XIAO ESP32-S3 Sense (top)    |
   [USB-C] ======    |  camera board clicked on top  |
                     |                               |
   button leg 1 ---- | D0                        5V  |  (not used)
   amp BCLK -------- | D1                       GND  | ---+---- GND to OLED, amp, button
   amp LRC  -------- | D2                       3V3  | ---+---- 3V3 to OLED VIN, amp VIN
   amp SD   -------- | D3                       D10  |  (SD card - don't use)
   OLED SDA -------- | D4                        D9  |  (SD card - don't use)
   OLED SCL -------- | D5                        D8  |  (SD card - don't use)
   amp DIN  -------- | D6                        D7  |  (spare)
                     +-------------------------------+
                         back: BAT+  BAT-  <- 400 mAh LiPo (red to +, black to -)

   OLED (Adafruit 326)        MAX98357A amp               Bone transducer
   +-------------+            +----------------+          +-----------+
   | VIN  <- 3V3 |            | VIN  <- 3V3    |  + ----> |  wire 1   |
   | GND  <- GND |            | GND  <- GND    |  - ----> |  wire 2   |
   | SCL  <- D5  |            | SD   <- D3     |          +-----------+
   | SDA  <- D4  |            | GAIN (empty)   |
   +-------------+            | DIN  <- D6     |          Button (4 legs)
   STEMMA QT cable:           | BCLK <- D1     |          D0 --o  o-- GND
   black=GND red=VIN          | LRC  <- D2     |          (diagonal legs)
   blue=SDA  yellow=SCL       +----------------+
```

The left/right order of the pins in the drawing is right. Hold the board with USB-C at the top and the chip facing you to match it. Still check the labels printed on the board before soldering.

## 4. Hookup tips

1. **Use a breadboard first.** Solder header pins to the XIAO, the amp and the OLED, then test everything on a breadboard. Only solder wires in place once all three tests pass (BUILD_STEPS step 6).
2. **The OLED's STEMMA QT cable** saves 4 wires. Cut one end off and solder its four colours to the XIAO (black GND, red 3V3, blue D4, yellow D5).
3. **Share power wires.** 3V3 and GND each go to three places. Twist those wires together, or use the breadboard's + and - rails.
4. **The battery goes on last**, after the USB tests work. Unplug USB before you solder the battery. The XIAO charges the battery whenever USB is plugged in; the red LED blinks while it charges.
5. **The transducer has to touch bone.** Press it against the bone just in front of your ear, or behind it. Hanging in the air, it is almost silent.
6. **Watch the heat.** The camera gets warm (Seeed measured about 54-64 °C on the board during long streaming). Mount the XIAO so it doesn't touch your skin. The firmware runs the camera only while the glasses are awake (up to 60 s after your last tap), then deep-sleeps, so it should stay cooler than in Seeed's non-stop test. That's an estimate until it's measured on the real board.
