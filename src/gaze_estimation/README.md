# gaze_estimation

Standalone ROS 2 package for webcam-based gaze target estimation.

This package opens the computer RGB webcam with OpenCV, detects a face, estimates a Gazelle/Gaze-LLE gaze heatmap, detects target objects with YOLO-World, and reports whether the estimated gaze falls on one of the target object bounding boxes.

It is intentionally independent from `empower`, `llm_interaction`, EMPOWER, TIAGo, Gazebo, Nav2, HRI topics, `/hri/*`, `/empower/*`, and existing TCP sockets.

It does not use RealSense, `pyrealsense2`, `.bag` files, depth frames, point clouds, or saved images as mandatory input.

## Build

From the workspace root:

```bash
source /opt/ros/humble/setup.bash
colcon build --symlink-install --packages-select gaze_estimation
source install/setup.bash
```

## Python Dependencies

Install the non-ROS Python dependencies in the environment where the node runs:

```bash
pip install torch torchvision opencv-python numpy ultralytics timm pillow
```

Use the PyTorch install command appropriate for your CPU/CUDA environment.

## Model Weights

Large weights are not versioned in this repository.

By default, put them in:

```text
models/gaze_estimation/
  gazelle_dinov2_vitl14_inout.pt
  yolov8x-worldv2.pt
  best.pt
```

The default YAML points to:

```text
/root/exchange/models/gaze_estimation
```

Update `config/gaze_webcam.yaml` or pass another params file if your workspace lives elsewhere.

If a checkpoint is missing, the node prints a clear message with the expected path instead of failing with an unclear model stacktrace.

## Launch

```bash
ros2 launch gaze_estimation gaze_webcam.launch.py
```

Common overrides:

```bash
ros2 launch gaze_estimation gaze_webcam.launch.py \
  camera_index:=0 \
  device:=auto \
  target_classes:="book,bottle,cup"
```

You can also override the full parameter file:

```bash
ros2 launch gaze_estimation gaze_webcam.launch.py \
  params_file:=/path/to/gaze_webcam.yaml
```

## Main Parameters

- `camera_index`: OpenCV webcam index, default `0`.
- `device`: `auto`, `cpu`, or `cuda`.
- `target_classes`: YOLO-World classes to detect.
- `resize_width`: frame width before inference; use `0` to keep the original size.
- `process_every_n_frames`: inference cadence.
- `yolo_confidence`: YOLO-World confidence threshold.
- `gaze_inout_threshold`: minimum Gazelle in/out score to trust an in-frame gaze.
- `bbox_margin_px`: extra object box margin for point-in-box checks.
- `use_heatmap_ratio_check`: also accept object boxes with enough gaze heatmap mass.
- `heatmap_inside_bbox_threshold`: minimum heatmap mass ratio inside an object box.
- `weights_dir`: directory containing Gazelle, YOLO-World, and face detector weights.

## CPU/GPU

Device selection is centralized:

- `device: auto`: use CUDA if `torch.cuda.is_available()`, otherwise CPU.
- `device: cpu`: force CPU.
- `device: cuda`: use CUDA if available; otherwise warn clearly and fall back to CPU.

Gazelle is moved with `.to(device)`. YOLO/YOLO-World use `device=0` for CUDA and `device="cpu"` otherwise.

## Runtime Behavior

The live OpenCV window shows:

- detected face box;
- gaze arrow and heatmap overlay;
- target object boxes and confidence;
- status text.

Typical statuses are:

- `LOOKING_AT_OBJECT: <class>`
- `LOOKING_ELSEWHERE`
- `NO_FACE`
- `NO_TARGET_OBJECT`
- `GAZE_OUT_OF_FRAME`

Press `q` in the OpenCV window or Ctrl+C in the terminal to exit.

## Limits

This is a probabilistic gaze-target estimate, not a calibrated eye tracker. It assumes the face and target object are visible in the same RGB frame. Accuracy depends heavily on lighting, camera placement, face detector quality, YOLO-World classes, and the Gazelle checkpoint.
