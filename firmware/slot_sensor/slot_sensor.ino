#include <EEPROM.h>

#include "capture.h"

// USB sensor timing for an Arduino Mega 2560.
//
// Sensor s1 is D2 (INT0), sensor s2 is D3 (INT1). Free = LOW, broken = HIGH,
// capture on RISING. Further Mega external-interrupt pins are listed and left
// disabled; other pins are not interrupt inputs.
//
// Opening the serial port resets the board. The sketch then prints one HELLO
// and waits to be synchronized. Lines are PSF/1 only.

namespace {

constexpr uint32_t kBaud = 115200;
constexpr int kActiveSensors = 2;
constexpr uint8_t kSensorId[] = {'1', '2', '3', '4', '5', '6'};

slot_sensor::Engine g_engine(kActiveSensors);
char g_session[8] = "b0000";
bool g_ready_sent = false;
uint32_t g_reported_drops = 0;
char g_rx[64];
uint8_t g_rx_len = 0;
bool g_rx_overflow = false;

void print_u64(uint64_t value) {
  char buffer[21];
  int index = 20;
  buffer[20] = '\0';
  if (value == 0) {
    Serial.write('0');
    return;
  }
  while (value > 0 && index > 0) {
    buffer[--index] = static_cast<char>('0' + static_cast<uint8_t>(value % 10ULL));
    value /= 10ULL;
  }
  Serial.print(buffer + index);
}

void print_session() { Serial.print(g_session); }

uint64_t read_time_ns() {
  noInterrupts();
  uint64_t now = g_engine.clock.extend_ns(micros());
  interrupts();
  return now;
}

void load_session() {
  uint16_t boot = EEPROM.read(0) | (static_cast<uint16_t>(EEPROM.read(1)) << 8);
  boot = static_cast<uint16_t>(boot + 1U);
  EEPROM.update(0, static_cast<uint8_t>(boot & 0xff));
  EEPROM.update(1, static_cast<uint8_t>(boot >> 8));
  g_session[0] = 'b';
  for (int nibble = 3; nibble >= 0; --nibble) {
    uint8_t digit = static_cast<uint8_t>((boot >> (nibble * 4)) & 0xf);
    g_session[4 - nibble] = static_cast<char>(digit < 10 ? '0' + digit : 'a' + digit - 10);
  }
  g_session[5] = '\0';
}

void send_hello() {
  Serial.print(F("PSF/1 HELLO "));
  print_session();
  Serial.print(F(" mega2560 1.1.0 ns 2 s1 s2\n"));
}

void send_status(const __FlashStringHelper* code) {
  Serial.print(F("PSF/1 STATUS "));
  print_session();
  Serial.print(' ');
  Serial.print(code);
  Serial.write('\n');
}

void send_error_buffer() {
  Serial.print(F("PSF/1 ERROR "));
  print_session();
  Serial.print(F(" BUFFER "));
  Serial.print(g_engine.buffer.dropped);
  Serial.write('\n');
}

void send_event(const slot_sensor::CapturedEvent& event) {
  Serial.print(F("PSF/1 "));
  print_session();
  Serial.print(' ');
  Serial.print(event.sequence);
  Serial.print(' ');
  print_u64(event.time_ns);
  Serial.print(F(" s"));
  Serial.write(kSensorId[event.sensor]);
  Serial.write('\n');
}

void on_edge(int index) {
  // micros() is called with interrupts already off inside the ISR.
  g_engine.accept_rising(index, micros());
}

void edge0() { on_edge(0); }
void edge1() { on_edge(1); }

void handle_line(uint64_t received_ns) {
  if (slot_sensor::parse_arm_command(g_rx)) {
    g_engine.rearm();
    return;
  }
  uint32_t host_seq = 0;
  if (!slot_sensor::parse_sync_command(g_rx, &host_seq)) {
    Serial.print(F("PSF/1 ERROR "));
    print_session();
    Serial.print(F(" PROTOCOL 0\n"));
    return;
  }
  uint64_t sending = read_time_ns();
  Serial.print(F("PSF/1 SYNC "));
  print_session();
  Serial.print(' ');
  Serial.print(host_seq);
  Serial.print(' ');
  print_u64(received_ns);
  Serial.print(' ');
  print_u64(sending);
  Serial.write('\n');
}

void poll_serial() {
  while (Serial.available() > 0) {
    int raw = Serial.read();
    if (raw < 0) {
      return;
    }
    char letter = static_cast<char>(raw);
    if (letter == '\n') {
      uint64_t received_ns = read_time_ns();
      g_rx[g_rx_len] = '\0';
      if (!g_rx_overflow && g_rx_len > 0) {
        handle_line(received_ns);
      }
      g_rx_len = 0;
      g_rx_overflow = false;
      continue;
    }
    if (letter == '\r') {
      continue;
    }
    if (g_rx_overflow || g_rx_len >= static_cast<uint8_t>(sizeof(g_rx) - 1)) {
      g_rx_overflow = true;
      g_rx_len = 0;
      continue;
    }
    g_rx[g_rx_len++] = letter;
  }
}

}  // namespace

void setup() {
  load_session();
  for (int index = 0; index < kActiveSensors; ++index) {
    uint8_t pin = slot_sensor::kInterruptPins[index];
    pinMode(pin, INPUT);
  }
  Serial.begin(kBaud);
  send_hello();
  send_status(F("WAIT"));
  int interrupt0 = digitalPinToInterrupt(slot_sensor::kInterruptPins[0]);
  int interrupt1 = digitalPinToInterrupt(slot_sensor::kInterruptPins[1]);
  if (interrupt0 != NOT_AN_INTERRUPT) {
    attachInterrupt(interrupt0, edge0, RISING);
  }
  if (interrupt1 != NOT_AN_INTERRUPT) {
    attachInterrupt(interrupt1, edge1, RISING);
  }
}

void loop() {
  uint64_t now = read_time_ns();
  if (g_engine.clock.take_wrap()) {
    send_status(F("TIME"));
  }
  for (int index = 0; index < kActiveSensors; ++index) {
    bool blocked = digitalRead(slot_sensor::kInterruptPins[index]) == HIGH;
    g_engine.service(index, now, blocked);
  }
  slot_sensor::CapturedEvent event;
  while (g_engine.buffer.pop(&event)) {
    send_event(event);
  }
  if (g_engine.buffer.dropped != g_reported_drops) {
    g_reported_drops = g_engine.buffer.dropped;
    send_error_buffer();
  }
  if (!g_ready_sent && g_engine.armed()) {
    g_ready_sent = true;
    send_status(F("READY"));
  }
  poll_serial();
}
