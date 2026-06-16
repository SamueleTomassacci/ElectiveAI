# TIAGo + EMPOWER ROS2 Pipeline

Questa repo contiene una sola scena Gazebo supportata:

```text
exchange/person_table_child.world
exchange/maps/person_table_child_static.yaml
src/empower_ros2/config/empower_person_table_child.yaml
```

La scena mette TIAGo al centro della stanza, una persona sul lato `-Y`, un bambino sul lato `+Y` e il tavolo con il libro sul lato `+X`. Per catturare tavolo e libro con la camera frontale, TIAGo deve partire all'origine guardando verso `+X`, cioe' con yaw `0`.

La pipeline EMPOWER arriva fino a detection, grounding, pianificazione simbolica e navigazione mobile tramite Nav2. La parte low-level esegue fisicamente solo `NAVIGATE`; azioni come `GRAB`, `DROP`, `PUSH` e `PULL` vengono riconosciute e saltate, perche' manipolazione, MoveIt e gripper non sono implementati.

## 1. Chiave OpenAI

Sul tuo host crea o aggiorna:

```bash
cd /home/samuele/ElectiveAI
printf '%s\n' 'sk-INSERISCI_LA_TUA_CHIAVE' > openai_api_key.txt
```

Il file deve contenere solo la chiave.

## 2. Avviare Il Container

Sul tuo host:

```bash
cd /home/samuele/ElectiveAI
bash start_tiago.sh
```

Per aprire altri terminali nello stesso container:

```bash
docker exec -it hrai_sim bash
```

## 3. Setup Ambiente

Esegui questi comandi dentro ogni terminale del container:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
export GAZEBO_MODEL_PATH=/root/exchange/exchange/models:${GAZEBO_MODEL_PATH:-}
PARAMS=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
MAP=/root/exchange/exchange/maps/person_table_child_static.yaml
WORLD=/root/exchange/exchange/person_table_child.world
```

Se hai modificato codice Python o launch file, rebuilda il package ROS2:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
rm -rf build/empower_ros2 install/empower_ros2
colcon build --symlink-install --packages-select empower_ros2
source /root/exchange/install/setup.bash
```

Se cambi solo YAML, world o mappa statica non serve rebuildare.

## 4. Terminale 1: Gazebo

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
export GAZEBO_MODEL_PATH=/root/exchange/exchange/models:${GAZEBO_MODEL_PATH:-}
WORLD=/root/exchange/exchange/person_table_child.world

ros2 launch tiago_gazebo tiago_gazebo.launch.py \
  is_public_sim:=True \
  gazebo_version:=classic \
  world_name:=$WORLD
```

Lascia aperto questo terminale.

## 5. Terminale 2: Nav2 Con Ground Truth

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
MAP=/root/exchange/exchange/maps/person_table_child_static.yaml

ros2 launch empower_ros2 tiago_nav2_ground_truth.launch.py \
  map:=$MAP
```

Lascia aperto anche questo terminale.

## 6. Pulire Output Vecchi

Prima di rigenerare EMPOWER sulla scena aggiornata, rimuovi gli output precedenti:

```bash
cd /root/exchange
rm -rf empower_data/images/person_table_child_task
rm -rf empower_data/output/person_table_child_task
```

## 7. Terminale 3: Capture RGB-D

Assicurati che Gazebo sia avviato e che TIAGo guardi verso il tavolo sul lato `+X`.

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
PARAMS=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml

ros2 launch empower_ros2 empower_capture.launch.py \
  params_file:=$PARAMS
```

Output principali:

```text
empower_data/images/person_table_child_task/scan.jpg
empower_data/output/person_table_child_task/depth.npy
empower_data/output/person_table_child_task/depth_pointcloud.pcd
empower_data/output/person_table_child_task/camera_info.json
```

## 8. Terminale 4: Model Cacher

Apri un terminale separato e lascialo aperto:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
PARAMS=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml

ros2 launch empower_ros2 empower_models_cacher.launch.py \
  params_file:=$PARAMS
```

