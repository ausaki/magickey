#!env python

import argparse
import asyncio
import dataclasses
import json
import logging
import logging.config
import signal
import time
from dataclasses import dataclass
from enum import Enum, IntEnum
from pathlib import Path
from typing import IO, Any, Dict, List, Optional, Set, Tuple, Union

import evdev
import pyudev

logging.config.dictConfig(
    {
        'version': 1,
        'formatters': {
            'default': {
                '()': 'logging.Formatter',
                'fmt': '[{levelname:1.1s} {asctime} {module}:{funcName}:{lineno}] {message}',  # noqa
                'style': '{',
            }
        },
        'handlers': {
            'stdout': {
                'class': 'logging.StreamHandler',
                'stream': 'ext://sys.stdout',
                'level': 'DEBUG',
                'formatter': 'default',
            },
        },
        'loggers': {
            'magickeyboard': {
                'handlers': ['stdout'],
                'level': 'INFO',
                'propagate': False,
            }
        },
    }
)
logger = logging.getLogger('magickeyboard')


class KeyState(IntEnum):
    up = 0
    down = 1
    hold = 2

    def __str__(self) -> str:  # type: ignore
        for name, value in self.__class__._member_map_.items():
            if value == self:
                return name


@dataclass
class KeyMapping:
    src_modifiers: set[int]
    src_key: int
    dst_modifiers: set[int]
    dst_key: int

    def __str__(self) -> str:
        src = [MagicKeyboard.keycode_to_name(k) for k in self.src_modifiers]
        src.append(MagicKeyboard.keycode_to_name(self.src_key))
        src = '+'.join(src)  # type: ignore

        dst = [MagicKeyboard.keycode_to_name(k) for k in self.dst_modifiers]
        dst.append(MagicKeyboard.keycode_to_name(self.dst_key))
        dst = '+'.join(dst)  # type: ignore

        return f'{src} -> {dst}'


class KeyboardMappingState(str, Enum):
    """PRE_MATCH_INIT -- press modifier --> PRE_MATCH_PRESSED_MODIFIER -- press key
    --> MATCHED or UNMATCHED

    MATCHED -- send out dst_modifiers and dst_key --> AFTER_MATCH or PRE_MATCH_INIT

    UNMATCHED -- send out _copy_modifiers and current key -->
    AFTER_MATCH or PRE_MATCHED_INIT

    AFTER_MATCH -- press key --> MATCH or UNMATCHED
    """

    PRE_MATCH_INIT = 'PRE_MATCH_INIT'
    PRE_MATCH_PRESSED_KEY = 'pre_match_pressed_key'
    PRE_MATCH_PRESSED_MODIFIER = 'pre_match_pressed_modifier'
    MATCHED = 'matched'
    UNMATCHED = 'unmatched'
    AFTER_MATCH = 'after_match'


@dataclass
class ActiveKeyInfo:
    state: KeyState = KeyState.down
    first_pressed_time: float = dataclasses.field(default_factory=time.time)
    count: int = 1


