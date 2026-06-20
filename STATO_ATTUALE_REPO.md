# Stato attuale della repo ElectiveAI

Questo documento e' pensato per essere passato a ChatGPT come contesto tecnico
della repo. Descrive lo stato attuale del codice, l'architettura dei package
ROS 2, il flusso di esecuzione della simulazione TIAGo in Gazebo e, in modo
particolare, la pipeline EMPOWER, che e' la componente su cui sono previsti i
prossimi cambiamenti.

## Sintesi generale

La repo contiene il codice necessario per eseguire una simulazione del robot
TIAGo in Gazebo Classic, con ROS 2 Humble, Nav2 e una pipeline di task planning
basata su EMPOWER. La scena supportata e' una scena custom:

- world: `exchange/person_table_child.world`
- mappa statica Nav2: `exchange/maps/person_table_child_static.yaml`
- configurazione EMPOWER: `src/empower_ros2/config/empower_person_table_child.yaml`

La repo e' organizzata intorno a tre componenti principali:

1. `empower_ros2`: adapter ROS 2 della pipeline EMPOWER, piu orchestrazione
   missione, Nav2, capture RGB-D, planning, grounding, visualizzazione ed
   esecuzione low-level.
2. `empower_hri`: componente di interazione vocale/testuale. Usa STT,
   LLM locale via Ollama e TTS per raccogliere istruzioni dal terapista e
   dialogare con il bambino.
3. `gaze_estimation`: componente autonoma per stimare se una persona guarda un
   oggetto target usando webcam, Gazelle/Gaze-LLE e YOLO-World.

Il sistema attuale e' un MVP integrato: TIAGo puo' navigare in Gazebo usando
Nav2, generare un piano simbolico da immagine RGB-D e task testuale, eseguire
azioni `NAVIGATE`, e simulare alcune azioni di manipolazione (`GRAB`/`DROP`) su
oggetti noti tramite comandi Gazebo. Non esiste ancora una manipolazione reale
con braccio, MoveIt o gripper.

## Ambiente di runtime

La simulazione viene eseguita dentro un container Docker basato su
`hrai-2025:v1`. Lo script principale e':

```bash
bash start_tiago.sh
```

Lo script:

- monta la repo host in `/root/exchange`;
- usa `--net host` e `--privileged`;
- passa X11 per Gazebo/RViz;
- prova a usare GPU NVIDIA se `nvidia-container-toolkit` e' presente;
- monta il socket PulseAudio se disponibile;
- avvia `/root/exchange/docker/entrypoint_tiago.sh`.

L'entrypoint fa il source di:

```bash
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
```

Nel Dockerfile (`dockerfiles/Dockerfile`) sono installati ROS 2 Humble, Nav2,
Gazebo ROS packages, dipendenze TIAGo, dipendenze EMPOWER, modelli EMPOWER
scaricati in `/opt/empower_models/config`, e dipendenze HRI audio/STT/TTS.

## Scena Gazebo e navigazione

La scena principale e' `exchange/person_table_child.world`, con world name:

```text
person_table_child_room
```

Oggetti e posizioni rilevanti:

- TIAGo deve partire all'origine guardando verso `+X` per vedere tavolo/shelf
  con la camera frontale.
- Adulto/terapista: lato `-Y`, modello `adult_person_in_front_of_tiago`.
- Bambino: lato `+Y`, modello `child_straight_ahead_after_turn`.
- Tavolo di consegna bambino: `child_delivery_table`, posizione circa
  `(0.0, 1.65)`.
- Area laterale con shelf/tavolo a destra: oggetti manipolabili noti:
  - `clear_red_book_on_right_table`
  - `laptop_computer_on_right_table`
  - `teddy_bear_on_right_table`

La mappa Nav2 e' `exchange/maps/person_table_child_static.yaml`:

```yaml
resolution: 0.05
origin: [-4.7, -3.9, 0.0]
mode: trinary
```

Il bringup Nav2 custom e':

```bash
ros2 launch empower_ros2 tiago_nav2_ground_truth.launch.py \
  map:=/root/exchange/exchange/maps/person_table_child_static.yaml
```

Questo launch avvia:

- `nav2_map_server`
- `nav2_lifecycle_manager`
- `ground_truth_localizer_node`
- `nav2_bringup/launch/navigation_launch.py`

