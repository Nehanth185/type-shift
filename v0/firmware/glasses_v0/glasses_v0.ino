// glasses_v0.ino - Smart-glasses prototype v0 firmware
// Board: Seeed XIAO ESP32-S3 Sense. Arduino core: esp32 by Espressif 3.x.
// Tools menu: Board "XIAO_ESP32S3", PSRAM "OPI PSRAM", USB CDC On Boot "Enabled".
//
// What it does:
//   tap button        -> take a photo, POST it to the server, show hud_text on the OLED,
//                        play the spoken answer on the bone-conduction transducer
//   hold button       -> record a spoken question while held (built-in mic), then photo + ask
//   double-tap        -> toggle mirrored HUD (for beam-splitter viewing), saved across reboots
//   idle 60 s         -> deep sleep (~mA); a tap wakes it and asks straight away
// Serial Monitor (115200) bench commands: a=ask  m=mirror  t=test tone  d=display test  s=sleep  i=info

#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Fonts/FreeSansBold9pt7b.h>
#include <ArduinoJson.h>
#include <ESP_I2S.h>
#include <Preferences.h>
#include "esp_camera.h"
#include "esp_sleep.h"
#include "driver/rtc_io.h"
#include "driver/gpio.h"
#include "mbedtls/base64.h"
// Library "OV5640 Auto Focus for ESP32 Camera" (Eric Nam). Required: newer Sense boards ship an OV5640.
// (A plain #include is needed: Arduino's library detection does not see includes hidden behind __has_include.)
#include <ESP32_OV5640_AF.h>
#define HAVE_OV5640_AF 1
OV5640 ov5640AF;

#include "config.h"
#include "camera_pins.h"
#if __has_include("secrets.h")
  #include "secrets.h"
#else
  #warning "secrets.h not found - using secrets.example.h placeholders. Copy it to secrets.h and fill in Wi-Fi + server."
  #include "secrets.example.h"
#endif

#define FW_VERSION "v0.1"

Adafruit_SSD1306 oled(OLED_W, OLED_H, &Wire, -1);
Preferences prefs;
bool oledOk = false, camOk = false;
bool hudMirrorX = HUD_MIRROR_X_DEFAULT, hudFlipY = HUD_FLIP_Y_DEFAULT;
uint32_t lastActivityMs = 0, hudShownMs = 0;
bool hudOn = false;

// ======================================================================= OLED
void oledApplyOrientation() {
  // SSD1306 hardware remap: 0xA1 = normal columns, 0xA0 = mirrored; 0xC8 = normal rows, 0xC0 = flipped.
  oled.ssd1306_command(hudMirrorX ? 0xA0 : 0xA1);
  oled.ssd1306_command(hudFlipY ? 0xC0 : 0xC8);
}

void oledPower(bool on) {
  if (!oledOk) return;
  oled.ssd1306_command(on ? SSD1306_DISPLAYON : SSD1306_DISPLAYOFF);
  hudOn = on;
}

// Word-wrap `text` into at most maxLines lines that fit `width` px with the current font.
int wrapText(const String &text, String lines[], int maxLines, int width) {
  int n = 0; String cur = "";
  int16_t x1, y1; uint16_t w, h;
  int i = 0, len = text.length();
  while (i < len && n < maxLines) {
    while (i < len && text[i] == ' ') i++;
    int j = i; while (j < len && text[j] != ' ') j++;
    String word = text.substring(i, j); i = j;
    if (word.length() == 0) break;
    String trial = cur.length() ? cur + " " + word : word;
    oled.getTextBounds(trial.c_str(), 0, 20, &x1, &y1, &w, &h);
    if (w <= width || cur.length() == 0) { cur = trial; }
    else { lines[n++] = cur; cur = word; }
  }
  if (cur.length() && n < maxLines) lines[n++] = cur;
  if (i < len) return -1;  // did not fit
  return n;
}

