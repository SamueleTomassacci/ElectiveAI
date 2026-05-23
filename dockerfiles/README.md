# TIAGo Docker Image

This folder contains the Docker build context for the TIAGo simulation image.

Build from the repository root:

```bash
docker build -t hrai-2025:v1 dockerfiles
```

The image installs ROS 2 Humble dependencies, imports the public PAL TIAGo repositories, builds `/root/tiago_public_ws`, and starts through `entrypoint_tiago.sh`.