La localizzazione non usa AMCL: viene pubblicata da
`ground_truth_localizer_node` a partire da `/ground_truth_odom`, per mantenere
semplice e stabile la simulazione.

## Package `empower_ros2`: ruolo e struttura

`empower_ros2` e' il package centrale. E' un package `ament_python` che espone
questi console script:

- `scene_capture_node`
- `models_cacher_node`
- `plan_generator_node`
- `export_reports`
- `color_pcl_node`
- `marker_centroids_node`
- `ground_truth_localizer_node`
- `low_level_execution_node`
- `mission_orchestrator_node`

Il package contiene due livelli di codice:

- moduli EMPOWER stile upstream in `src/empower_ros2/python/*.py`
  (`agents.py`, `detection.py`, `models.py`, `loader.py`, `paths.py`);
- adapter ROS 2 in `src/empower_ros2/python/empower_ros2/*.py`.

I file `agents.py`, `detection.py`, `models.py`, `loader.py`, `paths.py` sono
presenti anche direttamente in `src/empower_ros2/` e risultano identici alle
copie sotto `src/empower_ros2/python/`. La configurazione `setup.py` installa
questi file come `py_modules`, mentre il package ROS 2 vero e proprio sta in
`python/empower_ros2`.

## Configurazione principale EMPOWER

Il file corrente e':

```text
src/empower_ros2/config/empower_person_table_child.yaml
```

Parametri chiave:

```yaml
use_case: person_table_child_task
task_description: "Take the peluche, go to the child and drop the peluche"
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

Nota: alcuni README storici parlano ancora di task "Take the book from the
table", ma la YAML corrente usa il task sul `peluche` e include anche la
consegna al bambino.

### Contesto non visuale del bambino

EMPOWER vede solo l'immagine RGB frontale. Il bambino puo' non essere visibile
quando il robot osserva gli oggetti. Per questo la YAML aggiunge contesto non
visuale:

```yaml
child_context:
  enabled: true
  planner_description: >
    The child is present in the room but is not visible in the RGB image.
    The child is a known non-visual target.
    The child is a valid navigation target and a valid drop destination.
    If the task asks to bring an object to the child, the plan must include
    NAVIGATE to the child before dropping the object.
  aliases: ["child", "the child", "kid"]
```

Questo contesto viene passato all'agente LLM di planning, ma viene filtrato
dalla detection visuale per evitare che YOLO-World provi a rilevare il bambino
nell'immagine.

### Navigazione verso target non visuali

La YAML definisce pose hardcoded per target non visuali:

```yaml
child_navigation:
  enabled: true
  frame_id: map
  x: 0.0
  y: 0.95
  yaw: 1.5708
  via_waypoint:
    enabled: true
    x: 3.0
    y: 0.0
    yaw: 3.14159

person_navigation:
  enabled: true
  frame_id: map
  x: 0.0
  y: 0.0
  yaw: -1.5708
```

Il nodo low-level intercetta `NAVIGATE to child` e `NAVIGATE to person` senza
usare marker EMPOWER: genera goal Nav2 direttamente da queste pose.

### Simulazione oggetti

La manipolazione non e' reale. La YAML abilita pero' una simulazione via Gazebo:

```yaml
object_simulation:
  enabled: true
  gz_command: gz
  gz_world_name: person_table_child_room
  world_sdf_path: /root/exchange/exchange/person_table_child.world
  objects:
    book:
      aliases: ["book"]
      gazebo_entity: clear_red_book_on_right_table
      drop_z: 0.01
    computer:
      aliases: ["computer", "laptop", "laptop computer"]
      gazebo_entity: laptop_computer_on_right_table
      drop_z: 0.12
    peluche:
      aliases: ["peluche", "teddy bear", "plush toy", "stuffed animal"]
      gazebo_entity: teddy_bear_on_right_table
      drop_z: 0.01
