# X session of the live medium: a dark root window and the installer.
#
# X blanks the display after ten idle minutes by default.  An installation
# spends longer than that inside pacstrap with nobody touching the keyboard, so
# the screen would go black halfway through and look like a crash.  Blanking and
# the DPMS power-off are switched off for as long as the installer runs.
xset s off -dpms s noblank 2>/dev/null || true
xsetroot -solid '#14171c' 2>/dev/null || true
exec /usr/local/bin/kiln-setup