// Show text big (3 lines, bold 9pt). Falls back to small font if it does not fit.
void showHud(const String &textIn, bool keepOn = true) {
  Serial.printf("[HUD] %s\n", textIn.c_str());
  if (!oledOk) return;
  String text = textIn; text.replace("\n", " ");
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  String lines[8];
  oled.setFont(&FreeSansBold9pt7b); oled.setTextSize(1);
  int n = wrapText(text, lines, HUD_MAX_LINES, OLED_W);
  if (n > 0) {
    int lineH = 21, top = (OLED_H - n * lineH) / 2;
    for (int k = 0; k < n; k++) {
      int16_t x1, y1; uint16_t w, h;
      oled.getTextBounds(lines[k].c_str(), 0, 0, &x1, &y1, &w, &h);
      oled.setCursor((OLED_W - w) / 2 - x1, top + k * lineH + 15);
      oled.print(lines[k]);
    }
  } else {  // too long for big text: classic 6x8 font, up to 8 lines of 21 chars
    oled.setFont(nullptr); oled.setTextSize(1);
    n = wrapText(text, lines, 8, OLED_W);
    if (n < 0) n = 8;
    for (int k = 0; k < n; k++) { oled.setCursor(0, k * 8); oled.print(lines[k]); }
  }
  oledApplyOrientation();
  oled.display();
  oledPower(true);
  hudShownMs = millis();
  (void)keepOn;
}

bool initOled() {
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000);
  for (uint8_t addr : {(uint8_t)OLED_ADDR, (uint8_t)0x3C, (uint8_t)0x3D}) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0 && oled.begin(SSD1306_SWITCHCAPVCC, addr, true, false)) {
      Serial.printf("[OLED] found at 0x%02X\n", addr);
      oled.clearDisplay(); oled.display();
      return true;
    }
  }
  Serial.println("[OLED] NOT FOUND - check SDA/SCL/3V3/GND wires");
  return false;
}

// ======================================================================= CAMERA
bool initCamera() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0; c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM; c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.sccb_i2c_port = 1;               // keep camera off I2C port 0 (Wire / OLED)
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  // Allocate for SXGA so the OV5640 can auto-focus at full size; we switch to CAM_FRAME_SIZE right after.
  c.frame_size = psramFound() ? FRAMESIZE_SXGA : CAM_FRAME_SIZE;
  c.jpeg_quality = CAM_JPEG_QUALITY;
  c.fb_count = 1;
  c.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  c.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) { Serial.printf("[CAM] init failed 0x%x (is the camera board clicked on?)\n", err); return false; }
  sensor_t *s = esp_camera_sensor_get();
  if (s) {
    Serial.printf("[CAM] sensor PID 0x%04X\n", s->id.PID);
    if (s->id.PID == OV3660_PID) { s->set_vflip(s, 1); s->set_brightness(s, 1); s->set_saturation(s, -2); }
    if (s->id.PID == OV5640_PID) {
      s->set_reg(s, 0x302C, 0xC0, 0x00);  // Seeed heat fix: lower IO drive
#ifdef HAVE_OV5640_AF
      // Newer Sense boards ship an auto-focus OV5640: without this its lens sits out of focus (Seeed wiki).
      if (ov5640AF.start(s) && ov5640AF.focusInit() == 0 && ov5640AF.autoFocusMode() == 0) {
        uint32_t t0 = millis();
        while (ov5640AF.getFWStatus() != 0x10 && millis() - t0 < 2500) delay(50);   // 0x10 = focused
        Serial.printf("[CAM] OV5640 auto-focus on (%u ms)\n", (unsigned)(millis() - t0));
      } else Serial.println("[CAM] OV5640 auto-focus init failed - photos may be blurry");
#else
      Serial.println("[CAM] OV5640 found but AF library missing - install 'OV5640 Auto Focus for ESP32 Camera'");
#endif
    }
    s->set_framesize(s, CAM_FRAME_SIZE);
    if (CAM_VFLIP) s->set_vflip(s, 1);
    if (CAM_HMIRROR) s->set_hmirror(s, 1);
  }
  return true;
}

// Returns a fresh frame (drops stale buffered frames first). Caller must esp_camera_fb_return().
camera_fb_t *captureFresh(int dropFrames) {
  for (int k = 0; k < dropFrames; k++) { camera_fb_t *old = esp_camera_fb_get(); if (old) esp_camera_fb_return(old); }
  return esp_camera_fb_get();
}

// ======================================================================= AUDIO OUT
void ampEnable(bool on) {
  gpio_hold_dis((gpio_num_t)PIN_AMP_SD);
  pinMode(PIN_AMP_SD, OUTPUT);
  digitalWrite(PIN_AMP_SD, on ? HIGH : LOW);  // HIGH (>1.4 V) = on, left channel; LOW = shutdown
  if (on) delay(5);
}