```

Quando `low_level_execution_node` esegue:

- `GRAB peluche`: sposta/nasconde il modello Gazebo del peluche in
  `hidden_z = -10.0`;
- `DROP peluche`: riposiziona il modello. Se l'ultima navigazione era verso il
  bambino, usa una posa fissa sul tavolo di consegna:
  `(x=0.0, y=1.65, z=1.30, yaw=1.5708)` nella YAML corrente.

Questa simulazione e' centrale nello stato attuale: il piano puo' manipolare il
peluche anche se la detection finale non ha un marker specifico del peluche,
perche' il mapping simbolico `peluche -> teddy_bear_on_right_table` e'
hardcoded.

## Flusso EMPOWER manuale

Il flusso manuale tipico richiede terminali separati.

### 1. Gazebo

```bash
ros2 launch tiago_gazebo tiago_gazebo.launch.py \
  is_public_sim:=True \
  gazebo_version:=classic \
  world_name:=/root/exchange/exchange/person_table_child.world
```

### 2. Nav2 con ground truth

```bash
ros2 launch empower_ros2 tiago_nav2_ground_truth.launch.py \
  map:=/root/exchange/exchange/maps/person_table_child_static.yaml
```

### 3. Capture RGB-D

```bash
ros2 launch empower_ros2 empower_capture.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
```

Nodo coinvolto: `scene_capture_node`.

Input ROS:

- RGB: `/head_front_camera/rgb/image_raw`
- depth: `/head_front_camera/depth/image_raw`
- camera info: `/head_front_camera/depth/camera_info`

Output:

```text
empower_data/images/person_table_child_task/scan.jpg
empower_data/output/person_table_child_task/depth.npy
empower_data/output/person_table_child_task/depth_pointcloud.pcd
empower_data/output/person_table_child_task/camera_info.pkl
empower_data/output/person_table_child_task/camera_info.json
```

Implementazione:

- converte RGB con `cv_bridge` e salva `scan.jpg`;
- converte depth in millimetri;
- ricostruisce la point cloud con gli intrinseci camera;
- applica trasformazione ottica `[[1,0,0], [0,-1,0], [0,0,-1]]`;
- salva PCD e camera info.

### 4. Model cacher

```bash
ros2 launch empower_ros2 empower_models_cacher.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
```

Nodo coinvolto: `models_cacher_node`.

Ruolo:

- carica una sola volta i modelli pesanti:
  - YOLO-World;
  - EfficientViT-SAM encoder/decoder ONNX;
  - spaCy `en_core_web_sm`;
  - word2vec `word2vec-google-news-300`;
- legge `OPENAI_API_KEY` oppure `openai_api_key.txt`;
- crea un `EmpowerRos2Loader`, compatibile con il codice EMPOWER upstream;
- apre un socket TCP su `127.0.0.1:10000`;
- aspetta un messaggio JSON con `command: "detection"`;
- esegue detection, grounding e planning.

Questo design evita di ricaricare i modelli ad ogni planning. Il nodo rimane
attivo e `plan_generator_node` lo usa come servizio esterno via socket.

### 5. Planning EMPOWER

```bash
ros2 launch empower_ros2 empower_plan.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
```

Nodo coinvolto: `plan_generator_node`.

Ruolo:

- cancella un eventuale `planning.txt` precedente;
- invia al model cacher un payload:

```json
{
  "command": "detection",
  "task_description": "<task corrente>"
}
```

- attende la comparsa di `planning.txt`;
- converte il piano in `planning.json` usando `plan_parser.py`;
- valida, se possibile, la presenza di `NAVIGATE to child` quando il task cita
  il bambino;
- genera report leggibili tramite `report_writer.py`.

Output principali:

```text
empower_data/output/person_table_child_task/planning.pkl
empower_data/output/person_table_child_task/planning.txt
empower_data/output/person_table_child_task/planning.json
empower_data/output/person_table_child_task/relations.txt
empower_data/output/person_table_child_task/relations.json
empower_data/output/person_table_child_task/scene_description.txt
empower_data/output/person_table_child_task/symbolic_plan.txt
empower_data/output/person_table_child_task/detection.pkl
empower_data/output/person_table_child_task/detections.json
empower_data/output/person_table_child_task/empower_report.txt
```

### 6. Visualizzazione EMPOWER

```bash
ros2 launch empower_ros2 empower_visualization.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
```

Nodi coinvolti:

- `color_pcl_node`
- `marker_centroids_node`

`color_pcl_node`:

- legge `depth_pointcloud.pcd`, `detection.pkl`, `camera_info.pkl` e `scan.jpg`;
- proietta le maschere 2D sulla point cloud;
- colora i punti segmentati;
- salva:
  - `colored_image.png`
  - `colored_pcl.pcd`
  - `colors_dict.pkl`
- pubblica `/empower/colored_pointcloud`.

`marker_centroids_node`:

- legge `detection.pkl` e `colors_dict.pkl`;
- calcola centroidi e punti massimi dei cluster;
- prova a trasformare i marker nel frame `map`;
- pubblica:
  - `/pcl_centroids`
  - `/pcl_names`
  - `/pcl_maximum`

Questi marker sono usati anche dall'esecuzione low-level per navigare verso
target visuali come `shelf`.

### 7. Esecuzione low-level

```bash
ros2 launch empower_ros2 empower_execute.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
```

Nodi coinvolti:

- `color_pcl_node`
- `marker_centroids_node`
- `low_level_execution_node`

`low_level_execution_node` carica `planning.json` se presente, altrimenti
parsa `planning.txt`. Le azioni supportate dal parser sono:

```text
NAVIGATE
GRAB
DROP
PULL
PUSH
```

Stato implementativo:

- `NAVIGATE` e' implementato via Nav2 `NavigateToPose`;
- `GRAB` e `DROP` sono simulati via `gz model` solo per oggetti configurati;
- `PUSH` e `PULL` vengono riconosciuti ma non implementati;
- non c'e' controllo reale di braccio/gripper.

Per target visuali, il nodo risolve il marker da `/pcl_names` e
`/pcl_centroids`, poi calcola un goal Nav2 con `standoff_distance`: il robot si
ferma a distanza dal centroide e si orienta verso l'oggetto.

Per target non visuali (`child`, `person`) usa pose configurate nella YAML.

## EMPOWER: come e' realizzato internamente

La parte piu importante da modificare e' questa.

### Loader ROS 2 compatibile con EMPOWER

`loader_adapter.py` definisce `EmpowerRos2Loader`, che espone gli attributi che
il codice EMPOWER originale si aspetta:

- `SCAN_DIR`
- `DUMP_DIR`
- `CONFIG`
- `IMAGES`
- `OUTPUT`
- `YOLOW_PATH`
- `ENCODER_PATH`
- `DECODER_PATH`
- `nlp`
- `wv`
- `yolow_model`
- `vit_sam_model`
- `use_case`
- `child_planner_context`
- `child_aliases`

Questo adapter e' il ponte principale fra ROS 2 e la logica EMPOWER portata
dalla repo upstream.

### Agenti LLM

`agents.py` usa OpenAI Chat Completions con modello `gpt-4o`.

La pipeline multi-agent e':

1. `enviroment_agent`:
   - input: immagine RGB base64 + task visuale;
   - output: triple `(subject, relation, object)`;
   - relazioni consentite/promosse: `right to`, `left to`, `behind to`,
     `beside to`, `on`, ecc.
2. `description_agent`:
   - input: immagine + relazioni del primo agente;
   - output: descrizione testuale della scena e istruzioni preliminari.
3. planning agent:
   - input: task completo + descrizione + relazioni;
   - output: solo azioni simboliche fra `NAVIGATE`, `GRAB`, `DROP`, `PULL`,
     `PUSH`.

Quando il task cita il bambino, `Agents` costruisce anche una versione
"visual-only" del task per evitare che gli agenti visuali pretendano di vedere
il bambino. Il contesto non visuale del bambino viene invece aggiunto al prompt
di planning.

### Detection e visual grounding

`detection.py` contiene la classe `Detection`.

Sequenza:

1. `run_experiment()`:
   - legge `scan.jpg`;
   - codifica l'immagine in base64;
   - crea `Agents`;
   - ottiene:
     - `environment_agent_info`
     - `description_agent_info`
     - `planning_agent_info`
   - salva `planning.pkl` e `planning.txt`.
2. `run_image()`:
   - legge le relazioni generate;
   - filtra relazioni che coinvolgono il bambino non visuale;
   - estrae classi nominali usando spaCy e word2vec;
   - passa le classi a YOLO-World;
   - per ogni bbox sopra soglia usa EfficientViT-SAM per segmentare;
   - salva mask, bbox e label in `detection.pkl`;
   - genera immagini debug `mask.jpg`, `yolo.jpg`, `rgb_*.jpg`,
     `scan_with_bb.jpg`.

Il grounding delle label avviene confrontando le relazioni LLM con le detection.
La logica e' euristica:

- usa similarita' word2vec con soglia circa `0.708`;
- per relazioni `on`, `left`, `right` prova a scegliere bbox compatibili in
  coordinate immagine;
- aggiorna `data_reordered`, che diventa il `detection.pkl` finale.

Punto fragile attuale: nel run presente in repo `detections.json` contiene solo
una detection:

```json
{
  "0": {
    "label": "shelf",
    "bbox": [197.22, 241.03, 488.45, 478.15],
    "mask_shape": [480, 640, 3]
  }
}
```

Il piano invece e':

```text
NAVIGATE to the shelf
GRAB peluche
NAVIGATE to the child
DROP peluche
```

Quindi l'attuale esecuzione si appoggia al marker visuale dello `shelf` per la
navigazione iniziale e al mapping statico Gazebo per la manipolazione del
`peluche`.

### Parsing del piano

`plan_parser.py` converte il testo LLM in JSON strutturato.

Esempio attuale:

```json
[
  {
    "step": 1,
    "action": "NAVIGATE",
    "description": "NAVIGATE to the shelf",
    "target": "the shelf"
  },
  {
    "step": 2,
    "action": "GRAB",
    "description": "GRAB peluche",
    "target": "peluche"
  },
  {
    "step": 3,
    "action": "NAVIGATE",
    "description": "NAVIGATE to the child",
    "target": "child"
  },
  {
    "step": 4,
    "action": "DROP",
    "description": "DROP peluche",
    "target": "peluche"
  }
]
```

Il parser normalizza `child`, `the child`, `kid` in `child`. Per `DROP` prova a
riconoscere relazioni spaziali come `left to`, `right to`, `on`, `into`, `near`,
`at`, e compila eventuali campi `spatial_relation` e `reference`.

### Report leggibili

`report_writer.py` rigenera file `.txt` e `.json` a partire da pickle e piano.
`export_reports` permette di rigenerare solo i report senza rifare detection o
planning:

```bash
ros2 run empower_ros2 export_reports \
  --ros-args \
  --params-file /root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
