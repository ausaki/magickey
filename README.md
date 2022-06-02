# A keyboard remapping tool for sway

magickey is a tool like autohotkey(or autokey on linux) but not that such powerfull,
it can only remap combination keys to another combination keys, such as remap `alt+l` to `right` or remap `alt+h` to `left`.

## How to use

### Install

There is just a simple python file `magickey.py` for magickey, so you can copy it to your machine or clone this repo to your machine, or any other methods you like.

> For now, there is no PyPI package or linux distribution package

magickey is depends on `evdevl` and `pyudev`, running `pip install -r requirements.txt` to install them.

### Run

There are two ways to run magickey:

- manually

  `sudo python ./magickey.py -u UID`

  need root privilege to read and write input events.

- auto run when system startup

  - copy `magickey.service.exmaple` to your machine, and rename it to `magickey.service`.
  - edit `magickey.service`, you'll know what you should change.
  - run `sudo ln -s /path/to/magickey.service /etc/systemd/system/magickey.service`.
  - run `sudo systemctl enable magickey.service`
  - run `sudo systemctl start magickey.service`

### Configration

magickey will try to discover configration file on following locations:

- `$PWD/conf.json`
- `$HOME/.config/magickey/conf.json`
- `-c CONFIG_FILE` option

configration file is a json file.

- root is a list of object, every inner object is a keyboard mapping.

- kerboard mapping object supports following fileds:

  - `keyboards`: a list of keyboard name(or keyboard device id), use `magickey.py -l` to see the keyboards your system has connected.

  - `mappings`: a list of key mapping object, it supports following fields:

    - `src`: source keys combination you want to remaped
    - `dst`: target keys combination you want to remapped to
    
    following four fields are used to filter window, they are include two fiels `class` and `title`, correspond to window's class and title. note these four fields are exclusive.
    
    both `class` and `title` are python regex string.

    - `match`: both `class` and `title` are match with current window's class and title.
    - `match_or`: one of `class` or `title` is match with current window's class or title.
    - `match_not`: both `class` and `title` are not match with current window's class and title.
    - `match_not_or`: one of `class` or `title` is not match with current window's class and title.

    run `swaymsg -t get_tree | jq -r '..|try select(.type == "con" and .focused)'` to get window's class or title.

    if the window is running on native wayland, then `class`(`title`) ia against the `app_id`(`name`) field in the out of `swaymsg -t get_tree`. 
    if the window is running on xwayland, then `class`(`title`) is against the `window_properties.class`(`window_properties.title`) field in the out of `swaymsg -t get_tree`. 

