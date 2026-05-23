# TIAGo + EMPOWER ROS2 Pipeline

Questa guida descrive i comandi aggiornati per eseguire la pipeline EMPOWER portata in ROS2 Humble su TIAGo/Gazebo.

La pipeline arriva fino a:

- acquisizione RGB-D;
- creazione point cloud;
- detection/segmentation/grounding con logica EMPOWER;
- generazione piano simbolico;
- esportazione file leggibili;
- visualizzazione RViz.

Non esegue navigazione reale, MoveIt, Nav2, `cmd_vel`, manipolazione o primitive fisiche.

## 0. Chiave OpenAI

Sul tuo host, nel file:

```text
/home/samuele/ElectiveAI-2/openai_api_key.txt
```

deve esserci solo la chiave, una riga:

```text
sk-...
```

Non serve più fare:

```bash
export OPENAI_API_KEY=...
```

Il file è ignorato da git.

## 1. Avviare Il Container

Sul tuo host:

```bash
cd /home/samuele/ElectiveAI-2
bash start_tiago.sh
```

Se devi aprire altri terminali dentro lo stesso container:

```bash
docker exec -it hrai_sim bash
```

## 2. Setup Ambiente Dentro Ogni Terminale Container

Dentro il container:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
```

Se hai modificato codice Python e vuoi rebuildare solo il package ROS2, non Docker:

```bash
cd /root/exchange
rm -rf build/empower_ros2 install/empower_ros2
colcon build --symlink-install --packages-select empower_ros2
source /root/exchange/install/setup.bash
```

Se cambi solo file YAML, per esempio `empower_tiago.yaml`, non serve ricompilare.

## 3. Avviare TIAGo In Gazebo Con myWorld

In un terminale container:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

export GAZEBO_MODEL_PATH=/root/exchange/exchange/models:${GAZEBO_MODEL_PATH:-}

ros2 launch tiago_gazebo tiago_gazebo.launch.py \
  is_public_sim:=True \
  gazebo_version:=classic \
  world_name:=/root/exchange/exchange/myWorld.world
```

Lascia questo terminale aperto.

## 4. Configurare Il Task

Modifica da VS Code:

```text
src/empower_ros2/config/empower_tiago.yaml
```

Esempio:

```yaml
/**:
  ros__parameters:
    use_case: tiago_task
    task_description: "talk to the person"
    output_root: /root/exchange/empower_data
    rgb_topic: /head_front_camera/rgb/image_raw
    depth_topic: /head_front_camera/depth/image_raw
    camera_info_topic: /head_front_camera/depth/camera_info
    pointcloud_topic: /empower/colored_pointcloud
    fixed_frame: base_footprint
    camera_frame: base_footprint
    depth_unit_scale_to_mm: -1.0
    server_host: 127.0.0.1
    server_port: 10000
    openai_api_key_file: /root/exchange/openai_api_key.txt
```

Non serve ricompilare se cambi solo questo file.

Se cambi `task_description`, rilancia almeno:

- `empower_models_cacher`;
- `empower_plan`.

## 5. Catturare La Scena RGB-D

In un secondo terminale container:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

PARAMS=/root/exchange/src/empower_ros2/config/empower_tiago.yaml
ros2 launch empower_ros2 empower_capture.launch.py params_file:=$PARAMS
```

Output principali:

```text
empower_data/images/tiago_task/scan.jpg
empower_data/output/tiago_task/depth.npy
empower_data/output/tiago_task/depth_pointcloud.pcd
empower_data/output/tiago_task/camera_info.json
```

## 6. Avviare Il Model Cacher

In un terminale container separato, da lasciare aperto:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

PARAMS=/root/exchange/src/empower_ros2/config/empower_tiago.yaml
ros2 launch empower_ros2 empower_models_cacher.launch.py params_file:=$PARAMS
```

Aspetta di vedere:

```text
Model cacher ready
Listening for empower detection triggers on 127.0.0.1:10000
```

Questo nodo carica:

- YOLO-World;
- EfficientViT-SAM;
- spaCy;
- word2vec;
- logica EMPOWER originale.

## 7. Generare Il Piano

In un altro terminale container:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

PARAMS=/root/exchange/src/empower_ros2/config/empower_tiago.yaml
ros2 launch empower_ros2 empower_plan.launch.py params_file:=$PARAMS
```

Output utili:

```text
empower_data/output/tiago_task/planning.txt
empower_data/output/tiago_task/planning.json
empower_data/output/tiago_task/planning.pkl
empower_data/output/tiago_task/relations.txt
empower_data/output/tiago_task/relations.json
empower_data/output/tiago_task/scene_description.txt
empower_data/output/tiago_task/symbolic_plan.txt
empower_data/output/tiago_task/detections.json
empower_data/output/tiago_task/empower_report.txt
```

Per leggere il report completo:

```bash
cat /root/exchange/empower_data/output/tiago_task/empower_report.txt
```

Esempio di contenuto:

```text
Relations:
1) (person, left to, chair)
2) (chair, left to, table)