```

## Output EMPOWER attualmente presenti

La repo contiene output gia' generati per:

```text
empower_data/output/person_table_child_task
```

File principali:

- `scene_description.txt`
- `relations.txt`
- `relations.json`
- `planning.txt`
- `planning.json`
- `symbolic_plan.txt`
- `detection.pkl`
- `detections.json`
- `depth.npy`
- `depth_pointcloud.pcd`
- `camera_info.json`
- `colored_pcl.pcd`
- `colored_image.png`
- `colors_dict.pkl`
- `empower_report.txt`

Contenuto sintetico dell'ultimo run:

- scena descritta come shelf con book e peluche;
- relazioni:
  - `(peluche, on, shelf)`
  - `(book, on, shelf)`
  - `(peluche, right to, book)`
- piano:
  - `NAVIGATE to the shelf`
  - `GRAB peluche`
  - `NAVIGATE to the child`
  - `DROP peluche`

## Missione orchestrata HRI + EMPOWER

Il launch integrato e':

```bash
ros2 launch empower_ros2 hri_empower_mission.launch.py \
  hri_params_file:=/root/exchange/src/empower_hri/config/hri_cpu_english.yaml \
  hri_input_mode:=voice
```

Questo launch avvia:

- `models_cacher_node` di EMPOWER;
- `voice_llm_node` di `empower_hri`;
- `mission_orchestrator_node`.

La configurazione missione e':

```text
src/empower_ros2/config/hri_empower_mission.yaml
```

Pose correnti:

```yaml
therapist_pose: [0.0, -1.3, -1.5708]
spawn_pose: [4.8, 0.0, 1.72]
child_pose: [0.0, 0.95, 1.5708]
```

Stati missione:

```text
GO_TO_THERAPIST
WAIT_TASK
ACK_TASK
WAIT_ACTIVITY
ACK_ACTIVITY
GO_TO_SPAWN
RUN_EMPOWER
GO_TO_CHILD
CHILD_READY
CHILD_ACTIVITY
MISSION_COMPLETE
```

Nel config attuale:

```yaml
capture_before_empower: true
execute_empower_plan: true
child_delivery_handled_by_empower: true
```

Quindi l'orchestratore:

1. naviga dal terapista;
2. aspetta il task su `/hri/user_utterance`;
3. fa ack via `/hri/dialog_command`;
4. aspetta la descrizione dell'attivita' per il bambino;
5. torna allo spawn;
6. esegue capture EMPOWER;
7. lancia planning EMPOWER;
8. lancia esecuzione EMPOWER;
9. se `child_delivery_handled_by_empower` e' true, non fa una seconda
   navigazione hardcoded al bambino;
10. pubblica `/mission/child_ready`;
11. avvia l'attivita' bambino via HRI.

Topic missione importanti:

- `/mission/state`
- `/mission/feedback`
- `/mission/task_description`
- `/mission/child_ready`
- `/hri/user_utterance`
- `/hri/dialog_command`
- `/hri/dialog_complete`

## Package `empower_hri`

`empower_hri` implementa il dialogo locale. Console script:

```text
voice_llm_node = empower_hri.voice_llm_node:main
```

Launch:

- `hri_voice_llm_english.launch.py`
- `hri_voice_llm_cpu.launch.py`

Config principali:

- `src/empower_hri/config/hri_cpu_english.yaml`
- `src/empower_hri/config/hri_cpu_local.yaml`

Pipeline inglese tipica:

```text
microfono -> faster-whisper base -> Ollama/Qwen -> system TTS -> speaker
```

Pipeline locale/testuale:

```text
terminal input -> Ollama/Qwen -> pyttsx3/system TTS
```

Il nodo pubblica:

- `/hri/user_utterance`
- `/hri/robot_utterance`
- `/hri/dialog_state`
- `/hri/dialog_complete`

Il nodo sottoscrive:

- `/hri/dialog_command`
- `/mission/state`

Quando `follow_mission_state_for_therapist_input` e' true, l'HRI legge input
dal terapista solo negli stati missione `WAIT_TASK` e `WAIT_ACTIVITY`. Questo
evita che input vocali casuali entrino nella missione fuori fase.

Per il bambino, il nodo supporta tipi di attivita':

- `computer_emotion_recognition`
- `mood_check_teddy`
- `storytelling`
- `generic_child_activity`

La normalizzazione dell'attivita' viene fatta nell'orchestratore in base a
keyword nella frase del terapista.

## Package `gaze_estimation`

`gaze_estimation` e' un package ROS 2 separato e sostanzialmente autonomo.
Console script:

```text
gaze_webcam = gaze_estimation.gaze_webcam_node:main
```

Launch:

```bash
ros2 launch gaze_estimation gaze_webcam.launch.py
```

Config:

```text
src/gaze_estimation/config/gaze_webcam.yaml
```

Funzionamento:

1. apre una webcam OpenCV (`camera_index`);
2. ridimensiona frame a `resize_width`;
3. usa Gazelle/Gaze-LLE per stimare face box, gaze point, heatmap e
   in/out score;
4. usa YOLO-World per rilevare oggetti target;
5. decide se lo sguardo cade su uno degli oggetti con:
   - controllo punto gaze dentro bbox con margine;
   - opzionale controllo ratio heatmap dentro bbox;
6. mostra overlay OpenCV e logga stati:
   - `NO_FACE`
   - `GAZE_OUT_OF_FRAME`
   - `NO_TARGET_OBJECT`
   - `LOOKING_AT_OBJECT: <class>`
   - `LOOKING_ELSEWHERE`

Pesi attesi:

```text
models/gaze_estimation/gazelle_dinov2_vitl14_inout.pt
models/gaze_estimation/yolov8x-worldv2.pt
models/gaze_estimation/best.pt
```

Nota: nel file YAML di gaze estimation il path `weights_dir` punta a un path
host specifico (`/home/lorenzo/Scrivania/ElectiveAI/...`), mentre il README
consiglia `/root/exchange/models/gaze_estimation` dentro container. Questo e'
un potenziale punto da allineare.

Al momento non risulta un'integrazione diretta fra `gaze_estimation` e la
missione EMPOWER/HRI: e' una componente eseguibile separatamente.

## Comandi operativi principali

Build dei package principali:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
colcon build --symlink-install --packages-select empower_ros2 empower_hri
source /root/exchange/install/setup.bash
```

