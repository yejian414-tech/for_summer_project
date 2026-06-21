#!/bin/bash
# TORCS WSLg Launcher with aggressive window positioning
export DISPLAY=:0
export LIBGL_ALWAYS_SOFTWARE=1
export GALLIUM_DRIVER=llvmpipe

cd /home/yejian/torcs/BUILD

# Launch TORCS
./bin/torcs -s &
TORCS_PID=$!

echo "TORCS PID: $TORCS_PID"

# Wait and reposition window
for i in $(seq 1 30); do
    sleep 0.3
    WIN=$(xdotool search --pid $TORCS_PID 2>/dev/null | head -1)
    if [ -n "$WIN" ]; then
        echo "Found torcs window: $WIN at attempt $i"
        xdotool windowmap "$WIN" 2>/dev/null
        xdotool windowmove "$WIN" 0 0 2>/dev/null
        xdotool windowsize "$WIN" 800 600 2>/dev/null
        xdotool windowraise "$WIN" 2>/dev/null
        xdotool windowactivate "$WIN" 2>/dev/null
        echo "Window repositioned to 0,0"
        break
    fi
done

sleep 2
echo "=== Window tree ==="
xwininfo -root -tree 2>/dev/null | head -20
echo "=== Weston log ==="
tail -8 /mnt/wslg/weston.log 2>/dev/null

wait $TORCS_PID 2>/dev/null
