// camera_pins.h - XIAO ESP32-S3 Sense camera connector (B2B), from Seeed wiki:
// https://wiki.seeedstudio.com/xiao_esp32s3_camera_usage/ ("Camera slot circuit design")
// Same for OV2640, OV3660 and OV5640 modules.
#pragma once
#define PWDN_GPIO_NUM   -1   // not connected
#define RESET_GPIO_NUM  -1   // not connected
#define XCLK_GPIO_NUM   10   // XMCLK
#define SIOD_GPIO_NUM   40   // CAM_SDA
#define SIOC_GPIO_NUM   39   // CAM_SCL
#define Y9_GPIO_NUM     48   // DVP_Y9
#define Y8_GPIO_NUM     11   // DVP_Y8
#define Y7_GPIO_NUM     12   // DVP_Y7
#define Y6_GPIO_NUM     14   // DVP_Y6
#define Y5_GPIO_NUM     16   // DVP_Y5
#define Y4_GPIO_NUM     18   // DVP_Y4
#define Y3_GPIO_NUM     17   // DVP_Y3
#define Y2_GPIO_NUM     15   // DVP_Y2
#define VSYNC_GPIO_NUM  38   // DVP_VSYNC
#define HREF_GPIO_NUM   47   // DVP_HREF
#define PCLK_GPIO_NUM   13   // DVP_PCLK
