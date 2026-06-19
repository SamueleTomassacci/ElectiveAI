#!/usr/bin/env bash
# Publish a stop_gaze command to /mission/gaze_command.
# Usage: ./scripts/stop_gaze.sh
ros2 topic pub --once /mission/gaze_command std_msgs/msg/String \
  "{data: '{\"command\": \"stop_gaze\"}'}"
