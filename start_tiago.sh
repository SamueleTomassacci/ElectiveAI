#!/bin/bash

# Start TIAGo simulation container with NVIDIA GPU support

DOCKER_GPU_ARGS="--env DISPLAY \
  --env QT_X11_NO_MITSHM=1 \
  --env __NV_PRIME_RENDER_OFFLOAD=1 \
  --env __GLX_VENDOR_LIBRARY_NAME=nvidia \
  --volume=/tmp/.X11-unix:/tmp/.X11-unix:rw"


PULSE_SOCKET="/run/user/$(id -u)/pulse/native"
PULSE_COOKIE="$HOME/.config/pulse/cookie"
if [ -S "$PULSE_SOCKET" ]; then
  PULSE_ARGS="--volume=${PULSE_SOCKET}:/run/pulse/native \
    --env PULSE_SERVER=unix:/run/pulse/native"
  if [ -f "$PULSE_COOKIE" ]; then
    PULSE_ARGS="${PULSE_ARGS} --volume=${PULSE_COOKIE}:/root/.config/pulse/cookie:ro"
  fi
else
  echo "WARNING: PulseAudio socket not found at ${PULSE_SOCKET}, microphone may not work"
  PULSE_ARGS=""
fi

dpkg -l | grep nvidia-container-toolkit &> /dev/null

HAS_NVIDIA_TOOLKIT=$?
# HAS_NVIDIA_TOOLKIT=1 Uncomment if you want to force the use of the CPU only simulation

if [ $HAS_NVIDIA_TOOLKIT -eq 0 ]; then
  docker_version=$(docker version --format '{{.Client.Version}}' | cut -d. -f1)
  if [ "$docker_version" -ge 19 ]; then
    DOCKER_COMMAND="docker run --gpus all"
  else
    DOCKER_COMMAND="docker run --runtime=nvidia"
  fi
else
  echo "WARNING: nvidia-container-toolkit not found, running without GPU"
  DOCKER_COMMAND="docker run"
fi

IMAGE_NAME="hrai-2025:v1"
CONTAINER_NAME="hrai_sim"

# Local repository mounted into the container under /root/exchange
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOUNT_ARGS="-v ${SCRIPT_DIR}:/root/exchange"

xhost +local:docker

$DOCKER_COMMAND \
  $DOCKER_GPU_ARGS \
  $PULSE_ARGS \
  $MOUNT_ARGS \
  --net host \
  --privileged \
  --rm \
  -it \
  --name "$CONTAINER_NAME" \
  -w "/root/exchange" \
  "$IMAGE_NAME" \
  "/root/exchange/docker/entrypoint_tiago.sh"