Gazebo:

```bash
export GAZEBO_MODEL_PATH=/root/exchange/exchange/models:${GAZEBO_MODEL_PATH:-}
ros2 launch tiago_gazebo tiago_gazebo.launch.py \
  is_public_sim:=True \
  gazebo_version:=classic \
  world_name:=/root/exchange/exchange/person_table_child.world
```

Nav2:

```bash
ros2 launch empower_ros2 tiago_nav2_ground_truth.launch.py \
  map:=/root/exchange/exchange/maps/person_table_child_static.yaml
```

Pipeline EMPOWER manuale:

```bash
ros2 launch empower_ros2 empower_capture.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml

ros2 launch empower_ros2 empower_models_cacher.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml

ros2 launch empower_ros2 empower_plan.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml

ros2 launch empower_ros2 empower_execute.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
```

Missione integrata:

```bash
ros2 launch empower_ros2 hri_empower_mission.launch.py \
  hri_params_file:=/root/exchange/src/empower_hri/config/hri_cpu_english.yaml \
  hri_input_mode:=voice
```

## Limiti e punti fragili attuali

- La pipeline EMPOWER dipende da OpenAI `gpt-4o` tramite `agents.py`; serve
  `OPENAI_API_KEY` o `/root/exchange/openai_api_key.txt`.
