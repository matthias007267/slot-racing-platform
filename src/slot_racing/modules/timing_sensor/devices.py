"""Recognise the last USB sensor without treating a display name as an identity.

A unique serial number wins even when the port name changed. Without one, a
unique vendor/product pair and USB location can still match. A port name is
only used when exactly one listed port has it and the stored vendor ids do not
contradict that port. Several candidates are not guessed.

A name the user gave the board is only a label. It follows the same identity
and is never used to decide which port matches.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import ValidationError

from slot_racing.modules.timing_sensor.configuration import (
    BoardName,
    DeviceHint,
    SensorConfiguration,
)
from slot_racing.modules.timing_sensor.link import SensorLink, TransportMode
from slot_racing.modules.timing_sensor.transport import SensorTransport, SerialSensorTransport


class PortMatch(StrEnum):
    """How the stored hint relates to the ports currently listed."""

    UNIQUE = "unique"
    AMBIGUOUS = "ambiguous"
    ABSENT = "absent"


@dataclass(frozen=True, slots=True)
class SerialPortFacts:
    """OS facts for one serial port.

    ``product`` and ``manufacturer`` are display titles. They are not an identity.
    """

    device: str
    vid: int | None = None
    pid: int | None = None
    serial_number: str = ""
    location: str = ""
    product: str = ""
    manufacturer: str = ""

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
        for name in ("product", "manufacturer"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or len(value) > 128
                or any(ord(character) < 32 for character in value)
            ):
                raise ValueError(f"{name} must be a short display title")


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
                    product=_usb_title(
                        getattr(port, "product", None),
                        getattr(port, "description", None),
                        device,
                    ),
                    manufacturer=_display(getattr(port, "manufacturer", None)),
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


def usb_interface_label(facts: SerialPortFacts) -> str:
    """A label for one listed interface before the user has named that board."""
    title = facts.product or facts.manufacturer
    detail = facts.serial_number or facts.location
    if title and detail:
        return f"{title} · {detail}"
    if title or detail:
        return title or detail
    return facts.device


def port_choice_label(
    facts: SerialPortFacts, boards: Sequence[BoardName], ports: Sequence[SerialPortFacts]
) -> str:
    """The user's name for this board, or the USB interface label."""
    named = board_name_for(facts, boards, ports)
    if named:
        return named
    return usb_interface_label(facts)


def board_name_for(
    facts: SerialPortFacts, boards: Sequence[BoardName], ports: Sequence[SerialPortFacts]
) -> str:
    """The one stored name that uniquely identifies ``facts``. Empty when none does."""
    found = [board.name for board in boards if _same_board(board, facts, ports)]
    if len(found) == 1:
        return found[0]
    return ""


def assign_board_name(
    boards: Sequence[BoardName],
    facts: SerialPortFacts,
    name: str,
    ports: Sequence[SerialPortFacts],
) -> tuple[BoardName, ...]:
    """Replace the name of this board. A name already used by another board is refused.

    ``ValueError`` with ``invalid`` means the text is not a usable name.
    ``ValueError`` with ``taken`` means another board already has that name.
    """
    hint = _hint_from_facts(facts)
    try:
        named = BoardName(name=name, device=hint)
    except ValidationError as error:
        raise ValueError("invalid") from error
    listed = ports if any(port.device == facts.device for port in ports) else (*ports, facts)
    kept: list[BoardName] = []
    for board in boards:
        if _same_board(board, facts, listed):
            continue
        if board.name.casefold() == named.name.casefold():
            raise ValueError("taken")
        kept.append(board)
    kept.append(named)
    return tuple(kept)


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
    # ``connected`` stays false until the hello. The port can already be open.
    # Replacing it would reset the board and drop a clock exchange in progress.
    if link.port_open or link.race_bound or configuration.mode != "serial":
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


def _same_board(board: BoardName, facts: SerialPortFacts, ports: Sequence[SerialPortFacts]) -> bool:
    status, match = classify_port(board.device, ports)
    return status is PortMatch.UNIQUE and match is not None and match.device == facts.device


def _hint_from_facts(facts: SerialPortFacts) -> DeviceHint:
    return DeviceHint(
        vid=facts.vid,
        pid=facts.pid,
        serial_number=facts.serial_number,
        location=facts.location,
        port=facts.device,
    )


def _usb_title(product: object, description: object, device: str) -> str:
    title = _display(product)
    if title:
        return title
    described = _display(description)
    suffix = f"({device})"
    if described.endswith(suffix):
        described = described[: -len(suffix)].strip()
    if not described or described == device:
        return ""
    return described


def _display(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if len(text) > 128:
        return ""
    return text


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