// Plays 16-bit PCM (mono or stereo) from a callback-fed stream. Output is always stereo so the
// MAX98357A gets the sound whichever channel it listens to.
class Speaker {
 public:
  I2SClass i2s;
  bool begin(uint32_t rate) {
    ampEnable(true);
    i2s.setPins(PIN_I2S_BCLK, PIN_I2S_LRC, PIN_I2S_DOUT);
    if (!i2s.begin(I2S_MODE_STD, rate, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO)) {
      Serial.println("[SPK] i2s begin failed"); ampEnable(false); return false;
    }
    return true;
  }
  void writeMono(const int16_t *s, size_t n) {
    static int16_t st[512 * 2];
    while (n) {
      size_t k = n > 512 ? 512 : n;
      for (size_t i = 0; i < k; i++) { int32_t v = (int32_t)s[i] * AUDIO_VOLUME_PCT / 100; st[2*i] = v; st[2*i+1] = v; }
      i2s.write((uint8_t *)st, k * 4);
      s += k; n -= k;
    }
  }
  void end() {
    int16_t z[256] = {0}; i2s.write((uint8_t *)z, sizeof(z));  // flush with silence (no pop)
    delay(20); i2s.end(); ampEnable(false);
  }
};

void playTestTone() {
  Speaker sp; if (!sp.begin(16000)) return;
  int16_t buf[160];
  for (int blk = 0; blk < 100; blk++) {                   // 1 s of 440 Hz
    for (int i = 0; i < 160; i++) buf[i] = (int16_t)(9000 * sin(2 * PI * 440 * (blk * 160 + i) / 16000.0));
    sp.writeMono(buf, 160);
  }
  sp.end();
}

// Minimal WAV parser: finds "fmt " and "data" chunks. Returns data offset or -1.
int parseWav(const uint8_t *h, size_t len, uint32_t &rate, uint16_t &ch, uint16_t &bits) {
  if (len < 12 || memcmp(h, "RIFF", 4) || memcmp(h + 8, "WAVE", 4)) return -1;
  size_t p = 12;
  while (p + 8 <= len) {
    uint32_t sz = h[p+4] | h[p+5] << 8 | h[p+6] << 16 | (uint32_t)h[p+7] << 24;
    if (!memcmp(h + p, "fmt ", 4) && p + 24 <= len) {
      ch = h[p+10] | h[p+11] << 8; rate = h[p+12] | h[p+13] << 8 | h[p+14] << 16 | (uint32_t)h[p+15] << 24;
      bits = h[p+22] | h[p+23] << 8;
    } else if (!memcmp(h + p, "data", 4)) return p + 8;
    p += 8 + sz + (sz & 1);
  }
  return -1;
}

void playPcmBuffer(const uint8_t *wav, size_t len) {
  uint32_t rate = 16000; uint16_t ch = 1, bits = 16;
  int off = parseWav(wav, len, rate, ch, bits);
  if (off < 0 || bits != 16) { Serial.println("[SPK] not a 16-bit WAV"); return; }
  Speaker sp; if (!sp.begin(rate)) return;
  const int16_t *s = (const int16_t *)(wav + off); size_t n = (len - off) / 2;
  if (ch == 2) { for (size_t i = 0; i < n / 2; i++) { int16_t m = s[2*i]; sp.writeMono(&m, 1); } }
  else sp.writeMono(s, n);
  sp.end();
}

