"""The Xfce desktop this installer sets up.

Xfce is light, but its default look is grey and dated.  This module gives it
what the medium calls the Kiln look: the Materia-dark GTK and window theme, the
Papirus-Dark icon set, a dark panel across the top with the Kiln accent, the
wallpapers drawn by ``iso/wallpapers/make-wallpapers.py`` behind it and a login
screen that matches.

Everything is a file Xfce itself reads from ``/etc/xdg``, so every account
created later gets the same desktop at its first login.  Nothing is fetched
from the network: the wallpapers travel inside this package and are copied into
the new system here.
"""

from __future__ import annotations

import os

#: What the look is made of.  Both packages are in [extra] and options.py adds
#: them to the Xfce package list, so an install that chose Xfce has them.
#: Materia-dark carries GTK 2, 3 and 4 *and* the xfwm4 window decorations,
#: which is why it is the theme here: a GTK-only theme leaves the title bars
#: grey, and Arc, the other obvious choice, is not in the repositories.
THEME = "Materia-dark"
ICONS = "Papirus-Dark"
CURSOR = "Adwaita"
FONT = "Noto Sans 10"
MONO_FONT = "Noto Sans Mono 10"
PANEL_SIZE = 30

WALLPAPER_DIR = "/usr/share/backgrounds/kiln"
WALLPAPERS = ("kiln-001-aurora.png", "kiln-002-grid.png", "kiln-003-ember.png")
DEFAULT_WALLPAPER = WALLPAPERS[0]

#: Monitor keys Xfce writes its wallpaper under.  ``monitor0`` is the name it
#: has always used; the virtual outputs are what a plain QEMU or VirtualBox
#: machine gets, so both are described and whichever exists carries the image.
_MONITORS = ("0", "Virtual-0", "Virtual-1")

_XSETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<channel name="xsettings" version="1.0">
  <property name="Net" type="empty">
    <property name="ThemeName" type="string" value="%s"/>
    <property name="IconThemeName" type="string" value="%s"/>
  </property>
  <property name="Gtk" type="empty">
    <property name="FontName" type="string" value="%s"/>
    <property name="MonospaceFontName" type="string" value="%s"/>
    <property name="MenuImages" type="bool" value="true"/>
    <property name="ButtonImages" type="bool" value="true"/>
    <property name="CursorThemeName" type="string" value="%s"/>
    <property name="CursorThemeSize" type="int" value="24"/>
  </property>
  <property name="Xft" type="empty">
    <property name="Antialias" type="int" value="1"/>
    <property name="HintStyle" type="string" value="hintslight"/>
    <property name="RGBA" type="int" value="1"/>
  </property>
</channel>
""" % (THEME, ICONS, FONT, MONO_FONT, CURSOR)

_XFWM4 = """<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfwm4" version="1.0">
  <property name="general" type="empty">
    <property name="theme" type="string" value="%s"/>
    <property name="title_font" type="string" value="Noto Sans Bold 10"/>
    <property name="title_alignment" type="string" value="center"/>
    <property name="title_horizontal_offset" type="int" value="0"/>
    <property name="use_compositing" type="bool" value="true"/>
    <property name="box_move" type="bool" value="true"/>
    <property name="box_resize" type="bool" value="true"/>
    <property name="snap_to_border" type="bool" value="true"/>
    <property name="snap_to_windows" type="bool" value="true"/>
    <property name="wrap_windows" type="bool" value="true"/>
    <property name="button_layout" type="string" value="|HMC"/>
    <property name="workspace_count" type="int" value="4"/>
  </property>
