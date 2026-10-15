# pi_agent

Placeholder for the Raspberry Pi agent (not implemented yet).

The agent will run on the Raspberry Pi, read photodiodes/sensors and send timestamped sensor
events to the desktop application over the network. It is deliberately a separate top-level
package so that GPIO code never becomes part of the desktop core. On the desktop side it would
be one transport of the device-neutral `timing_sensor` module, feeding the same parser as the
simulator. That transport is not implemented. The module does not depend on this agent.
