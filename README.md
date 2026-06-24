# TIAGo + EMPOWER ROS2 Pipeline

Questa repo contiene una sola scena Gazebo supportata:

```text
exchange/scene.world
exchange/maps/scene.yaml
src/empower/config/empower_config.yaml
src/empower/config/scenes/therapy_room_scene_graph.json
```

La scena mette TIAGo al centro della stanza, una persona sul lato `-Y`, un bambino sul lato `+Y` e il tavolo con il libro sul lato `+X`. Per catturare tavolo e libro con la camera frontale, TIAGo deve partire all'origine guardando verso `+X`, cioe' con yaw `0`.

La pipeline EMPOWER arriva fino a detection, grounding, pianificazione simbolica e navigazione mobile tramite Nav2. La parte low-level esegue fisicamente solo `NAVIGATE`; azioni come `GRAB`, `DROP`, `PUSH` e `PULL` vengono riconosciute e saltate, perche' manipolazione, MoveIt e gripper non sono implementati.

## Scene Graph Statico

Le pose manuali della stanza terapeutica sono centralizzate in:

```text
src/empower/config/scenes/therapy_room_scene_graph.json
```

Questo file JSON e' uno static semantic pose graph: descrive prior manuali in frame `map` per terapeuta, bambino, centro stanza, waypoint di approccio, superfici note e drop pose. Non esegue discovery autonoma delle pose e non sostituisce la perception EMPOWER.

Resta separato da `global_scene_graph.json`: quel file viene generato runtime dalla pipeline EMPOWER multi-view insieme a `global_detections.json` e serve per oggetti osservati, detection e planning visuale. Non va modificato o fuso con il grafo statico.

Per cambiare le pose principali, modifica i nodi del grafo statico:

- terapeuta: `therapist_interaction_pose.pose`
- bambino: `child_delivery_pose.pose`
- centro stanza/capture origin: `zone_center.pose`
- waypoint bambino: `child_approach_waypoint.pose`
- drop vicino al bambino: `child_delivery_table.drop_pose`

Il mission orchestrator usa il grafo statico per terapeuta, centro stanza e bambino. Il low-level executor lo usa per alias `child`/`kid` e `person`/`adult`/`therapist`, waypoint del bambino e drop pose vicino al bambino. La navigazione verso oggetti visuali continua invece a usare `global_detections.json`, `make_nav_goal_pose()` e lo standoff distance configurato.

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
PARAMS=/root/exchange/src/empower/config/empower_config.yaml
MAP=/root/exchange/exchange/maps/scene.yaml
WORLD=/root/exchange/exchange/scene.world
```

Se hai modificato codice Python o launch file, rebuilda il package ROS2:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
rm -rf build/empower install/empower
colcon build --symlink-install --packages-select empower
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
WORLD=/root/exchange/exchange/scene.world

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
MAP=/root/exchange/exchange/maps/scene.yaml

ros2 launch empower tiago_nav2_ground_truth.launch.py \
  map:=$MAP
```

Lascia aperto anche questo terminale.

## 6. Pulire Output Vecchi

Prima di rigenerare EMPOWER sulla scena aggiornata, rimuovi gli output precedenti:

```bash
cd /root/exchange
rm -rf output/empower_output/therapy_task
```

## 7. Terminale 3: Capture RGB-D

Assicurati che Gazebo sia avviato e che TIAGo guardi verso il tavolo sul lato `+X`.

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
PARAMS=/root/exchange/src/empower/config/empower_config.yaml

ros2 launch empower empower_capture.launch.py \
  params_file:=$PARAMS
```

Output principali:

```text
output/empower_output/therapy_task/scan.jpg
output/empower_output/therapy_task/depth.npy
output/empower_output/therapy_task/depth_pointcloud.pcd
output/empower_output/therapy_task/camera_info.json
```

## 8. Terminale 4: Model Cacher

Apri un terminale separato e lascialo aperto:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
PARAMS=/root/exchange/src/empower/config/empower_config.yaml

ros2 launch empower empower_models_cacher.launch.py \
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
PARAMS=/root/exchange/src/empower/config/empower_config.yaml

ros2 launch empower empower_plan.launch.py \
  params_file:=$PARAMS
```

Output principali:

```text
output/empower_output/therapy_task/detection.pkl
output/empower_output/therapy_task/detections.json
output/empower_output/therapy_task/colors_dict.pkl
output/empower_output/therapy_task/relations.txt
output/empower_output/therapy_task/relations.json
output/empower_output/therapy_task/scene_description.txt
output/empower_output/therapy_task/planning.txt
output/empower_output/therapy_task/planning.json
output/empower_output/therapy_task/planning.pkl
output/empower_output/therapy_task/symbolic_plan.txt
output/empower_output/therapy_task/empower_report.txt
```