</channel>
""" % THEME

_PANEL = """<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-panel" version="1.0">
  <property name="configver" type="int" value="2"/>
  <property name="panels" type="array">
    <value type="int" value="1"/>
    <property name="dark-mode" type="bool" value="true"/>
    <property name="panel-1" type="empty">
      <property name="position" type="string" value="p=6;x=0;y=0"/>
      <property name="position-locked" type="bool" value="true"/>
      <property name="length" type="uint" value="100"/>
      <property name="size" type="uint" value="%d"/>
      <property name="autohide-behavior" type="uint" value="0"/>
      <property name="background-style" type="uint" value="1"/>
      <property name="background-rgba" type="array">
        <value type="double" value="0.031373"/>
        <value type="double" value="0.047059"/>
        <value type="double" value="0.070588"/>
        <value type="double" value="0.960000"/>
      </property>
      <property name="enter-opacity" type="uint" value="100"/>
      <property name="leave-opacity" type="uint" value="100"/>
      <property name="plugin-ids" type="array">
        <value type="int" value="1"/>
        <value type="int" value="2"/>
        <value type="int" value="3"/>
        <value type="int" value="4"/>
        <value type="int" value="5"/>
        <value type="int" value="6"/>
      </property>
    </property>
  </property>
  <property name="plugins" type="empty">
    <property name="plugin-1" type="string" value="applicationsmenu"/>
    <property name="plugin-2" type="string" value="launcher">
      <property name="items" type="array">
        <value type="string" value="xfce4-terminal.desktop"/>
      </property>
    </property>
    <property name="plugin-3" type="string" value="tasklist">
      <property name="grouping" type="bool" value="true"/>
      <property name="show-labels" type="bool" value="true"/>
      <property name="flat-buttons" type="bool" value="true"/>
    </property>
    <property name="plugin-4" type="string" value="separator">
      <property name="expand" type="bool" value="true"/>
      <property name="style" type="uint" value="0"/>
    </property>
    <property name="plugin-5" type="string" value="systray">
      <property name="names-visible" type="array">
        <value type="string" value="network"/>
        <value type="string" value="sound"/>
        <value type="string" value="battery"/>
      </property>
    </property>
    <property name="plugin-6" type="string" value="clock">
      <property name="digital-format" type="string" value="%%a %%d %%b  %%H:%%M"/>
      <property name="mode" type="uint" value="2"/>
    </property>
  </property>
</channel>
""" % PANEL_SIZE

_SESSION = """<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-session" version="1.0">
  <property name="general" type="empty">
    <property name="SplashScreen" type="bool" value="false"/>
    <property name="LockScreen" type="bool" value="true"/>
    <property name="SaveOnExit" type="bool" value="false"/>
    <property name="AutoLockScreen" type="bool" value="true"/>
  </property>
  <property name="shutdown" type="empty">
    <property name="ShowSuspend" type="bool" value="true"/>
    <property name="ShowHibernate" type="bool" value="true"/>
  </property>
</channel>
"""

#: Thunar, set up for reading rather than rummaging: a details list, folders
#: first, thumbnails for local files, and no hidden files or single-click.
_THUNAR = """<?xml version="1.0" encoding="UTF-8"?>
<channel name="thunar" version="1.0">
  <property name="last-view" type="string" value="ThunarDetailsView"/>
  <property name="last-icon-view-zoom-level" type="string" value="THUNAR_ZOOM_LEVEL_NORMAL"/>
  <property name="last-details-view-zoom-level" type="string" value="THUNAR_ZOOM_LEVEL_38"/>
  <property name="misc-date-style" type="string" value="THUNAR_DATE_STYLE_SHORT"/>
  <property name="misc-folders-first" type="bool" value="true"/>
  <property name="misc-thumbnail-mode" type="string" value="THUNAR_THUMBNAIL_MODE_ONLY_LOCAL"/>
  <property name="misc-single-click" type="bool" value="false"/>
  <property name="misc-show-delete-action" type="bool" value="true"/>
  <property name="misc-volume-management" type="bool" value="false"/>
</channel>
"""

_GREETER = """[greeter]
background=%s/%s
theme-name=%s
icon-theme-name=%s
font-name=%s
xft-antialias=true
xft-hintstyle=hintslight
clock-format=%%H:%%M
indicators=~host;~spacer;~clock;~spacer;~session;~power
""" % (WALLPAPER_DIR, DEFAULT_WALLPAPER, THEME, ICONS, FONT)

_DESKTOP_HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-desktop" version="1.0">
  <property name="backdrop" type="empty">
    <property name="screen0" type="empty">
"""

