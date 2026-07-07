#!/usr/bin/env bash
set -eo pipefail

set +u
if [[ -f /opt/ros/humble/setup.bash ]]; then
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
fi

if [[ -f /root/tiago_public_ws/install/setup.bash ]]; then
  # shellcheck disable=SC1091
  source /root/tiago_public_ws/install/setup.bash
fi

if [[ -f /root/exchange/install/setup.bash ]]; then
  # shellcheck disable=SC1091
  source /root/exchange/install/setup.bash
fi
set -u

export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
unset FASTRTPS_DEFAULT_PROFILES_FILE

disable_base_lidar_gazebo_sensor() {
  if [[ "${TIAGO_DISABLE_BASE_LIDAR:-1}" != "1" ]]; then
    return
  fi

  local candidates=(
    "/root/tiago_public_ws/install/pal_urdf_utils/share/pal_urdf_utils/urdf/laser/sick_tim571_laser.urdf.xacro"
    "/root/tiago_public_ws/src/pal_urdf_utils/urdf/laser/sick_tim571_laser.urdf.xacro"
  )
  local file
  local enabled_call='    <xacro:sick_tim571_laser_gazebo name="${name}" ros_topic="${ros_topic}" update_rate="${update_rate}" min_angle="${min_angle}" max_angle="${max_angle}"/>'
  local disabled_call='    <xacro:if value="false"><xacro:sick_tim571_laser_gazebo name="${name}" ros_topic="${ros_topic}" update_rate="${update_rate}" min_angle="${min_angle}" max_angle="${max_angle}"/></xacro:if>'

  for file in "${candidates[@]}"; do
    [[ -f "$file" ]] || continue

    if grep -q '<xacro:if value="false"><xacro:sick_tim571_laser_gazebo' "$file"; then
      echo "TIAGo base lidar Gazebo sensor already disabled in $file"
      continue
    fi

    if grep -Fq "$enabled_call" "$file"; then
      sed -i "s#${enabled_call}#${disabled_call}#" "$file"
      echo "Disabled TIAGo base lidar Gazebo sensor in $file"
    fi
  done
}

disable_base_lidar_gazebo_sensor

# Allow a custom tiago launch command via env.
if [[ -n "${TIAGO_CMD:-}" ]]; then
  exec bash -lc "${TIAGO_CMD}"
fi

if [[ $# -gt 0 ]]; then
  exec "$@"
fi

exec bash
