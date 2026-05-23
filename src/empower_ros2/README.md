# empower_ros2

ROS 2 Humble adapters for the upstream `Lab-RoCoCo-Sapienza/empower` pipeline on TIAGo/Gazebo.

The port intentionally stops at symbolic plan generation. It does not launch or implement Nav2, MoveIt2, `cmd_vel`, manipulation, GRAB/DROP/NAVIGATE execution, or any physical primitive.

## Upstream Logic Reused

Vendored from `empower`:

- `agents.py`: OpenAI/GPT vision/planning prompts.
- `detection.py`: relation extraction, class extraction, YOLO-World detection, EfficientViT-SAM masks, grounding and dump format.
- `models.py`: YOLO-World and EfficientViT-SAM wrappers.
- `efficientvit/` and `yolo_world/`: model code required by `models.py`.
- `config/yolow/*.py`: YOLO-World config expected by `models.py`.

Read but not executed:

- `low_level_execution.py`
- `primitive_actions.py`

The ROS 2 code only adapts topic access, file locations, launches, RViz publishers and Docker dependencies.

## Outputs

Default output root:

```text
/root/exchange/empower_data
```

Per use case:

```text
empower_data/images/<use_case>/scan.jpg
empower_data/output/<use_case>/depth.npy
empower_data/output/<use_case>/depth_pointcloud.pcd
empower_data/output/<use_case>/camera_info.json
empower_data/output/<use_case>/planning.txt
empower_data/output/<use_case>/planning.json
empower_data/output/<use_case>/detection.pkl
empower_data/output/<use_case>/mask.jpg
empower_data/output/<use_case>/scan_with_bb.jpg
empower_data/output/<use_case>/colored_pcl.pcd
empower_data/output/<use_case>/colored_image.png
empower_data/output/<use_case>/colors_dict.pkl
```

## Main Parameters

Configured in `config/empower_tiago.yaml`:

- `rgb_topic`
- `depth_topic`
- `camera_info_topic`
- `pointcloud_topic`
- `fixed_frame`
- `camera_frame`
- `use_case`
- `task_description`
- `output_root`

Use `depth_unit_scale_to_mm: -1.0` for auto mode. Floating depth images are converted from meters to millimeters to preserve the original `empower` point cloud assumptions; integer depth images are treated as millimeters.

## Run Order

Build the mounted workspace inside the container:

```bash
cd /root/exchange
colcon build --symlink-install --packages-select empower_ros2
source install/setup.bash
```

Launch TIAGo/Gazebo in another terminal, then capture one RGB-D scene:

```bash
ros2 launch empower_ros2 empower_capture.launch.py
```

Keep the model cacher running:

```bash
ros2 launch empower_ros2 empower_models_cacher.launch.py
```

Trigger planning:

```bash
ros2 launch empower_ros2 empower_plan.launch.py
```

Publish the colored point cloud and centroid/name markers:

```bash
ros2 launch empower_ros2 empower_visualization.launch.py
```

The generated plan is only printed and saved; no low-level action is executed.

## Checkpoints

Docker places model assets under:

```text
/opt/empower_models/config
```

Expected files:

```text
/opt/empower_models/config/yolow/yolow.pth
/opt/empower_models/config/yolow/yolo_world_l_t2i_bn_2e-4_100e_4x8gpus_obj365v1_goldg_train_lvis_minival.py
/opt/empower_models/config/efficientvitsam/l2_encoder.onnx
/opt/empower_models/config/efficientvitsam/l2_decoder.onnx
```

Set `OPENAI_API_KEY` in the container before running `plan_generator_node`.
