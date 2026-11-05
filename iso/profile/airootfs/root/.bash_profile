# Start the graphical installer on tty1, and leave a shell on the other
# consoles and whenever X cannot start.
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
  exec /usr/local/bin/kiln-setup-session
fi

# Root's interactive shell everywhere else.
PS1='\[\e[1;36m\]installer\[\e[0m\] \w # '
export PS1
