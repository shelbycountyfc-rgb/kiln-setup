"""The Kiln package manager: pacman's repositories and the AUR in one window.

Two kinds of package are handled here and they are not the same thing.  A
package in an official repository is a binary that ``pacman`` downloads and
installs.  A package in the AUR is a recipe: it has to be built on this machine
by ``makepkg``, as a normal user, with ``sudo`` for the final install.  That is
why ``yay`` exists and why this window treats AUR entries separately instead of
pretending they install the same way as ``extra`` entries.
"""

__version__ = "1.0"