// Streams a WAV from the server and plays it while downloading.
void playWavUrl(const String &url) {
  HTTPClient http; http.setTimeout(15000);
  if (!http.begin(url)) return;
  int code = http.GET();
  if (code != 200) { Serial.printf("[SPK] GET %s -> %d\n", url.c_str(), code); http.end(); return; }
  WiFiClient *st = http.getStreamPtr();
  int remaining = http.getSize();          // -1 if chunked
  uint8_t hdr[256]; size_t got = 0; uint32_t t0 = millis();
  while (got < sizeof(hdr) && (remaining < 0 || (int)got < remaining) && millis() - t0 < 5000 && http.connected()) {
    int a = st->available(); if (!a) { delay(2); continue; }
    got += st->read(hdr + got, min((size_t)a, sizeof(hdr) - got));
  }
  uint32_t rate = 16000; uint16_t ch = 1, bits = 16;
  int off = parseWav(hdr, got, rate, ch, bits);
  if (off < 0 || bits != 16 || ch != 1) { Serial.println("[SPK] expected 16-bit mono WAV"); http.end(); return; }
  Speaker sp; if (!sp.begin(rate)) { http.end(); return; }
  static uint8_t buf[2048];
  size_t carry = got - off; memcpy(buf, hdr + off, carry);
  if (remaining > 0) remaining -= got;
  t0 = millis();
  while (http.connected() || st->available()) {
    if (remaining == 0) break;
    int a = st->available();
    if (!a) { if (millis() - t0 > 4000) break; delay(1); continue; }
    t0 = millis();
    size_t want = min((size_t)a, sizeof(buf) - carry);
    if (remaining > 0) want = min(want, (size_t)remaining);
    int r = st->read(buf + carry, want); if (r <= 0) continue;
    if (remaining > 0) remaining -= r;
    size_t total = carry + r, even = total & ~1u;
    sp.writeMono((int16_t *)buf, even / 2);
    carry = total - even; if (carry) buf[0] = buf[even];
  }
  sp.end(); http.end();
}

// ======================================================================= MIC (hold-to-talk)
uint8_t *micWav = nullptr; size_t micWavLen = 0;

void writeWavHeader(uint8_t *h, uint32_t dataBytes, uint32_t rate) {
  uint32_t byteRate = rate * 2;
  memcpy(h, "RIFF", 4); uint32_t v = 36 + dataBytes; memcpy(h + 4, &v, 4);
  memcpy(h + 8, "WAVEfmt ", 8); v = 16; memcpy(h + 16, &v, 4);
  uint16_t s = 1; memcpy(h + 20, &s, 2); memcpy(h + 22, &s, 2);  // PCM, mono
  memcpy(h + 24, &rate, 4); memcpy(h + 28, &byteRate, 4);
  s = 2; memcpy(h + 32, &s, 2); s = 16; memcpy(h + 34, &s, 2);
  memcpy(h + 36, "data", 4); memcpy(h + 40, &dataBytes, 4);
}

// Records while the button is held (max MAX_TALK_SECONDS). Result in micWav/micWavLen.
void recordWhileHeld() {
  size_t maxBytes = MIC_SAMPLE_RATE * 2 * MAX_TALK_SECONDS;
  if (!micWav) micWav = (uint8_t *)ps_malloc(44 + maxBytes);
  micWavLen = 0;
  if (!micWav) return;
  I2SClass mic;
  mic.setPinsPdmRx(PIN_MIC_CLK, PIN_MIC_DATA);
  if (!mic.begin(I2S_MODE_PDM_RX, MIC_SAMPLE_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
    Serial.println("[MIC] init failed"); return;
  }
  showHud("Listening...");
  size_t n = 0; uint32_t t0 = millis();
  while (digitalRead(PIN_BUTTON) == LOW && n < maxBytes) {
    size_t r = mic.readBytes((char *)micWav + 44 + n, min((size_t)1024, maxBytes - n));
    n += r;
  }
  mic.end();
  // simple gain: PDM mic is quiet; x4 with clipping
  int16_t *s = (int16_t *)(micWav + 44);
  for (size_t i = 0; i < n / 2; i++) { int32_t v = s[i] * 4; s[i] = v > 32767 ? 32767 : v < -32768 ? -32768 : v; }
  writeWavHeader(micWav, n, MIC_SAMPLE_RATE);
  micWavLen = 44 + n;
  Serial.printf("[MIC] recorded %u ms\n", (unsigned)(millis() - t0));
}

// ======================================================================= WIFI + ASK
bool ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) return true;
  showHud("Wi-Fi...");
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(true);  // modem sleep between packets: big idle-power saving
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < WIFI_TIMEOUT_MS) delay(100);
  if (WiFi.status() != WL_CONNECTED) { showHud("No Wi-Fi. Check secrets.h"); return false; }
  Serial.printf("[WIFI] %s  IP %s  RSSI %d\n", WIFI_SSID, WiFi.localIP().toString().c_str(), WiFi.RSSI());
  return true;
}

void appendBytes(uint8_t *dst, size_t &p, const void *src, size_t n) { memcpy(dst + p, src, n); p += n; }
void appendStr(uint8_t *dst, size_t &p, const String &s) { appendBytes(dst, p, s.c_str(), s.length()); }