_DESKTOP_MONITOR = """      <property name="monitor%s" type="empty">
        <property name="workspace0" type="empty">
          <property name="color-style" type="int" value="0"/>
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="%s"/>
          <property name="backdrop-cycle-enable" type="bool" value="false"/>
        </property>
        <property name="workspace1" type="empty">
          <property name="color-style" type="int" value="0"/>
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="%s"/>
        </property>
      </property>
"""

_DESKTOP_TAIL = """    </property>
  </property>
  <property name="desktop-icons" type="empty">
    <property name="style" type="int" value="0"/>
    <property name="icon-size" type="uint" value="48"/>
  </property>
  <property name="last-settings-migration-version" type="uint" value="1"/>
</channel>
"""


def settings() -> dict:
    """What the look is, for the window and for the checks."""
    return {
        "desktop": "xfce",
        "theme": THEME,
        "icons": ICONS,
        "font": FONT,
        "panel_size": PANEL_SIZE,
        "wallpapers": list(WALLPAPERS),
        "wallpaper": "%s/%s" % (WALLPAPER_DIR, DEFAULT_WALLPAPER),
    }


def desktop_xml(image: str = None) -> str:
    """The xfce4-desktop channel, with ``image`` on every workspace."""
    image = image or "%s/%s" % (WALLPAPER_DIR, DEFAULT_WALLPAPER)
    body = "".join(_DESKTOP_MONITOR % (name, image, image) for name in _MONITORS)
    return _DESKTOP_HEAD + body + _DESKTOP_TAIL


def wallpaper_source() -> str:
    """The directory the drawn wallpapers travel in."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "wallpapers")


def wallpapers() -> list:
    """Names of the wallpapers this package carries, in a stable order."""
    source = wallpaper_source()
    try:
        found = [name for name in os.listdir(source) if name.lower().endswith(".png")]
    except OSError:
        return list(WALLPAPERS)
    return sorted(found) or list(WALLPAPERS)


def _copy_wallpapers(ctx) -> list:
    """Copy the wallpapers into the new system; returns their target paths."""
    source = wallpaper_source()
    destination = os.path.join(ctx.target, WALLPAPER_DIR.lstrip("/"))
    copied = []
    for name in wallpapers():
        origin = os.path.join(source, name)
        if not os.path.isfile(origin):
            continue
        path = os.path.join(destination, name)
        if not ctx.dry_run:
            os.makedirs(destination, exist_ok=True)
            with open(origin, "rb") as reader, open(path, "wb") as writer:
                writer.write(reader.read())
        copied.append(os.path.join(WALLPAPER_DIR, name))
    return copied


def apply(ctx, cfg) -> list:
    """Write the Xfce look into the target.  Returns the files written."""
    if (getattr(cfg, "desktop", "") or "").lower() != "xfce":
        return []
    ctx.log.log("giving Xfce the Kiln look: %s, %s" % (THEME, ICONS), "step")
    written = _copy_wallpapers(ctx)
    files = (
        ("etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xsettings.xml", _XSETTINGS),
        ("etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfwm4.xml", _XFWM4),
        ("etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml", _PANEL),
        ("etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfce4-desktop.xml", desktop_xml()),
        # The login screen is the first thing seen, so it gets the same wall.
        ("etc/lightdm/lightdm-gtk-greeter.conf", _GREETER),
        # Xfce reads this to pick the GTK theme for apps that ask xsettings.
        ("etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfce4-session.xml", _SESSION),
        ("etc/xdg/xfce4/xfconf/xfce-perchannel-xml/thunar.xml", _THUNAR),
    )
    for relpath, text in files:
        written.append(ctx.write_target(relpath, text))
    return written