class KeyboardMapping:
    keyboard_name: str
    key_mappings: List[KeyMapping]
    input_device: evdev.InputDevice
    output_device: evdev.InputDevice
    state: KeyboardMappingState
    # _all_modifiers is built from key_mappings[*].src_modifiers, see parse_config
    _all_modifiers: Set[int]
    _active_modifiers: Dict[int, ActiveKeyInfo]
    _active_keys: Dict[int, ActiveKeyInfo]
    _copy_modifiers: Dict[int, ActiveKeyInfo]
    _matched_key_mapping: Optional[KeyMapping]
    _async_task: Optional[asyncio.Task[None]]

    def __init__(self, keyboard_name: str, key_mappings: List[KeyMapping]) -> None:
        self.keyboard_name = keyboard_name
        self.key_mappings = key_mappings
        self.input_device = None
        self.output_device = None
        self.state = KeyboardMappingState.PRE_MATCH_PRESSED_KEY

        self._all_modifiers = set()
        self._active_modifiers = {}
        self._active_keys = {}
        self._copy_modifiers = {}
        self._matched_key_mapping = None
        self._async_task = None

    def __str__(self) -> str:
        s = self.keyboard_name
        s += '\n' + '\n'.join(str(key_mapping) for key_mapping in self.key_mappings)
        return s

    def add_key_mapping(self, key_mapping: KeyMapping) -> None:
        self.key_mappings.append(key_mapping)
        self._all_modifiers.update(key_mapping.src_modifiers)
        self._all_modifiers.update(key_mapping.dst_modifiers)

    def set_all_modifiers(self) -> None:
        for key_mapping in self.key_mappings:
            self._all_modifiers.update(key_mapping.src_modifiers)
            self._all_modifiers.update(key_mapping.dst_modifiers)

    def find_input_device(self) -> Optional[evdev.InputDevice]:
        devices = [evdev.InputDevice(fn) for fn in evdev.list_devices()]
        for dev in devices:
            if self.keyboard_name in {dev.name, dev.phys, dev.path}:
                return dev

        return None

    def send_key(self, keycode: int, keystate: KeyState) -> None:
        logger.debug('send key %s %s', evdev.ecodes.keys[keycode], keystate)
        self.output_device.write(evdev.ecodes.EV_KEY, keycode, keystate)

    def down_key(self, keycode: int) -> None:
        self.send_key(keycode, KeyState.down)

    def up_key(self, keycode: int) -> None:
        self.send_key(keycode, KeyState.up)

    def send_keys(self, keycodes: List[int], keystate: KeyState) -> None:
        for keycode in keycodes:
            self.send_key(keycode, keystate)

    def match(self, src_modifiers: set[int], src_key: int) -> Optional[KeyMapping]:
        for key_mapping in self.key_mappings:
            if (
                src_modifiers == key_mapping.src_modifiers
                and src_key == key_mapping.src_key
            ):
                return key_mapping

        return None

    def handle_pre_match_init(
        self, keycode: int, keyname: str, keystate: KeyState, is_modifier: bool
    ) -> None:
        if not is_modifier:
            if keystate in {KeyState.down, KeyState.hold}:
                self._active_keys[keycode] = ActiveKeyInfo(keystate, time.time(), 1)
                self.send_key(keycode, keystate)
                self.output_device.syn()
                self.state = KeyboardMappingState.PRE_MATCH_PRESSED_KEY
            else:
                logger.warning(
                    '%s unexpected key: %s %s',
                    KeyboardMappingState.PRE_MATCH_INIT,
                    keyname,
                    keystate,
                )
            return

        # start here keycode is a modifier
        if keystate in {KeyState.down, KeyState.hold}:
            self._active_modifiers[keycode] = ActiveKeyInfo(keystate, time.time(), 1)
            self.send_key(keycode, keystate)
            self.output_device.syn()
            self.state = KeyboardMappingState.PRE_MATCH_PRESSED_MODIFIER
        else:
            logger.warning(
                '%s unexpected key: %s %s',
                KeyboardMappingState.PRE_MATCH_INIT,
                keyname,
                keystate,
            )
        return

    def handle_pre_match_pressed_key(
        self, keycode: int, keyname: str, keystate: KeyState, is_modifier: bool
    ) -> None:
        if is_modifier:
            logger.warning(
                '%s got unexpected key: %s %s',
                KeyboardMappingState.PRE_MATCH_PRESSED_KEY,
                keyname,
                keystate,
            )
            return

        if keystate in {KeyState.down, KeyState.hold}:
            self._active_keys[keycode] = ActiveKeyInfo(keystate, time.time(), 1)
        else:
            self._active_keys.pop(keycode, None)
            if not self._active_keys:
                self.state = KeyboardMappingState.PRE_MATCH_INIT

        self.send_key(keycode, keystate)
        self.output_device.syn()
        return

    def try_match_key(self, keycode: int, keyname: str, keystate: KeyState) -> None:
        old_state = self.state
        self._copy_modifiers = dict(self._active_modifiers)
        matched_key_mapping = self.match(set(self._active_modifiers), keycode)

        logger.debug(
            '%s %s',
            '+'.join(MagicKeyboard.keycode_to_name(m) for m in self._active_modifiers),
            keyname,
        )

        if matched_key_mapping is None:
            dst_modifiers = self._active_modifiers
            dst_key = keycode
            self.state = KeyboardMappingState.UNMATCHED
        else:
            logger.info('matched key mapping %s', matched_key_mapping)
            dst_modifiers = matched_key_mapping.dst_modifiers  # type: ignore
            dst_key = matched_key_mapping.dst_key
            self.state = KeyboardMappingState.MATCHED
            self._matched_key_mapping = matched_key_mapping

        # release active modifiers
        self.send_keys(list(self._active_modifiers), KeyState.up)
        self.output_device.syn()

        if keystate in {KeyState.down, KeyState.hold}:
            self.send_keys(list(dst_modifiers), KeyState.down)
            self.send_key(dst_key, keystate)
            self.output_device.syn()
        else:
            logger.warning(
                '%s unexpected key: %s %s',
                old_state,
                keyname,
                keystate,
            )

    def handle_pre_match_pressed_modifier(
        self, keycode: int, keyname: str, keystate: KeyState, is_modifier: bool
    ) -> None:
        if is_modifier:
            if keystate in {KeyState.down, KeyState.hold}:
                info = self._active_modifiers.setdefault(
                    keycode, ActiveKeyInfo(keystate, time.time(), 0)
                )
                info.count += 1
                self.send_key(keycode, keystate)
                self.output_device.syn()
                return

            # start here keystate == KeyState.up
            info = self._active_modifiers.pop(keycode, None)  # type: ignore
            if info:
                self.send_key(keycode, keystate)
                self.output_device.syn()

            if not self._active_modifiers:
                self.state = KeyboardMappingState.PRE_MATCH_INIT

            return

        # start here keycode is not modifier
        self.try_match_key(keycode, keyname, keystate)
        return

    def handle_matched_or_unmated(
        self, keycode: int, keyname: str, keystate: KeyState, is_modifier: bool
    ) -> None:
        if is_modifier:
            if keystate in {KeyState.down, KeyState.hold}:
                self._active_modifiers[keycode] = ActiveKeyInfo(
                    keystate, time.time(), 1
                )
            else:
                self._active_modifiers.pop(keycode, None)
            return

        # start here keycode is not modifier
        if keystate in {KeyState.down, KeyState.hold}:
            logger.warning(
                '%s unexpected key: %s %s',
                self.state,
                keyname,
                keystate,
            )
            return

        dst_key = (
            self._matched_key_mapping.dst_key  # type: ignore
            if self.state == KeyboardMappingState.MATCHED
            else keycode
        )
        self.send_key(dst_key, keystate)
        dst_modifiers = (
            self._matched_key_mapping.dst_modifiers  # type: ignore
            if self.state == KeyboardMappingState.MATCHED
            else self._copy_modifiers.keys()
        )
        self.send_keys(list(dst_modifiers), KeyState.up)
        self.output_device.syn()

        self._matched_key_mapping = None
        self._copy_modifiers.clear()

        if self._active_modifiers:
            self.state = KeyboardMappingState.AFTER_MATCH
        else:
            self.state = KeyboardMappingState.PRE_MATCH_INIT

        return

    def handle_after_match(
        self, keycode: int, keyname: str, keystate: KeyState, is_modifier: bool
    ) -> None:
        if is_modifier:
            if keystate in {KeyState.down, KeyState.hold}:
                self._active_modifiers[keycode] = ActiveKeyInfo(
                    keystate, time.time(), 1
                )
                return

            # start here keystate == KeyState.up
            self._active_modifiers.pop(keycode, None)
            if not self._active_modifiers:
                self.state = KeyboardMappingState.PRE_MATCH_INIT
            return

        # start here keycode is not modifier
        self.try_match_key(keycode, keyname, keystate)
        return

    def handle_input_event(self, event: evdev.InputEvent) -> None:
        event_type = event.type
        if event_type != evdev.ecodes.EV_KEY:
            logger.debug('event type %s is not EV_KEY', event_type)
            self.output_device.write_event(event)
            self.output_device.syn()
            return

        event: evdev.KeyEvent = evdev.KeyEvent(event)  # type: ignore
        keycode = event.scancode
        keyname = event.keycode
        keystate = KeyState(event.keystate)
        logger.debug('event: %s(%s)  %s', keyname, keycode, keystate)

        is_modifier = MagicKeyboard.is_modifier(keycode)

        if self.state == KeyboardMappingState.PRE_MATCH_INIT:
            return self.handle_pre_match_init(keycode, keyname, keystate, is_modifier)

        if self.state == KeyboardMappingState.PRE_MATCH_PRESSED_KEY:
            return self.handle_pre_match_pressed_key(
                keycode, keyname, keystate, is_modifier
            )

        if self.state == KeyboardMappingState.PRE_MATCH_PRESSED_MODIFIER:
            return self.handle_pre_match_pressed_modifier(
                keycode, keyname, keystate, is_modifier
            )

        if self.state in {KeyboardMappingState.MATCHED, KeyboardMappingState.UNMATCHED}:
            return self.handle_matched_or_unmated(
                keycode, keyname, keystate, is_modifier
            )

        if self.state == KeyboardMappingState.AFTER_MATCH:
            return self.handle_after_match(keycode, keyname, keystate, is_modifier)

    async def _handle_input_events(self) -> None:
        try:
            async for event in self.input_device.async_read_loop():
                self.handle_input_event(event)
        finally:
            self.input_device.close()
            self.input_device = None

    def grab(self, evloop: asyncio.AbstractEventLoop) -> None:
        dev = self.find_input_device()

        if dev is None:
            # maybe keyboard is disconnected, so try to ungrab
            logger.debug('can not find %s', self.keyboard_name)
            self.ungrab()
            return

        self.input_device = dev

        try:
            self.input_device.grab()
        except OSError as e:
            logger.debug('grab <%s> failed: %s', self.keyboard_name, e)
            return

        logger.debug('grab <%s> successful', self.keyboard_name)

        caps = self.input_device.capabilities()
        # EV_SYN is automatically added to uinput devices
        del caps[evdev.ecodes.EV_SYN]

        caps_ev_key = set(caps[evdev.ecodes.EV_KEY])
        for key_mapping in self.key_mappings:
            caps_ev_key.update(key_mapping.src_modifiers)
            caps_ev_key.add(key_mapping.src_key)
            caps_ev_key.update(key_mapping.dst_modifiers)
            caps_ev_key.add(key_mapping.dst_key)

        caps[evdev.ecodes.EV_KEY] = list(caps_ev_key)
        self.output_device = evdev.UInput.from_device(
            self.input_device, name=f'magickey-{self.input_device.name}'
        )

        self._async_task = evloop.create_task(self._handle_input_events())

    def ungrab(self) -> bool:
        if self.state != KeyboardMappingState.PRE_MATCH_INIT:
            logger.debug(
                'can not ungrab <%s> on %s', self.keyboard_name, self.state
            )
            return False

        if self.input_device:
            try:
                self.input_device.ungrab()
                self.input_device.close()
                self.input_device = None
                logger.debug('ungrab <%s> successful', self.keyboard_name)
            except (OSError, IOError) as e:
                logger.debug('ungrab <%s> failed: %s', self.keyboard_name, e)
                return False

        if self.output_device:
            try:
                self.output_device.close()
                self.output_device = None
                logger.debug('close output successful')
            except IOError:
                logger.debug('close output failed')
                return False

        if self._async_task:
            self._async_task.cancel()
            self._async_task = None

        return True


