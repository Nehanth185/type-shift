# Prototype v0 parts list (priced 2026-11-10, nothing bought)
| Part | Price | Source | Status |
|---|---|---|---|
| Seeed XIAO ESP32-S3 Sense (camera+mic+WiFi+LiPo charging) | $18.90 | https://www.seeedstudio.com/XIAO-ESP32S3-Sense-p-5639.html | verified page |
| Adafruit 0.96" 128x64 OLED (326) | $17.50 | https://www.adafruit.com/product/326 | verified, in stock |
| Adafruit bone conductor transducer (1674) | $8.95 | https://www.adafruit.com/product/1674 | verified, in stock |
| Adafruit MAX98357A I2S amp (3006) | $5.95 mono (out of stock) / $9.95 stereo | https://www.adafruit.com/product/3006 | verified |
| Adafruit LiPo 3.7V 400mAh (3898) | $6.95 | https://www.adafruit.com/product/1578 (variant list) | verified; 500mAh out of stock |
| 20mm 50/50 beam splitter cube (Amazon B08FBFMWFL) | $14.99 | https://www.amazon.com/dp/B08FBFMWFL | search excerpt only |
| Small magnifier lens 25-40mm focal | ~$5-8 | - | estimate |
| Cheap safety glasses as base frame | ~$5-10 | - | estimate |
| STEMMA QT cable, wire, heat shrink, Sugru | ~$5 | - | estimate |
Total ~ $96 + shipping/tax (estimate).
Battery estimate: ~200mA draw while streaming camera -> ~2h on 400mAh (estimate).
Cloud: ESP32 -> WiFi -> laptop/phone Python server -> vision LLM API (free tier) -> text to OLED + TTS to bone conduction. SilentHelp Google Cloud credits are NOT for this.
