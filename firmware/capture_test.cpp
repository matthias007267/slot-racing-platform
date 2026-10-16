#include "capture.h"

#include <stdio.h>
#include <stdlib.h>

namespace {

int g_failures = 0;

void expect(bool condition, const char* message) {
  if (!condition) {
    fprintf(stderr, "FAIL %s\n", message);
    g_failures += 1;
  }
}

void test_clock_extends_one_wrap() {
  slot_sensor::Clock clock;
  expect(clock.extend_us(100) == 100, "first micros");
  expect(clock.extend_us(100) == 100, "same micros stays");
  uint64_t wrapped = clock.extend_us(50);
  expect(wrapped == ((1ULL << 32) | 50), "one wrap is extended");
  expect(clock.take_wrap(), "wrap is announced once");
  expect(!clock.take_wrap(), "wrap flag clears");
  expect(clock.extend_ns(50) == wrapped * slot_sensor::kNanosPerMicro, "nanoseconds");
}

void test_blocked_start_is_not_a_crossing() {
  slot_sensor::Engine engine(2);
  expect(!engine.accept_rising(0, 10), "rising while waiting is ignored");
  expect(engine.sequence == 0, "ignored start does not take a sequence");
  expect(engine.buffer.count == 0, "no event while waiting");
  engine.service(0, 0, true);
  engine.service(0, slot_sensor::kStableFreeNs, true);
  expect(engine.channels[0].state == slot_sensor::kWaitFree, "still blocked");
  engine.service(0, 0, false);
  engine.service(0, slot_sensor::kStableFreeNs, false);
  expect(engine.channels[0].state == slot_sensor::kOpen, "stable free arms the sensor");
}

void test_lock_is_not_extended_by_ignored_edges() {
  slot_sensor::Engine engine(2);
  engine.service(0, 0, false);
  engine.service(0, slot_sensor::kStableFreeNs, false);
  expect(engine.accept_rising(0, 1000), "open sensor accepts the edge");
  slot_sensor::CapturedEvent event;
  expect(engine.buffer.pop(&event), "event is queued");
  expect(event.sequence == 1, "first sequence is 1");
  expect(event.time_ns == 1000ULL * slot_sensor::kNanosPerMicro, "stamp is the edge");
  uint64_t accepted = engine.channels[0].accepted_ns;
  expect(!engine.accept_rising(0, 1000 + 500000), "locked edge is ignored");
  expect(engine.channels[0].accepted_ns == accepted, "ignored edge does not move the lock");
  expect(engine.sequence == 1, "ignored edge takes no sequence");

  uint64_t lock_end = accepted + slot_sensor::kLockoutNs;
  engine.service(0, lock_end - slot_sensor::kStableFreeNs, false);
  engine.service(0, lock_end - 1, false);
  expect(engine.channels[0].state == slot_sensor::kLocked, "still locked inside 1000 ms");
  engine.service(0, lock_end, false);
  expect(engine.channels[0].state == slot_sensor::kOpen, "arms at 1000 ms when already free");
}

void test_free_glitch_restarts_only_the_stable_window() {
  slot_sensor::Engine engine(1);
  engine.service(0, 0, false);
  engine.service(0, slot_sensor::kStableFreeNs, false);
  expect(engine.accept_rising(0, 0), "accept");
  uint64_t accepted = engine.channels[0].accepted_ns;
  uint64_t after = accepted + slot_sensor::kLockoutNs + 1000;
  engine.service(0, after, false);
  engine.service(0, after + 1000, true);
  engine.service(0, after + 2000, false);
  engine.service(0, after + 2000 + slot_sensor::kStableFreeNs - 1, false);
  expect(engine.channels[0].state == slot_sensor::kLocked, "glitch restarts the 20 ms");
  expect(engine.channels[0].accepted_ns == accepted, "glitch does not extend the 1000 ms");
  engine.service(0, after + 2000 + slot_sensor::kStableFreeNs, false);
  expect(engine.channels[0].state == slot_sensor::kOpen, "arms 20 ms after the glitch");
}

void test_sensors_are_independent() {
  slot_sensor::Engine engine(2);
  engine.service(0, 0, false);
  engine.service(0, slot_sensor::kStableFreeNs, false);
  engine.service(1, 0, false);
  engine.service(1, slot_sensor::kStableFreeNs, false);
  expect(engine.accept_rising(0, 10), "lane sensor 1");
  expect(engine.accept_rising(1, 20), "lane sensor 2 while the other is locked");
  expect(engine.channels[0].state == slot_sensor::kLocked, "sensor 1 locked");
  expect(engine.channels[1].state == slot_sensor::kLocked, "sensor 2 locked on its own stamp");
  expect(engine.channels[1].accepted_ns != engine.channels[0].accepted_ns, "stamps differ");
  expect(!engine.accept_rising(0, 30), "sensor 1 still ignores");
  expect(engine.sequence == 2, "two accepted edges");
}

void test_buffer_overflow_keeps_the_sequence_and_the_lock() {
  slot_sensor::Engine engine(1);
  engine.service(0, 0, false);
  engine.service(0, slot_sensor::kStableFreeNs, false);
  for (int index = 0; index < slot_sensor::kBufferCapacity; ++index) {
    engine.channels[0].state = slot_sensor::kOpen;
    expect(engine.accept_rising(0, static_cast<uint32_t>(1000 + index)), "fill");
  }
  engine.channels[0].state = slot_sensor::kOpen;
  uint64_t accepted = 0;
  expect(!engine.accept_rising(0, 5000), "full buffer drops the edge");
  accepted = engine.channels[0].accepted_ns;
  expect(engine.buffer.dropped == 1, "drop is counted");
  expect(
      engine.buffer.last_dropped_sequence ==
          static_cast<uint32_t>(slot_sensor::kBufferCapacity + 1),
      "dropped edge still consumed a sequence");
  expect(engine.channels[0].state == slot_sensor::kLocked, "drop still locks the sensor");
  expect(!engine.accept_rising(0, 6000), "lock holds after the drop");
  expect(engine.channels[0].accepted_ns == accepted, "later edge does not move the lock");
  expect(engine.buffer.dropped == 1, "ignored edge is not another drop");
}

void test_sync_command_parser() {
  uint32_t seq = 0;
  expect(slot_sensor::parse_sync_command("PSF/1 SYNC 12", &seq) && seq == 12, "sync command");
  expect(slot_sensor::parse_sync_command("PSF/1 SYNC 12\r", &seq), "sync tolerates CR");
  expect(!slot_sensor::parse_sync_command("PSF/1 SYNC 0", &seq), "seq starts at 1");
  expect(!slot_sensor::parse_sync_command("# debug", &seq), "no debug command");
  expect(!slot_sensor::parse_sync_command("PSF/1 b0001 1 10 s1", &seq), "event is not a command");
  expect(!slot_sensor::parse_sync_command("PSF/1 SYNC -1", &seq), "negative seq");
}

}  // namespace

int main() {
  test_clock_extends_one_wrap();
  test_blocked_start_is_not_a_crossing();
  test_lock_is_not_extended_by_ignored_edges();
  test_free_glitch_restarts_only_the_stable_window();
  test_sensors_are_independent();
  test_buffer_overflow_keeps_the_sequence_and_the_lock();
  test_sync_command_parser();
  if (g_failures != 0) {
    fprintf(stderr, "%d firmware capture checks failed\n", g_failures);
    return 1;
  }
  return 0;
}
