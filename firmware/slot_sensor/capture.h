#pragma once

// Capture rules for the slot-sensor firmware.
//
// Free beam is LOW, broken beam is HIGH, and a crossing is the first accepted
// rising edge. Each sensor locks on its own. It arms again only after 1000 ms
// from that edge and 20 ms of continuous free signal. Those 20 ms may overlap
// the end of the lock. A later edge while locked does not move the deadline.
//
// At startup a sensor that is already broken waits until it has been free for
// 20 ms. That blocked state is not a crossing.
//
// The 32-bit microsecond counter is extended to a 64-bit nanosecond time base
// shared by every sensor. One missed wrap (the caller was away for 2^32 us,
// about 71.6 minutes) is counted. Two wraps in one gap cannot be seen; the
// firmware treats that as a lost clock.

#include <stddef.h>
#include <stdint.h>

namespace slot_sensor {

constexpr uint64_t kNanosPerMicro = 1000ULL;
constexpr uint64_t kLockoutNs = 1000000000ULL;
constexpr uint64_t kStableFreeNs = 20000000ULL;
constexpr int kBufferCapacity = 16;
constexpr int kMaxSensors = 6;
constexpr uint8_t kWaitFree = 0;
constexpr uint8_t kOpen = 1;
constexpr uint8_t kLocked = 2;

// External interrupt pins on the Mega 2560. digitalPinToInterrupt is valid
// for these and is not valid for the other digital pins. Only the first two
// are enabled by the sketch.
constexpr uint8_t kInterruptPins[kMaxSensors] = {2, 3, 21, 20, 19, 18};

struct CapturedEvent {
  int sensor;
  uint64_t time_ns;
  uint32_t sequence;
};

struct Clock {
  uint32_t overflows;
  uint32_t last_low;
  bool wrapped;

  Clock() : overflows(0), last_low(0), wrapped(false) {}

  // Micros value from the controller. The caller serializes this.
  uint64_t extend_us(uint32_t micros_now) {
    if (micros_now < last_low) {
      overflows += 1;
      wrapped = true;
    }
    last_low = micros_now;
    return (static_cast<uint64_t>(overflows) << 32) | micros_now;
  }

  uint64_t extend_ns(uint32_t micros_now) { return extend_us(micros_now) * kNanosPerMicro; }

  bool take_wrap() {
    bool seen = wrapped;
    wrapped = false;
    return seen;
  }
};

struct Channel {
  uint8_t state;
  uint64_t accepted_ns;
  uint64_t stable_since_ns;
  bool timing_stable;
  bool ever_open;

  Channel()
      : state(kWaitFree),
        accepted_ns(0),
        stable_since_ns(0),
        timing_stable(false),
        ever_open(false) {}

  // True when this rising edge is a crossing. Ignored edges leave accepted_ns.
  bool on_rising(uint64_t now_ns, uint32_t* sequence, CapturedEvent* out, int index) {
    if (state != kOpen || sequence == nullptr || out == nullptr) {
      return false;
    }
    state = kLocked;
    accepted_ns = now_ns;
    timing_stable = false;
    *sequence += 1;
    out->sensor = index;
    out->time_ns = now_ns;
    out->sequence = *sequence;
    return true;
  }

  void service(uint64_t now_ns, bool blocked) {
    if (state == kOpen) {
      return;
    }
    if (blocked) {
      timing_stable = false;
      return;
    }
    if (!timing_stable) {
      timing_stable = true;
      stable_since_ns = now_ns;
      return;
    }
    if (now_ns - stable_since_ns < kStableFreeNs) {
      return;
    }
    if (state == kLocked && now_ns - accepted_ns < kLockoutNs) {
      return;
    }
    state = kOpen;
    ever_open = true;
    timing_stable = false;
  }
};

struct Buffer {
  CapturedEvent items[kBufferCapacity];
  int head;
  int count;
  uint32_t dropped;
  uint32_t last_dropped_sequence;

  Buffer() : head(0), count(0), dropped(0), last_dropped_sequence(0) {}

  bool push(const CapturedEvent& event) {
    if (count >= kBufferCapacity) {
      dropped += 1;
      last_dropped_sequence = event.sequence;
      return false;
    }
    int tail = (head + count) % kBufferCapacity;
    items[tail] = event;
    count += 1;
    return true;
  }

  bool pop(CapturedEvent* out) {
    if (count == 0 || out == nullptr) {
      return false;
    }
    *out = items[head];
    head = (head + 1) % kBufferCapacity;
    count -= 1;
    return true;
  }
};

struct Engine {
  Clock clock;
  Channel channels[kMaxSensors];
  int sensor_count;
  Buffer buffer;
  uint32_t sequence;

  explicit Engine(int sensors) : sensor_count(sensors), sequence(0) {
    if (sensor_count < 1) {
      sensor_count = 1;
    }
    if (sensor_count > kMaxSensors) {
      sensor_count = kMaxSensors;
    }
  }

  // ISR body. micros_now is the controller's micros() at the edge.
  bool accept_rising(int index, uint32_t micros_now) {
    if (index < 0 || index >= sensor_count) {
      return false;
    }
    CapturedEvent event;
    uint64_t now_ns = clock.extend_ns(micros_now);
    if (!channels[index].on_rising(now_ns, &sequence, &event, index)) {
      return false;
    }
    return buffer.push(event);
  }

  void service(int index, uint64_t now_ns, bool blocked) {
    if (index < 0 || index >= sensor_count) {
      return;
    }
    channels[index].service(now_ns, blocked);
  }

  bool armed() const {
    for (int index = 0; index < sensor_count; ++index) {
      if (!channels[index].ever_open) {
        return false;
      }
    }
    return true;
  }
};

// Host line "PSF/1 SYNC <seq>". Returns false for every other line, including
// event lines and comments. The sketch answers a rejected line with ERROR PROTOCOL.
inline bool parse_sync_command(const char* line, uint32_t* host_seq) {
  if (line == nullptr || host_seq == nullptr) {
    return false;
  }
  const char* prefix = "PSF/1 SYNC ";
  for (size_t index = 0; prefix[index] != '\0'; ++index) {
    if (line[index] != prefix[index]) {
      return false;
    }
  }
  const char* cursor = line + 11;
  if (*cursor < '1' || *cursor > '9') {
    return false;
  }
  uint32_t value = 0;
  while (*cursor >= '0' && *cursor <= '9') {
    uint32_t digit = static_cast<uint32_t>(*cursor - '0');
    if (value > (UINT32_MAX - digit) / 10) {
      return false;
    }
    value = value * 10 + digit;
    cursor += 1;
  }
  if (*cursor == '\r') {
    cursor += 1;
  }
  if (*cursor != '\0') {
    return false;
  }
  *host_seq = value;
  return true;
}

}  // namespace slot_sensor