Scene description:
...

Symbolic plan:
NAVIGATE to the chair
NAVIGATE to the left of the chair

Detections:
- 0: table bbox=...
```

## 8. Visualizzazione RViz

Prima pulisci eventuali vecchie visualizzazioni duplicate:

```bash
pkill -f color_pcl_node
pkill -f marker_centroids_node
```

Poi lancia la visualizzazione:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

PARAMS=/root/exchange/src/empower_ros2/config/empower_tiago.yaml
ros2 launch empower_ros2 empower_visualization.launch.py params_file:=$PARAMS
```

In un altro terminale container:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

rviz2
```

In RViz:

- Fixed Frame: `base_footprint`
- Add `PointCloud2`: `/empower/colored_pointcloud`
- Add `MarkerArray`: `/pcl_centroids`
- Add `MarkerArray`: `/pcl_names`
- opzionale `MarkerArray`: `/pcl_maximum`

Per controllare che non ci siano publisher duplicati:

```bash
ros2 node list | grep empower
ros2 topic info /pcl_centroids -v | grep "Publisher count"
ros2 topic info /empower/colored_pointcloud -v | grep "Publisher count"
```

I publisher devono essere:

```text
Publisher count: 1
```

Se sono `2`, hai lanciato due volte la visualizzazione. Ferma i nodi con:

```bash
pkill -f color_pcl_node
pkill -f marker_centroids_node
```

e rilancia una sola visualizzazione.

## 9. Rigenerare Solo I Report Leggibili

Se hai già `planning.pkl` e `detection.pkl`, puoi rigenerare i file `.txt/.json` leggibili senza rifare planning:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

ros2 run empower_ros2 export_reports \
  --ros-args \
  --params-file /root/exchange/src/empower_ros2/config/empower_tiago.yaml
```

## 10. File Di Output

Output immagine:

```text
empower_data/images/tiago_task/scan.jpg
```

Output point cloud/depth:

```text
empower_data/output/tiago_task/depth.npy
empower_data/output/tiago_task/depth_pointcloud.pcd
empower_data/output/tiago_task/colored_pcl.pcd
empower_data/output/tiago_task/colored_image.png
```

Output detection/grounding:

```text
empower_data/output/tiago_task/detection.pkl
empower_data/output/tiago_task/detections.json
empower_data/output/tiago_task/mask.jpg
empower_data/output/tiago_task/scan_with_bb.jpg
empower_data/output/tiago_task/yolo.jpg
empower_data/output/tiago_task/colors_dict.pkl
```

Output piano e scena simbolica:

```text
empower_data/output/tiago_task/planning.txt
empower_data/output/tiago_task/planning.json
empower_data/output/tiago_task/planning.pkl
empower_data/output/tiago_task/relations.txt
empower_data/output/tiago_task/relations.json
empower_data/output/tiago_task/scene_description.txt
empower_data/output/tiago_task/symbolic_plan.txt
empower_data/output/tiago_task/empower_report.txt
```

## 11. Rebuild Docker Futuro

Per ora, se lavori con la repo montata, non serve rifare Docker build per modifiche Python/YAML: basta:

```bash
colcon build --symlink-install --packages-select empower_ros2
```

In futuro, per creare una nuova immagine con tutti i fix inclusi:

```bash
cd /home/samuele/ElectiveAI-2
docker build -t hrai-2025:v1 dockerfiles
```

Se vuoi evitare il precache di word2vec durante build:

```bash
docker build -t hrai-2025:v1 dockerfiles \
  --build-arg EMPOWER_PRECACHE_WORD2VEC=false
```

Se vuoi evitare anche il precache HuggingFace:

```bash
docker build -t hrai-2025:v1 dockerfiles \
  --build-arg EMPOWER_PRECACHE_WORD2VEC=false \
  --build-arg EMPOWER_PRECACHE_HF=false
```

## 12. Note Importanti

- `start_tiago.sh` si esegue solo sull'host, non dentro il container.
- Ogni nuovo terminale `docker exec` deve fare il `source` dell'ambiente.
- Il sistema non esegue fisicamente azioni come `NAVIGATE`, `GRAB`, `DROP`.
- Il piano è solo simbolico/testuale.
- La visualizzazione RViz mostra point cloud colorata, centroidi e nomi degli oggetti groundati.
- Se cambi solo `task_description`, non serve rebuildare; rilancia model cacher e planner.
- Se cambi codice Python, rebuilda solo `empower_ros2` con `colcon`.