void doAsk(bool withVoice) {
  lastActivityMs = millis();
  if (!camOk) { showHud("Camera error"); return; }
  if (!ensureWifi()) return;
  showHud("Looking...");
  digitalWrite(PIN_USER_LED, LOW);
  camera_fb_t *fb = captureFresh(2);
  digitalWrite(PIN_USER_LED, HIGH);
  if (!fb) { showHud("Photo failed"); return; }
  Serial.printf("[CAM] %ux%u JPEG %u bytes\n", fb->width, fb->height, (unsigned)fb->len);

  // ---- multipart/form-data body in PSRAM
  const String B = "----glassesv0boundary7MA4YWxk";
  String head = "--" + B + "\r\nContent-Disposition: form-data; name=\"device_id\"\r\n\r\n" + WiFi.macAddress() + "\r\n";
  head += "--" + B + "\r\nContent-Disposition: form-data; name=\"image\"; filename=\"frame.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n";
  String mid = "", tail = "\r\n--" + B + "--\r\n";
  bool voice = withVoice && micWavLen > 44 + MIC_SAMPLE_RATE / 5;  // >0.1 s of audio
  if (voice) mid = "\r\n--" + B + "\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"q.wav\"\r\nContent-Type: audio/wav\r\n\r\n";
  size_t total = head.length() + fb->len + mid.length() + (voice ? micWavLen : 0) + tail.length();
  uint8_t *body = (uint8_t *)ps_malloc(total);
  if (!body) { esp_camera_fb_return(fb); showHud("Out of memory"); return; }
  size_t p = 0;
  appendStr(body, p, head); appendBytes(body, p, fb->buf, fb->len);
  if (voice) { appendStr(body, p, mid); appendBytes(body, p, micWav, micWavLen); }
  appendStr(body, p, tail);
  esp_camera_fb_return(fb);

  showHud("Thinking...");
  HTTPClient http; http.setTimeout(HTTP_TIMEOUT_MS); http.setConnectTimeout(5000);
  String url = String(SERVER_URL) + "/ask";
  http.begin(url);
  http.addHeader("Content-Type", "multipart/form-data; boundary=" + B);
  if (strlen(DEVICE_TOKEN)) http.addHeader("X-Device-Token", DEVICE_TOKEN);
  uint32_t t0 = millis();
  int code = http.POST(body, total);
  free(body);
  if (code != 200) {
    Serial.printf("[HTTP] POST %s -> %d %s\n", url.c_str(), code, code < 0 ? http.errorToString(code).c_str() : "");
    showHud(code < 0 ? "Server not reachable" : "Server error " + String(code));
    http.end(); return;
  }
  String resp = http.getString(); http.end();
  Serial.printf("[HTTP] %d bytes in %u ms\n", resp.length(), (unsigned)(millis() - t0));

  JsonDocument doc;
  if (deserializeJson(doc, resp)) { showHud("Bad reply"); return; }
  String hud = doc["hud_text"] | "No answer";
  showHud(hud);
  const char *pcm = doc["pcm_b64"] | (const char *)nullptr;
  const char *au = doc["audio_url"] | (const char *)nullptr;
  if (pcm && strlen(pcm)) {
    size_t outLen = 0, inLen = strlen(pcm);
    uint8_t *wav = (uint8_t *)ps_malloc(inLen * 3 / 4 + 4);
    if (wav && mbedtls_base64_decode(wav, inLen * 3 / 4 + 4, &outLen, (const uint8_t *)pcm, inLen) == 0) playPcmBuffer(wav, outLen);
    free(wav);
  } else if (au && strlen(au)) {
    String a = au; if (a.startsWith("/")) a = String(SERVER_URL) + a;
    playWavUrl(a);
  }
  lastActivityMs = millis();
}

// ======================================================================= POWER
void goToSleep() {
  Serial.println("[PWR] deep sleep - tap button to wake");
  showHud("Sleeping"); delay(400);
  oledPower(false);
  ampEnable(false);
  gpio_hold_en((gpio_num_t)PIN_AMP_SD);           // keep amp shut down while asleep
  WiFi.disconnect(true); WiFi.mode(WIFI_OFF);
  esp_sleep_enable_ext0_wakeup((gpio_num_t)PIN_BUTTON, 0);  // wake on button LOW
  rtc_gpio_pullup_en((gpio_num_t)PIN_BUTTON);
  rtc_gpio_pulldown_dis((gpio_num_t)PIN_BUTTON);
  Serial.flush();
  esp_deep_sleep_start();
}

