#!/bin/sh
# Wait (bounded) for the USB ReSpeaker capture device to be enumerated.
#
# The ReSpeaker is hot-plugged USB audio; sound.target is reached long before
# udev finishes enumerating it, so on boot bmo-voice can open the mic before the
# card exists and crash-loop. This gives udev up to ~30 s. It always exits 0:
# if the card never appears, the service still starts and Restart=always is the
# backstop.
set -u

i=0
while [ "$i" -lt 30 ]; do
    if arecord -l 2>/dev/null | grep -qiE 'respeaker|xmos|xu316'; then
        exit 0
    fi
    i=$((i + 1))
    sleep 1
done
exit 0