Aspetta log simili a:

```text
Model cacher ready
Listening for empower detection triggers on 127.0.0.1:10000
```

## 9. Terminale 5: Planning EMPOWER

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
PARAMS=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml

ros2 launch empower_ros2 empower_plan.launch.py \
  params_file:=$PARAMS
```

Output principali:

```text
empower_data/output/person_table_child_task/detection.pkl
empower_data/output/person_table_child_task/detections.json
empower_data/output/person_table_child_task/colors_dict.pkl
empower_data/output/person_table_child_task/relations.txt
empower_data/output/person_table_child_task/relations.json
empower_data/output/person_table_child_task/scene_description.txt
empower_data/output/person_table_child_task/planning.txt
empower_data/output/person_table_child_task/planning.json
empower_data/output/person_table_child_task/planning.pkl
empower_data/output/person_table_child_task/symbolic_plan.txt
empower_data/output/person_table_child_task/empower_report.txt
```

## 10. Terminale 6: Visualizzazione RViz

Pubblica point cloud colorata e marker:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
PARAMS=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml

ros2 launch empower_ros2 empower_visualization.launch.py \
  params_file:=$PARAMS
```

In un altro terminale:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

rviz2
```

In RViz usa:

```text
Fixed Frame: map
PointCloud2: /empower/colored_pointcloud
MarkerArray: /pcl_centroids
MarkerArray: /pcl_names
MarkerArray: /pcl_maximum
```

Per controllare i marker da terminale:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

ros2 topic echo /pcl_names --once
ros2 topic echo /pcl_centroids --once
```

## 11. Terminale 7: Execution Low-Level

Prerequisiti:

- Gazebo aperto.
- Nav2 ground truth aperto.
- `empower_models_cacher` aperto se vuoi anche visualizzare/ricaricare i marker.
- Output di capture e planning gia' generati per `person_table_child_task`.
- Robot nella stessa posa usata durante la capture, se stai riusando output salvati.

Esecuzione:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
PARAMS=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml

ros2 launch empower_ros2 empower_execute.launch.py \
  params_file:=$PARAMS
```

Comportamento atteso per il task di default:

```text
NAVIGATE to the table  -> eseguito con Nav2 NavigateToPose
GRAB book              -> skipped, not implemented
```

## 12. Rigenerare Solo I Report Leggibili

Se hai gia' `planning.pkl` e `detection.pkl` e vuoi rigenerare `.txt`/`.json`:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

ros2 run empower_ros2 export_reports \
  --ros-args \
  --params-file /root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
```

## 13. Config Principale

La configurazione del task e':

```text
src/empower_ros2/config/empower_person_table_child.yaml
```

Valori principali:

```yaml
/**:
  ros__parameters:
    use_case: person_table_child_task
    task_description: "Take the book from the table"
    output_root: /root/exchange/empower_data
    rgb_topic: /head_front_camera/rgb/image_raw
    depth_topic: /head_front_camera/depth/image_raw
    camera_info_topic: /head_front_camera/depth/camera_info
    pointcloud_topic: /empower/colored_pointcloud
    fixed_frame: map
    camera_frame: head_front_camera_color_optical_frame
    map_frame: map
    base_frame: base_footprint
    nav_action_name: navigate_to_pose
    standoff_distance: 1.0
    continue_on_nav_failure: false
```

`standoff_distance` controlla quanto il goal Nav2 resta distante dal centroide dell'oggetto. Se il goal cade dentro l'ostacolo o nella sua inflation, aumenta leggermente il valore.

## 14. Note

- `start_tiago.sh` si esegue sull'host, non dentro il container.
- Ogni nuovo terminale `docker exec` deve fare i `source` dell'ambiente.
- Se cambi `task_description`, rilancia almeno model cacher e planning.
- Se cambi `standoff_distance`, rilancia execution.
- Se cambi codice Python o launch file, rebuilda `empower_ros2`.
- Se cambi la world o la mappa, riavvia Gazebo e Nav2.
