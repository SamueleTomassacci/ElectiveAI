# TIAGo + EMPOWER ROS2 Pipeline

Questa guida descrive i comandi aggiornati per eseguire la pipeline EMPOWER portata in ROS2 Humble su TIAGo/Gazebo.

La pipeline arriva fino a:

- acquisizione RGB-D;
- creazione point cloud;
- detection/segmentation/grounding con logica EMPOWER;
- generazione piano simbolico;
- esportazione file leggibili;
- visualizzazione RViz;
- esecuzione low-level degli step `NAVIGATE` tramite Nav2.

La parte low-level esegue solo navigazione mobile. Le azioni `GRAB`, `DROP`, `PUSH` e `PULL` vengono riconosciute, loggate e saltate in modo sicuro. Non viene eseguita manipolazione, MoveIt, MoveIt2, grasping o controllo del gripper.

## 0. Chiave OpenAI

Sul tuo host, nel file:

```text
/home/samuele/ElectiveAI/openai_api_key.txt
```

deve esserci solo la chiave, una riga:

```text
sk-...
```

## 1. Avviare Il Container

Sul tuo host:

```bash
cd /home/samuele/ElectiveAI
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

## 4. Avviare Nav2 Con Ground Truth

In un secondo terminale container:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

ros2 launch empower_ros2 tiago_nav2_ground_truth.launch.py
```

Questo launch usa:

- mappa statica `exchange/maps/myWorld_static.yaml`;
- niente SLAM;
- niente laser;
- localizzazione ground truth da Gazebo;
- TF `map -> odom` pubblicata da `ground_truth_localizer_node`;
- backend Nav2 `NavigateToPose`.

Lascia anche questo terminale aperto. 

## 5. Configurare Il Task

Modifica da VS Code:

```text
src/empower_ros2/config/empower_tiago.yaml
```

Esempio:

```yaml
/**:
  ros__parameters:
    use_case: tiago_task
    task_description: "Move the cup on the chair"
    output_root: /root/exchange/empower_data
    rgb_topic: /head_front_camera/rgb/image_raw
    depth_topic: /head_front_camera/depth/image_raw
    camera_info_topic: /head_front_camera/depth/camera_info
    pointcloud_topic: /empower/colored_pointcloud
    fixed_frame: map
    camera_frame: head_front_camera_color_optical_frame
    depth_unit_scale_to_mm: -1.0
    server_host: 127.0.0.1
    server_port: 10000
    openai_api_key_file: /root/exchange/openai_api_key.txt
    centroids_topic: /pcl_centroids
    names_topic: /pcl_names
    map_frame: map
    base_frame: base_footprint
    nav_action_name: navigate_to_pose
    marker_timeout_sec: 30.0
    tf_timeout_sec: 2.0
    action_server_timeout_sec: 20.0
    nav_result_timeout_sec: 300.0
    standoff_distance: 0.8
    continue_on_nav_failure: false
```

Non serve ricompilare se cambi solo questo file.

`standoff_distance` controlla quanto il goal Nav2 resta distante dal centroide dell'oggetto. Se il goal cade dentro l'ostacolo o nella sua inflation, Nav2 fallisce con messaggi tipo `failed to create plan`; in quel caso aumenta leggermente il valore.


## 6. Catturare La Scena RGB-D

In un terminale container:

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

## 7. Avviare Il Model Cacher

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

## 8. Generare Il Piano

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

## 9. Visualizzazione RViz

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

- Fixed Frame: `map`
- Add `PointCloud2`: `/empower/colored_pointcloud`
- Add `MarkerArray`: `/pcl_centroids`
- Add `MarkerArray`: `/pcl_names`
- opzionale `MarkerArray`: `/pcl_maximum`


## 10. Eseguire Il Piano Low-Level

Prerequisiti:

- Gazebo TIAGo avviato;
- Nav2 ground truth avviato;
- `planning.json` o `planning.txt` gia' generato;
- `detection.pkl`, `colors_dict.pkl` e file RGB-D gia' presenti nella cartella di output;
- robot nella stessa posa usata per catturare la scena, se stai riusando output salvati senza rifare capture.

Poi lancia l'esecuzione:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

