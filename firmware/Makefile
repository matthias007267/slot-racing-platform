# Host checks use the system C++ compiler. The Mega image uses Arduino CLI.
CXX ?= g++
CXXFLAGS := -std=c++17 -Wall -Wextra -Werror -I slot_sensor
FQBN := arduino:avr:mega
BUILD_DIR := build
TEST_BIN := $(BUILD_DIR)/capture_test

.PHONY: test firmware clean

test: $(TEST_BIN)
	$(TEST_BIN)

$(TEST_BIN): capture_test.cpp slot_sensor/capture.h
	mkdir -p $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) -o $(TEST_BIN) capture_test.cpp

firmware:
	arduino-cli compile --fqbn $(FQBN) --warnings all slot_sensor

clean:
	rm -rf $(BUILD_DIR)