void toggleMirror() {
  hudMirrorX = !hudMirrorX;
  prefs.putBool("mirrorX", hudMirrorX);
  showHud(hudMirrorX ? "Mirror ON" : "Mirror OFF");
}

void displayTest() {
  showHud("HUD test: can you read this?");
  delay(2500);
  showHud("This is a much longer line to check the small-font fallback when a reply is too long for big text.");
}

void printInfo() {
  Serial.printf("fw %s | PSRAM %u free | heap %u | wifi %s | mirrorX %d flipY %d | server %s\n", FW_VERSION,
                (unsigned)ESP.getFreePsram(), (unsigned)ESP.getFreeHeap(),
                WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString().c_str() : "down", hudMirrorX, hudFlipY, SERVER_URL);
}

// ======================================================================= SETUP / LOOP
void setup() {
  setCpuFrequencyMhz(CPU_MHZ);
  Serial.begin(115200);
  esp_sleep_wakeup_cause_t wake = esp_sleep_get_wakeup_cause();
  if (wake == ESP_SLEEP_WAKEUP_EXT0) rtc_gpio_deinit((gpio_num_t)PIN_BUTTON);
  gpio_hold_dis((gpio_num_t)PIN_AMP_SD);
  ampEnable(false);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_USER_LED, OUTPUT); digitalWrite(PIN_USER_LED, HIGH);
  delay(wake == ESP_SLEEP_WAKEUP_EXT0 ? 0 : 800);  // give USB serial a moment on cold boot
  Serial.printf("\n=== smart-glasses %s === wake cause %d, PSRAM %s\n", FW_VERSION, (int)wake, psramFound() ? "yes" : "NO (enable OPI PSRAM!)");

  prefs.begin("glasses", false);
  hudMirrorX = prefs.getBool("mirrorX", HUD_MIRROR_X_DEFAULT);
  hudFlipY = prefs.getBool("flipY", HUD_FLIP_Y_DEFAULT);

  camOk = initCamera();       // camera first (its own I2C port), then the OLED bus
  oledOk = initOled();
  showHud(camOk ? "Starting..." : "Camera error");
  if (ensureWifi()) showHud(wake == ESP_SLEEP_WAKEUP_EXT0 ? "Looking..." : "Ready. Tap to ask");
  lastActivityMs = millis();
  if (wake == ESP_SLEEP_WAKEUP_EXT0) doAsk(false);   // the wake tap counts as a question
}

void loop() {
  // ---- bench commands over USB serial
  if (Serial.available()) {
    char c = Serial.read();
    if (c == 'a') doAsk(false);
    else if (c == 'm') toggleMirror();
    else if (c == 't') playTestTone();
    else if (c == 'd') displayTest();
    else if (c == 's') goToSleep();
    else if (c == 'i') printInfo();
  }

  // ---- button: tap / double-tap / hold-to-talk
  if (digitalRead(PIN_BUTTON) == LOW) {
    delay(25);                                  // debounce
    if (digitalRead(PIN_BUTTON) == LOW) {
      uint32_t t0 = millis();
      while (digitalRead(PIN_BUTTON) == LOW && millis() - t0 < HOLD_TO_TALK_MS) delay(5);
      if (digitalRead(PIN_BUTTON) == LOW) {      // still held -> hold-to-talk
        recordWhileHeld();
        doAsk(true);
      } else {                                   // released: wait briefly for a second tap
        uint32_t t1 = millis(); bool second = false;
        while (millis() - t1 < DOUBLE_TAP_MS) { if (digitalRead(PIN_BUTTON) == LOW) { second = true; break; } delay(5); }
        if (second) { while (digitalRead(PIN_BUTTON) == LOW) delay(5); toggleMirror(); }
        else doAsk(false);
      }
      lastActivityMs = millis();
    }
  }

  // ---- power management
  if (hudOn && millis() - hudShownMs > HUD_SHOW_MS) oledPower(false);
  if (millis() - lastActivityMs > IDLE_TO_SLEEP_MS) goToSleep();
  delay(10);
}
