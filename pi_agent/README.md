# pi_agent

Placeholder for the Raspberry Pi agent (not implemented yet).

The agent will run on the Raspberry Pi, read photodiodes/sensors and send timestamped sensor
events to the desktop application over the network. It is deliberately a separate top-level
package so that GPIO code never becomes part of the desktop core. On the desktop side the events
are received by the `timing_sensor` module and converted into standard `SensorTriggered` events.