## 10. Terminale 6: Visualizzazione RViz

Pubblica point cloud colorata e marker:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
PARAMS=/root/exchange/src/empower/config/empower_config.yaml

ros2 launch empower empower_visualization.launch.py \
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
- Output di capture e planning gia' generati per `therapy_task`.
- Robot nella stessa posa usata durante la capture, se stai riusando output salvati.

Esecuzione:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
PARAMS=/root/exchange/src/empower/config/empower_config.yaml

ros2 launch empower empower_execute.launch.py \
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

ros2 run empower export_reports \
  --ros-args \
  --params-file /root/exchange/src/empower/config/empower_config.yaml
```

## 13. Config Principale

La configurazione del task e':

```text
src/empower/config/empower_config.yaml
```

Valori principali:

```yaml
/**:
  ros__parameters:
    use_case: therapy_task
    task_description: "Take the book from the table"
    output_root: /root/exchange/output
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
- Se cambi codice Python o launch file, rebuilda `empower`.
- Se cambi la world o la mappa, riavvia Gazebo e Nav2.

## 15. Missione Completa HRI + EMPOWER

Questa sezione avvia il flusso orchestrato:

```text
TIAGo -> terapista -> task vocale HRI -> spawn -> EMPOWER -> bambino -> child_ready
```

Il launch principale e':

```bash
ros2 launch empower hri_empower_mission.launch.py
```

ma per usare microfono, Qwen e speaker serve avviare il container con audio.

### 15.1 Host: Avvia Ollama

Terminale host 1:

```bash
ollama serve
```

Lascialo aperto.

Terminale host 2:

```bash
ollama pull qwen2.5:1.5b
```

Quando il download e' finito, questo secondo terminale puo' essere chiuso.

### 15.2 Host: Avvia Il Container Con Audio

Terminale host 3:

```bash
cd /home/samuele/ElectiveAI

docker rm -f hrai_sim 2>/dev/null || true

xhost +local:docker

HOST_UID=$(id -u)
PULSE_SOCKET="/run/user/${HOST_UID}/pulse/native"
PULSE_COOKIE="${HOME}/.config/pulse/cookie"

docker run \
  --gpus all \
  --name hrai_sim \
  --rm \
  -it \
  --net host \
  --privileged \
  --device /dev/snd \
  --env DISPLAY \
  --env QT_X11_NO_MITSHM=1 \
  --env __NV_PRIME_RENDER_OFFLOAD=1 \
  --env __GLX_VENDOR_LIBRARY_NAME=nvidia \
  -e PULSE_SERVER="unix:${PULSE_SOCKET}" \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  -v "${PULSE_SOCKET}:${PULSE_SOCKET}" \
  -v "${PULSE_COOKIE}:/root/.config/pulse/cookie:ro" \
  -v /home/samuele/ElectiveAI:/root/exchange \
  -w /root/exchange \
  hrai-2025:v1 \
  /root/exchange/docker/entrypoint_tiago.sh
```

Se Docker non supporta `--gpus all`, rilancia lo stesso comando togliendo solo quella riga.

### 15.3 Container: Installa Audio, STT E TTS

Dentro il container:

```bash
cd /root/exchange

apt update

apt install -y \
  python3-pip \
  libportaudio2 \
  portaudio19-dev \
  libsndfile1 \
  libasound2-plugins \
  pulseaudio-utils \
  alsa-utils \
  espeak-ng \
  speech-dispatcher \
  curl

python3 -m pip install \
  faster-whisper \
  sounddevice \
  soundfile \
  pyttsx3
```

Configura microfono e speaker. Nella configurazione testata il microfono e' `hw:0,7`.

```bash
mkdir -p /root/.config/pulse

cat > /root/.asoundrc <<'EOF'
pcm.!default {
    type asym
    playback.pcm "pulse_output"
    capture.pcm "microphone"
}

pcm.pulse_output {
    type pulse
}

pcm.microphone {
    type plug
    slave.pcm "hw:0,7"
}

ctl.!default {
    type pulse
}
EOF
```

Scarica in cache il modello Whisper inglese:

```bash
python3 -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')"
```

### 15.4 Container: Build ROS2

```bash
cd /root/exchange

source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash

colcon build --symlink-install --packages-select empower llm_interaction

source /root/exchange/install/setup.bash
```

Se modifichi codice Python o launch file, riesegui almeno:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
colcon build --symlink-install --packages-select empower llm_interaction
source /root/exchange/install/setup.bash
```

### 15.5 Terminale Container 1: Gazebo

Se non sei gia' nel container:

```bash
docker exec -it hrai_sim bash
```

Poi:

```bash
cd /root/exchange

source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

export GAZEBO_MODEL_PATH=/root/exchange/exchange/models:${GAZEBO_MODEL_PATH:-}
WORLD=/root/exchange/exchange/scene.world

ros2 launch tiago_gazebo tiago_gazebo.launch.py \
  is_public_sim:=True \
  gazebo_version:=classic \
  world_name:=$WORLD
```

Lascia aperto questo terminale.

### 15.6 Terminale Container 2: Nav2

```bash
docker exec -it hrai_sim bash

cd /root/exchange

source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

MAP=/root/exchange/exchange/maps/scene.yaml

ros2 launch empower tiago_nav2_ground_truth.launch.py \
  map:=$MAP
```

Lascia aperto anche questo terminale.

### 15.7 Terminale Container 3: Missione Orchestrata

```bash
docker exec -it hrai_sim bash

cd /root/exchange

source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

rm -rf /root/exchange/output/empower_output/therapy_task

ros2 launch empower hri_empower_mission.launch.py \
  hri_params_file:=/root/exchange/src/llm_interaction/config/llm_config.yaml \
  hri_input_mode:=voice
```

Quando TIAGo arriva dalla terapista, il nodo HRI passa a `waiting_for_push_to_talk`.
Premi INVIO nel terminale della missione e parla in inglese durante la registrazione, ad esempio:

```text
Take the book from the table
```

Flusso atteso nei log:

```text
Mission state: GO_TO_THERAPIST
Mission state: WAIT_TASK
Received physical task
Mission state: ACK_TASK
Mission state: WAIT_ACTIVITY
Received child activity
Normalized activity type
Mission state: ACK_ACTIVITY
Mission state: GO_TO_SPAWN
Mission state: RUN_EMPOWER
EMPOWER scene capture completed
Running EMPOWER planning
Detection, grounding and planning completed
EMPOWER planning completed
Running EMPOWER low-level execution
Mission state: GO_TO_CHILD
Mission state: CHILD_READY
Mission state: CHILD_ACTIVITY
```

### 15.8 Monitor Della Missione

In altri terminali container:

```bash
docker exec -it hrai_sim bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

ros2 topic echo /mission/state
```

```bash
docker exec -it hrai_sim bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

ros2 topic echo /mission/feedback
```

```bash
docker exec -it hrai_sim bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

ros2 topic echo /mission/task_description
```

```bash
docker exec -it hrai_sim bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

ros2 topic echo /mission/child_ready
```

### 15.9 Debug Senza Microfono

Per validare la missione senza input vocale:

```bash
docker exec -it hrai_sim bash

cd /root/exchange

source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash

rm -rf /root/exchange/output/empower_output/therapy_task

ros2 launch empower hri_empower_mission.launch.py \
  hri_params_file:=/root/exchange/src/llm_interaction/config/llm_config.yaml \
  hri_input_mode:=text
```

Oppure pubblica direttamente i due input della terapeuta. Il primo va pubblicato
quando lo stato e' `WAIT_TASK`; il secondo quando lo stato e' `WAIT_ACTIVITY`.

Caso computer + emotion recognition:

```bash
ros2 topic pub --once /hri/user_utterance std_msgs/msg/String "{data: 'Take the computer'}"
```

```bash
ros2 topic pub --once /hri/user_utterance std_msgs/msg/String "{data: 'Do emotion recognition with the computer'}"
```

Caso teddy bear + mood questions:

```bash
ros2 topic pub --once /hri/user_utterance std_msgs/msg/String "{data: 'Take the teddy bear'}"
```

```bash
ros2 topic pub --once /hri/user_utterance std_msgs/msg/String "{data: 'Ask the child how they feel'}"
```

### 15.10 Note Sulla Missione

- `models_cacher_node` carica i modelli e poi resta in ascolto su `127.0.0.1:10000`.
- Il planning non parte quando il cacher e' pronto: parte nello stato `RUN_EMPOWER`, dopo che TIAGo e' tornato allo spawn.
- Il task passato a EMPOWER e' il primo testo utile ricevuto da `/hri/user_utterance`.
- L'attivita' sociale e' il secondo testo utile ricevuto da `/hri/user_utterance` e determina il comando `start_child_activity`.
- Dopo l'avvio della child activity, l'orchestratore ignora le nuove utterance su `/hri/user_utterance`.
- Le azioni EMPOWER diverse da `NAVIGATE`, come `GRAB` e `DROP`, restano simboliche/loggate.
- Se Nav2 fallisce nel ritorno allo spawn con `Failed to make progress`, ferma solo la missione, resetta/sposta TIAGo o correggi `zone_center.pose` in `src/empower/config/scenes/therapy_room_scene_graph.json`, poi rilancia la missione.
- Se cambi le pose in YAML non serve rebuildare; se cambi Python o launch file serve rebuildare.
