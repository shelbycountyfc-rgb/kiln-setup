"""The name this project goes by, and the disclaimer that goes with it.

The Arch Linux trademark policy
(https://terms.archlinux.org/docs/trademark-policy/) allows a project to say it
is "based on" or "derived from" Arch Linux, but not to call its product Arch
Linux, and not to suggest that the Arch Linux project approves or supports it.
The medium, the window, the release and the installed files therefore carry this
project's own name, and Arch Linux is mentioned only as the distribution being
installed.

Keep the naming in one place: changing PRODUCT here renames the window, the ISO
label and the boot menu text together.
"""

PRODUCT = "Kiln"
PRODUCT_TITLE = "Kiln Setup"
PRODUCT_SLUG = "kiln-setup"

#: What this software installs. Named, never claimed.
UPSTREAM = "Arch Linux"
UPSTREAM_GUIDE = "https://wiki.archlinux.org/title/Installation_guide"

#: Volume label of the live medium (FAT/UDF labels are upper case).
ISO_LABEL = "KILN_SETUP"

TAGLINE = "an unofficial graphical installer for Arch Linux"

DISCLAIMER = (
    "Kiln Setup is an unofficial project. It is not affiliated with, endorsed by, "
    "or supported by the Arch Linux project, and this is not an official Arch "
    "Linux installation medium."
)

SHORT_DISCLAIMER = (
    "Unofficial installer for Arch Linux. Not affiliated with the Arch Linux project."
)