class MagicKeyboard:
    MODIFIERS: Dict[str, int] = {
        'ctrl': evdev.ecodes.KEY_LEFTCTRL,
        'left_ctrl': evdev.ecodes.KEY_LEFTCTRL,
        'right_ctrl': evdev.ecodes.KEY_RIGHTCTRL,
        'shift': evdev.ecodes.KEY_LEFTSHIFT,
        'left_shift': evdev.ecodes.KEY_LEFTSHIFT,
        'right_shift': evdev.ecodes.KEY_RIGHTSHIFT,
        'alt': evdev.ecodes.KEY_LEFTALT,
        'left_alt': evdev.ecodes.KEY_LEFTALT,
        'right_alt': evdev.ecodes.KEY_RIGHTALT,
        'meta': evdev.ecodes.KEY_LEFTMETA,
        'left_meta': evdev.ecodes.KEY_LEFTMETA,
        'right_meta': evdev.ecodes.KEY_RIGHTMETA,
        'caps_lock': evdev.ecodes.KEY_CAPSLOCK,
    }

    MODIFIER_KEY_CODES = set(MODIFIERS.values())

    def __init__(self, config_file: Union[Path, IO[str]]) -> None:
        self.parse_config(config_file)

        self.evloop = asyncio.get_event_loop_policy().get_event_loop()

    @classmethod
    def normalize_key(cls, key_name: str) -> int:
        key_name = key_name.strip().lower()

        if cls.is_modifier(key_name):
            return cls.MODIFIERS[key_name]

        if (keycode := evdev.ecodes.ecodes.get(f'KEY_{key_name.upper()}')) is None:
            raise ValueError(f'unknown key name: {key_name}')

        return keycode  # type: ignore

    @classmethod
    def keycode_to_name(cls, keycode: int) -> str:
        return evdev.ecodes.keys[keycode].removeprefix('KEY_').lower()  # type: ignore

    @classmethod
    def is_modifier(cls, keycode: Union[int, str]) -> bool:
        if isinstance(keycode, int):
            return keycode in cls.MODIFIER_KEY_CODES
        return keycode in cls.MODIFIERS

    @classmethod
    def split_key_combination(cls, key_combination: str) -> Tuple[Set[int], int]:
        keys = key_combination.split('+')
        modifiers = set()
        _key = -1

        for key in keys:
            key = key.strip().lower()
            keycode = cls.normalize_key(key)

            if cls.is_modifier(keycode):
                modifiers.add(keycode)
            else:
                _key = keycode

        return modifiers, _key

    @classmethod
    def find_all_keyboards(cls) -> List[str]:
        keyboards = []
        for path in evdev.list_devices():
            dev = evdev.InputDevice(path)
            if evdev.ecodes.EV_KEY in dev.capabilities():
                keyboards.append(dev.name)

        return keyboards

    def parse_config(self, config_file: Union[Path, IO[str]]) -> None:
        if isinstance(config_file, Path):
            f = config_file.open('r')  # type: IO[str]
        else:
            f = config_file

        config: List[Dict[str, Any]] = json.load(f)

        keyboard_mappings: List[KeyboardMapping] = []
        for item in config:
            # get key mappings
            mappings = item.get('mappings', [])
            if not mappings:
                continue

            key_mappings = []
            for mapping in mappings:
                src = mapping['src']
                dst = mapping['dst']
                src_modifiers, src_key = self.split_key_combination(src)
                dst_modifiers, dst_key = self.split_key_combination(dst)
                key_mapping = KeyMapping(src_modifiers, src_key, dst_modifiers, dst_key)
                key_mappings.append(key_mapping)

            # get keyboards
            keyboards = item.get('keyboards', self.find_all_keyboards())
            if not keyboards:
                continue

            for keyboard in keyboards:
                keyboard_mapping = KeyboardMapping(keyboard, key_mappings)
                keyboard_mapping.set_all_modifiers()
                keyboard_mappings.append(keyboard_mapping)

        self.keyboard_mappings = keyboard_mappings
        logger.debug(
            'keyboard_mappings: %s', '\n'.join(str(km) for km in keyboard_mappings)
        )

    def shutdown(self, tried_count: int = 0) -> None:
        logger.info('shutdown')
        if tried_count > 3:
            logger.debug(
                'shutdown failed after try %s times, shutdown anyway', tried_count
            )
            self.evloop.stop()
            return

        if all(
            keyboard_mapping.ungrab() for keyboard_mapping in self.keyboard_mappings
        ):
            self.evloop.stop()
            return

        self.evloop.call_later(0.1, self.shutdown, tried_count + 1)
        return

    def handle_SIGTERM(self) -> None:
        logger.info('SIGTERM received')
        self.shutdown()

    def handle_udev_event(self, monitor: pyudev.Monitor) -> None:
        device = monitor.poll(0)
        if device is None:
            return

        logger.info('udev event: %s', device)

        for keyboard_mapping in self.keyboard_mappings:
            keyboard_mapping.grab(self.evloop)

    def monitor_udev(self) -> None:
        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by('input')
        fd = monitor.fileno()
        monitor.start()
        self.evloop.add_reader(fd, self.handle_udev_event, monitor)

    def run_forever(self) -> None:
        self.evloop.add_signal_handler(signal.SIGTERM, self.handle_SIGTERM)
        self.monitor_udev()
        for keyboard_mapping in self.keyboard_mappings:
            keyboard_mapping.grab(self.evloop)
        self.evloop.run_forever()