- Il model cacher usa un socket TCP custom, non un service/action ROS 2.
- La detection/grounding e' euristica e puo' produrre label non corrispondenti
  perfettamente agli oggetti manipolabili.
- Il bambino e' gestito come target non visuale hardcoded, non tramite
  perception.
- Le pose di bambino/persona/spawn/consegna sono hardcoded nei file YAML.
- La manipolazione fisica non esiste: `GRAB` e `DROP` sono simulati solo per
  `book`, `computer`, `peluche` via Gazebo CLI.
- `PUSH` e `PULL` sono accettati dal piano ma non implementati.
- L'ultimo output rileva solo `shelf` come detection finale, ma il piano
  manipola `peluche`; questo funziona solo grazie a `object_simulation`.
- Ci sono file `__pycache__` e output generati versionati/non puliti nella repo.
- Alcuni README/config non sono completamente allineati allo stato corrente
  (`book` vs `peluche`, path gaze weights host vs container).

## Punti da tenere presenti per modifiche future su EMPOWER

Per cambiare EMPOWER bisogna prima decidere quale livello modificare:

1. Prompt/planning:
   - modificare `src/empower_ros2/python/agents.py`;
   - impatta relazioni, descrizione scena e piano simbolico.
2. Detection/grounding:
   - modificare `src/empower_ros2/python/detection.py`;
   - impatta classi YOLO-World, matching relazioni-detection, label finali,
     `detection.pkl`.
3. Parsing piano:
   - modificare `src/empower_ros2/python/empower_ros2/plan_parser.py`;
   - impatta `planning.json` e quindi l'esecuzione.
4. Esecuzione low-level:
   - modificare `src/empower_ros2/python/empower_ros2/low_level_execution_node.py`;
   - impatta Nav2, target non visuali e simulazione Gazebo degli oggetti.
5. Configurazione scenario:
   - modificare `src/empower_ros2/config/empower_person_table_child.yaml`;
   - impatta task, topic, pose, aliases, oggetti simulabili, standoff distance.
6. Orchestrazione missione:
   - modificare `src/empower_ros2/python/empower_ros2/mission_orchestrator_node.py`;
   - impatta quando viene lanciata EMPOWER e come interagisce con HRI/Nav2.

Per la maggior parte dei cambiamenti al comportamento EMPOWER e' probabile che
serva toccare insieme almeno:

- `agents.py` per ottenere il piano desiderato;
- `plan_parser.py` se il formato del piano cambia;
- `low_level_execution_node.py` se una nuova azione deve essere eseguita;
- `empower_person_table_child.yaml` se servono nuovi target, alias o mapping
  Gazebo.

