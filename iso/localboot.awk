# Give the BIOS menu a handover to the installed system that needs nothing
# read from the medium.
#
# archiso's own entry loads chain.c32 before it can start the disk.  That module
# has to be read out of the medium's file system first, so on a stick the
# firmware reads badly the handover fails with "edd error ... reading sector"
# and "failed to load com32 file chain.c32" instead of starting the install
# that was just finished.  LOCALBOOT is part of the boot loader itself: it asks
# the BIOS for the next boot device, which is the disk when the medium is a
# stick or a disc.
#
# Only the "existing" entry is rewritten, only when it is the chain.c32 one,
# and every other menu entry (memtest, HDT, reboot, poweroff) is left alone.
# Reads the whole file so a block can be inspected before it is replaced.

{ lines[NR] = $0 }

END {
    n = NR
    i = 1
    while (i <= n) {
        if (lines[i] ~ /^LABEL[[:space:]]+existing[[:space:]]*$/) {
            j = i + 1
            while (j <= n && lines[j] !~ /^[[:space:]]*$/ && lines[j] !~ /^LABEL[[:space:]]/) {
                j++
            }
            block = ""
            for (k = i; k < j; k++) {
                block = block lines[k] "\n"
            }
            if (block ~ /COM32[[:space:]]+chain\.c32/) {
                print "LABEL existing"
                print "TEXT HELP"
                print "Hand the boot over to the next boot device, which is where the"
                print "installed system lives. If this menu comes back, take the install"
                print "medium out and the firmware boots the disk."
                print "ENDTEXT"
                print "MENU LABEL Boot the installed system"
                print "LOCALBOOT -1"
            } else {
                printf "%s", block
            }
            i = j
            continue
        }
        print lines[i]
        i++
    }
}
