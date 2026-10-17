"""Recognise the last USB sensor without treating a display name as an identity.

A unique serial number wins even when the port name changed. Without one, a
unique vendor/product pair and USB location can still match. A port name is
only used when exactly one listed port has it and the stored vendor ids do not
contradict that port. Several candidates are not guessed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from slot_racing.modules.timing_sensor.configuration import DeviceHint, SensorConfiguration
from slot_racing.modules.timing_sensor.link import SensorLink, TransportMode
from slot_racing.modules.timing_sensor.transport import SensorTransport, SerialSensorTransport


class PortMatch(StrEnum):
    """How the stored hint relates to the ports currently listed."""

    UNIQUE = "unique"
    AMBIGUOUS = "ambiguous"
    ABSENT = "absent"


@dataclass(frozen=True, slots=True)
class SerialPortFacts:
    """Stable OS facts for one serial port. The friendly name is intentionally absent."""

    device: str
    vid: int | None = None
    pid: int | None = None
    serial_number: str = ""
    location: str = ""

    def __post_init__(self) -> None:
        if (
            not isinstance(self.device, str)
            or not self.device
            or any(character.isspace() for character in self.device)
        ):
            raise ValueError("serial device name must be a single token")
        for name in ("vid", "pid"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 0xFFFF
            ):
                raise ValueError(f"{name} must be a usb id or None")
        for name in ("serial_number", "location"):
            value = getattr(self, name)
            if not isinstance(value, str) or any(character.isspace() for character in value):
                raise ValueError(f"{name} must be a single token")


def list_serial_devices() -> tuple[SerialPortFacts, ...]:
    """Ports the operating system reports. Empty when pyserial cannot list them."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return ()
    found: list[SerialPortFacts] = []
    for port in list_ports.comports():
        device = getattr(port, "device", "")
        if not isinstance(device, str) or not device:
            continue
        try:
            found.append(
                SerialPortFacts(
                    device=device,
                    vid=_usb_id(getattr(port, "vid", None)),
                    pid=_usb_id(getattr(port, "pid", None)),
                    serial_number=_token(getattr(port, "serial_number", None)),
                    location=_token(getattr(port, "location", None)),
                )
            )
        except ValueError:
            continue
    return tuple(found)


def classify_port(
    hint: DeviceHint, ports: Sequence[SerialPortFacts]
) -> tuple[PortMatch, SerialPortFacts | None]:
    """The one port ``hint`` identifies, or why no port was selected."""
    if not isinstance(hint, DeviceHint):
        raise TypeError("hint must be a DeviceHint")
    serial = hint.serial_number
    if serial:
        found = [
            port
            for port in ports
            if port.serial_number == serial
            and _id_agrees(hint.vid, port.vid)
            and _id_agrees(hint.pid, port.pid)
        ]
        return _one(found)
    if hint.vid is not None and hint.pid is not None and hint.location:
        found = [
            port
            for port in ports
            if port.vid == hint.vid and port.pid == hint.pid and port.location == hint.location
        ]
        return _one(found)
    if hint.vid is not None and hint.pid is not None:
        found = [port for port in ports if port.vid == hint.vid and port.pid == hint.pid]
        return _one(found)
    if hint.port:
        found = [port for port in ports if port.device == hint.port]
        if len(found) != 1:
            return (_one(found)[0], None)
        only = found[0]
        if not _id_agrees(hint.vid, only.vid) or not _id_agrees(hint.pid, only.pid):
            return PortMatch.ABSENT, None
        return PortMatch.UNIQUE, only
    return PortMatch.ABSENT, None


def remembered_hint(configuration: SensorConfiguration) -> DeviceHint:
    """The stored device facts, using the last port name when the hint has none."""
    hint = configuration.device
    if hint.port or not configuration.port:
        return hint
    return hint.model_copy(update={"port": configuration.port})


def attach_remembered_serial(
    link: SensorLink,
    configuration: SensorConfiguration,
    ports: Sequence[SerialPortFacts],
    *,
    hold: bool = False,
    open_port: Callable[[str], SensorTransport] | None = None,
) -> SerialPortFacts | None:
    """Open the one remembered USB device. Ambiguous and missing devices stay closed.

    The caller still has to read a protocol hello. Opening the port is not proof
    that the remembered controller is the device that answered.
    """
    if link.connected or link.race_bound or configuration.mode != "serial":
        return None
    _status, match = classify_port(remembered_hint(configuration), ports)
    if match is None:
        return None
    opener = open_port if open_port is not None else SerialSensorTransport
    link.use_transport(opener(match.device), TransportMode.SERIAL)
    if hold:
        link.acquire()
    return match


def _one(found: list[SerialPortFacts]) -> tuple[PortMatch, SerialPortFacts | None]:
    if len(found) == 1:
        return PortMatch.UNIQUE, found[0]
    if len(found) > 1:
        return PortMatch.AMBIGUOUS, None
    return PortMatch.ABSENT, None


def _id_agrees(expected: int | None, actual: int | None) -> bool:
    """A missing id does not contradict. Two present ids must be equal."""
    return expected is None or actual is None or expected == actual


def _usb_id(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 0xFFFF:
        return None
    return value


def _token(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or any(character.isspace() for character in text) or len(text) > 128:
        return ""
    return text