def list_devices() -> None:
    for path in evdev.list_devices():
        device = evdev.InputDevice(path)
        print(f'{device.path:25s} {device.phys:35s} {device.name:40s}')


def read_events(req_device: str) -> None:
    input_device = None
    for path in evdev.list_devices():
        device = evdev.InputDevice(path)
        if req_device in {
            device.path,
            device.phys,
            device.name,
            device.path.removeprefix('/dev/input/event'),
        }:
            input_device = device

    if input_device is None:
        print('Device not found')
        return

    print('press ctrl-c to stop')

    for event in input_device.read_loop():
        if event.type != evdev.ecodes.EV_KEY:
            continue
        try:
            categorized = evdev.categorize(event)
            print(categorized.keycode, categorized.scancode, categorized.keystate)
        except KeyError:
            if event.value:
                print('Unknown key (%s) has been pressed.' % event.code)
            else:
                print('Unknown key (%s) has been released.' % event.code)


def main() -> None:
    parser = argparse.ArgumentParser('MagicKeyboard')
    parser.add_argument('-c', '--config', default='./magickey.conf', help='Config file')
    parser.add_argument(
        '-l',
        '--list-devices',
        action='store_true',
        help='List input devices by name and physical address',
    )
    parser.add_argument(
        '-e',
        '--read-events',
        metavar='DEVICE',
        help='Read events from an input device by either '
        'name, physical address or number.',
    )
    parser.add_argument(
        '-d', '--debug', action='store_true', default=False, help='print debug messages'
    )

    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)

    if args.list_devices:
        list_devices()
        return

    if args.read_events:
        read_events(args.read_events)
        return

    config = Path('/')  # make mypy happy
    config_files = [args.config, '~/.config/magickey/conf.json']
    for f in config_files:
        config = Path(f).expanduser()
        if config.exists():
            break
    else:
        logger.error('Config file not found, tried %s', ', '.join(config_files))
        return

    MagicKeyboard(config).run_forever()


if __name__ == '__main__':
    main()