PARAMS=/root/exchange/src/empower_ros2/config/empower_tiago.yaml
ros2 launch empower_ros2 empower_execute.launch.py params_file:=$PARAMS
```

Comportamento atteso:

```text
NAVIGATE table        -> eseguito con Nav2 NavigateToPose
GRAB cup              -> skipped, not implemented
NAVIGATE chair ...    -> eseguito con Nav2 NavigateToPose
DROP cup ...          -> skipped, not implemented
```

Nel terminale dell'executor dovresti vedere log simili:

```text
Loaded parsed plan from .../planning.json
Loaded EMPOWER object centroids: ...
Executing NAVIGATE to 'table' from EMPOWER centroid (...)
Sending NAVIGATE goal to Nav2: frame=map x=... y=...
NAVIGATE succeeded
Skipping GRAB cup: action not implemented in ROS2 port
```

Nel terminale Nav2 dovresti vedere:

```text
Begin navigating from current location (...) to (...)
Reached the goal!
Goal succeeded
```

Se vuoi riusare il piano gia' salvato senza rifare perception/planning, tieni aperti solo Gazebo e Nav2, verifica che i file in `empower_data/output/tiago_task` siano presenti, e lancia direttamente `empower_execute.launch.py`.


## 11. Scena Alternativa Per Test NAVIGATE

La repo contiene anche una scena pensata per testare meglio la primitiva `NAVIGATE`, con piu' target distribuiti nello spazio:

```text
exchange/nav_challenge.world
exchange/maps/nav_challenge_static.yaml
src/empower_ros2/config/empower_nav_challenge.yaml
```

Oggetti principali:

- cup su un tavolo;
- bowl su un tavolo rotondo;
- chair;
- person;
- sofa;
- cabinet;
- plant/tree.

Avvia Gazebo con la nuova world:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
export GAZEBO_MODEL_PATH=/root/exchange/exchange/models:${GAZEBO_MODEL_PATH:-}

ros2 launch tiago_gazebo tiago_gazebo.launch.py \
  is_public_sim:=True \
  gazebo_version:=classic \
  world_name:=/root/exchange/exchange/nav_challenge.world
```

Avvia Nav2 con la mappa statica della scena:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

ros2 launch empower_ros2 tiago_nav2_ground_truth.launch.py \
  map:=/root/exchange/exchange/maps/nav_challenge_static.yaml
```

Usa il params file dedicato:

```bash
PARAMS=/root/exchange/src/empower_ros2/config/empower_nav_challenge.yaml
```

Poi esegui normalmente capture, model cacher, planning ed execution:

```bash
ros2 launch empower_ros2 empower_capture.launch.py params_file:=$PARAMS
ros2 launch empower_ros2 empower_models_cacher.launch.py params_file:=$PARAMS
ros2 launch empower_ros2 empower_plan.launch.py params_file:=$PARAMS
ros2 launch empower_ros2 empower_execute.launch.py params_file:=$PARAMS
```

Il task di default e':

```text
visit the cup, then the person, then the plant, then the cabinet
```

Questa scena salva gli output in:

```text
empower_data/output/nav_challenge_task
```

Per verificare che i target siano coerenti con la world:

```bash
ros2 topic echo /pcl_names --once
ros2 topic echo /pcl_centroids --once
```

I marker devono essere in `map` e in posizioni compatibili con la disposizione della scena.

## 12. Rigenerare Solo I Report Leggibili

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

## 13. File Di Output

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

## 14. Rebuild Docker Futuro

Per ora, se lavori con la repo montata, non serve rifare Docker build per modifiche Python/YAML: basta:

```bash
colcon build --symlink-install --packages-select empower_ros2
```

In futuro, per creare una nuova immagine con tutti i fix inclusi:

```bash
cd /home/samuele/ElectiveAI
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

## 15. Note Importanti

- `start_tiago.sh` si esegue solo sull'host, non dentro il container.
- Ogni nuovo terminale `docker exec` deve fare il `source` dell'ambiente.
- Il sistema esegue fisicamente solo `NAVIGATE` tramite Nav2.
- Il sistema non esegue fisicamente `GRAB`, `DROP`, `PUSH`, `PULL` o manipolazione.
- La visualizzazione RViz mostra point cloud colorata, centroidi e nomi degli oggetti groundati.
- Se cambi solo `task_description`, non serve rebuildare; rilancia model cacher e planner.
- Se cambi solo `standoff_distance` o altri parametri YAML, non serve rebuildare; rilancia l'execution.
- Se cambi codice Python, rebuilda solo `empower_ros2` con `colcon`.