<details>
  <summary><bold>full configration file exmaple</bold></summary>
  
  ```json
  [
    {
      "keyboards": ["AT Translated Set 2 keyboard", "Keyboard K380 Keyboard"],
      "mappings": [
        {
          "src": "alt+c",
          "dst": "ctrl+c",
          "match_not": {
            "class": "(?i)^alacritty"
          }
        },
        {
          "src": "alt+v",
          "dst": "ctrl+v",
          "match_not": {
            "class": "(?i)^alacritty"
          }
        },
        {
          "src": "alt+x",
          "dst": "ctrl+x",
          "match_not": {
            "class": "(?i)^alacritty"
          }
        },
        {
          "src": "alt+z",
          "dst": "ctrl+z",
          "match_not": {
            "class": "(?i)^alacritty"
          }
        },
        {
          "src": "alt+shift+z",
          "dst": "ctrl+shift+z"
        },
        {
          "src": "alt+left",
          "dst": "home"
        },
        {
          "src": "alt+shift+left",
          "dst": "shift+home"
        },
        {
          "src": "alt+a",
          "dst": "home"
        },
        {
          "src": "alt+shift+a",
          "dst": "shift+home"
        },
        {
          "src": "alt+right",
          "dst": "end"
        },
        {
          "src": "alt+shift+right",
          "dst": "shift+end"
        },
        {
          "src": "alt+e",
          "dst": "end"
        },
        {
          "src": "alt+shift+e",
          "dst": "shift+end"
        },
        {
          "src": "alt+up",
          "dst": "ctrl+home"
        },
        {
          "src": "alt+shift+up",
          "dst": "ctrl+shift+home"
        },
        {
          "src": "alt+down",
          "dst": "ctrl+end"
        },
        {
          "src": "alt+shift+down",
          "dst": "ctrl+shift+end"
        },
        {
          "src": "meta+left",
          "dst": "ctrl+left"
        },
        {
          "src": "meta+shift+left",
          "dst": "ctrl+shift+left"
        },
        {
          "src": "alt+b",
          "dst": "ctrl+left"
        },
        {
          "src": "alt+shift+b",
          "dst": "ctrl+shift+left"
        },
        {
          "src": "meta+backspace",
          "dst": "ctrl+backspace"
        },
        {
          "src": "meta+right",
          "dst": "ctrl+right"
        },
        {
          "src": "meta+shift+right",
          "dst": "ctrl+shift+right"
        },
        {
          "src": "alt+f",
          "dst": "ctrl+right"
        },
        {
          "src": "alt+shift+f",
          "dst": "ctrl+shift+right"
        },
        {
          "src": "meta+delete",
          "dst": "ctrl+delete"
        },
        {
          "src": "alt+h",
          "dst": "left"
        },
        {
          "src": "alt+shift+h",
          "dst": "shift+left"
        },
        {
          "src": "alt+l",
          "dst": "right"
        },
        {
          "src": "alt+shift+l",
          "dst": "shift+right"
        },
        {
          "src": "alt+j",
          "dst": "down"
        },
        {
          "src": "alt+shift+j",
          "dst": "shift+down"
        },
        {
          "src": "alt+k",
          "dst": "up"
        },
        {
          "src": "alt+shift+k",
          "dst": "shift+up"
        },
        {
          "src": "alt+semicolon",
          "dst": "ctrl+backspace"
        },
        {
          "src": "alt+apostrophe",
          "dst": "ctrl+delete"
        }
      ]
    }
  ]
  ```
</details>




## How it works

First thing first, on linux, you can get every keys user typed, and you can send out any key to system via a virtual keyboard.

Run `ls -l /dev/input/event*` in your terminal, you can see a lot files, these are so called "input device" - a character device.

I don't know much about linux I/O driver, so that's all. If you want to know more information about "input device", please see:

- [Reading events - python-evdev](https://python-evdev.readthedocs.io/en/latest/tutorial.html#reading-events)
- [send out keys - python-evdev](https://python-evdev.readthedocs.io/en/latest/tutorial.html#injecting-input)

### Key state machine

```UML
title magic keyboard

PRE_MATCH_INIT -> PRE_MATCH_PRESSED_KEY: press key(*)
PRE_MATCH_INIT -> PRE_MATCH_PRESSED_MODIFIER: press modifier key(*)

PRE_MATCH_PRESSED_KEY -> PRE_MATCH_PRESSED_KEY: press or release key(*)
PRE_MATCH_PRESSED_KEY -> PRE_MATCH_PRESSED_KEY:  press modifier key(-)
PRE_MATCH_PRESSED_KEY -> PRE_MATCH_INIT: release key and no any key is hold(*)

PRE_MATCH_PRESSED_MODIFIER -> PRE_MATCH_PRESSED_MODIFIER: press or release modifier(*)
PRE_MATCH_PRESSED_MODIFIER -> PRE_MATCH_INIT: release modifier and no any modifier is hold(*)

PRE_MATCH_PRESSED_MODIFIER -> MATCHED: press key and find a key mapping is match(*)
PRE_MATCH_PRESSED_MODIFIER -> UNMATCHED: press key and no key mapping is match(*)

MATCHED -> MATCHED: press or release modifier(-)
MATCHED -> PRE_MATCH_INIT: release modifier and no modifier is hold(-)
MATCHED -> MATCHED: press key and find a key mapping is match(*)
MATCHED -> UNMATCHED: press key and no key mapping is match(*)

UNMATCHED -> UNMATCHED: press or release modifier(-)
UNMATCHED -> PRE_MATCH_INIT: release modifier and no modifier is hold(-)
UNMATCHED -> MATCHED: press key and find a key mapping is match(*)
UNMATCHED -> UNMATCHED: press key and no key mapping is match(*)


note left of PRE_MATCH_INIT
    *: send out keys
    -: do nothing
end note
```

> see graphic version in here: https://www.websequencediagrams.com/
