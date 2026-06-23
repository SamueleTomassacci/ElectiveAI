# Stato attuale della repo ElectiveAI

Questo documento e' pensato per essere passato a ChatGPT come contesto tecnico
della repo. Descrive lo stato attuale del codice, l'architettura dei package
ROS 2, il flusso di esecuzione della simulazione TIAGo in Gazebo e, in modo
particolare, la pipeline EMPOWER e l'interazione robot-bambino con gaze
estimation integrata.

## Sintesi generale

La repo contiene il codice necessario per eseguire una simulazione del robot
TIAGo in Gazebo Classic, con ROS 2 Humble, Nav2 e una pipeline di task planning
basata su EMPOWER. La scena supportata e' una scena custom:

- world: `exchange/scene.world`
- mappa statica Nav2: `exchange/maps/scene.yaml`
- configurazione EMPOWER: `src/empower/config/empower_config.yaml`

La repo e' organizzata intorno a tre componenti principali:

1. `empower`: adapter ROS 2 della pipeline EMPOWER, piu orchestrazione
   missione, Nav2, capture RGB-D (single-view e multi-view), planning,
   grounding, aggregazione multi-vista, visualizzazione ed esecuzione low-level.
2. `llm_interaction`: componente di interazione vocale/testuale. Usa STT,
   LLM locale via Ollama e TTS per raccogliere istruzioni dal terapista e
   dialogare con il bambino. Include richiamo attenzione basato su gaze.
3. `gaze_estimation`: componente per stimare se una persona guarda un oggetto
   target usando webcam, Gazelle/Gaze-LLE e YOLO-World. Include un nodo
   `attention_monitor` che monitora la disattenzione durante le attivita'
   con il bambino.

Il sistema attuale e' un MVP integrato: TIAGo puo' navigare in Gazebo usando
Nav2, generare un piano simbolico da immagini RGB-D (ora anche da scansione
multi-view a 360 gradi) e task testuale, eseguire azioni `NAVIGATE`, e simulare
alcune azioni di manipolazione (`GRAB`/`DROP`) su oggetti noti tramite comandi
Gazebo. Non esiste ancora una manipolazione reale con braccio, MoveIt o gripper.

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
- monta il socket PulseAudio e la cookie di autenticazione PA
  (`~/.config/pulse/cookie`) se disponibile;
- avvia `/root/exchange/docker/entrypoint_tiago.sh`.

L'entrypoint fa il source di:

```bash
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
```

Nel Dockerfile (`dockerfiles/Dockerfile`) sono installati ROS 2 Humble, Nav2,
Gazebo ROS packages, dipendenze TIAGo, dipendenze EMPOWER, modelli EMPOWER
scaricati in `/opt/empower_models/config`, dipendenze HRI audio/STT/TTS e
`pulseaudio-utils` per output audio nel container.

## Scena Gazebo e navigazione

La scena principale e' `exchange/scene.world`, con world name:

```text
scene
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

La mappa Nav2 e' `exchange/maps/scene.yaml`:

```yaml
resolution: 0.05
origin: [-4.7, -3.9, 0.0]
mode: trinary
```

Il bringup Nav2 custom e':

```bash
ros2 launch empower tiago_nav2_ground_truth.launch.py \
  map:=/root/exchange/exchange/maps/scene.yaml
```

Questo launch avvia:

- `nav2_map_server`
- `nav2_lifecycle_manager`
- `ground_truth_localizer_node`
- `nav2_bringup/launch/navigation_launch.py`

La localizzazione non usa AMCL: viene pubblicata da
`ground_truth_localizer_node` a partire da `/ground_truth_odom`, per mantenere
semplice e stabile la simulazione.

## Package `empower`: ruolo e struttura

`empower` e' il package centrale. E' un package `ament_python` che espone
questi console script:

- `scene_capture_node`
- `multi_view_capture_node`
- `multi_view_aggregator`
- `models_cacher_node`
- `plan_generator_node`
- `export_reports`
- `color_pcl_node`
- `marker_centroids_node`
- `ground_truth_localizer_node`
- `low_level_execution_node`
- `mission_orchestrator_node`

Il package contiene due livelli di codice:

- moduli EMPOWER stile upstream in `src/empower/python/*.py`
  (`agents.py`, `detection.py`, `models.py`, `loader.py`, `paths.py`);
- adapter ROS 2 in `src/empower/python/empower/*.py`.

I file `agents.py`, `detection.py`, `models.py`, `loader.py`, `paths.py` sono
presenti anche direttamente in `src/empower/` e risultano identici alle
copie sotto `src/empower/python/`. La configurazione `setup.py` installa
questi file come `py_modules`, mentre il package ROS 2 vero e proprio sta in
`python/empower`.

## Configurazione principale EMPOWER

Il file corrente e':

```text
src/empower/config/empower_config.yaml
```

Parametri chiave:

```yaml
use_case: therapy_task
task_description: "Take the peluche, go to the child and drop the peluche"
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
standoff_distance: 1.2
continue_on_nav_failure: false
```

### Multi-view EMPOWER (novita')

La YAML abilita la modalita' multi-view per la capture:

```yaml
multi_view:
  enabled: true
  overlap_ratio: 0.15
  yaw_coverage: 6.283185307179586   # 2*pi = 360 gradi
  min_views: 4
  max_views: 12
  settle_time_sec: 1.0
  rotation_mode: nav2
  save_debug: true
  output_subdir: views
  merge:
    default_distance_m: 0.35
    small_object_distance_m: 0.25
    large_object_distance_m: 0.60
    z_threshold_m: 0.25
```

Quando `multi_view.enabled` e' true, l'orchestratore usa `multi_view_capture_node`
al posto di `scene_capture_node`. Il nodo ruota il robot sul posto (via Nav2)
catturando una serie di viste RGB-D a 360 gradi con sovrapposizione configurabile.
I dettagli sono nella sezione dedicata piu' sotto.

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
  drop_distance: 0.75
  child_delivery_table_drop_x: 0.0
  child_delivery_table_drop_y: 1.65
  child_delivery_table_drop_z: 1.7
  child_delivery_table_drop_yaw: 1.5708
  hidden_x: 0.0
  hidden_y: 0.0
  hidden_z: -10.0
  gz_command: gz
  gz_world_name: scene
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

- `GRAB peluche`: sposta il modello Gazebo del peluche nella posizione nascosta
  (`hidden_z = -10.0`);
- `DROP peluche`: riposiziona il modello. Se l'ultima navigazione era verso il
  bambino, usa una posa fissa sul tavolo di consegna
  `(x=0.0, y=1.65, z=1.7, yaw=1.5708)` nella YAML corrente.

Questa simulazione e' centrale nello stato attuale: il piano puo' manipolare il
peluche anche se la detection finale non ha un marker specifico del peluche,
perche' il mapping simbolico `peluche -> teddy_bear_on_right_table` e'
hardcoded.

## Pipeline EMPOWER multi-view (novita')

### Multi-view capture (`multi_view_capture_node`)

File: `src/empower/python/empower/multi_view_capture_node.py`

Launch:

```bash
ros2 launch empower empower_multiview_capture.launch.py \
  params_file:=/root/exchange/src/empower/config/empower_config.yaml
```

Il nodo:

1. Attende i messaggi RGB, depth e camera info;
2. Calcola il campo visivo orizzontale (HFOV) dalla CameraInfo;
3. Determina il numero di viste necessarie per coprire `yaw_coverage`
   (default 360 gradi) con sovrapposizione `overlap_ratio`;
4. Ruota il robot sul posto usando Nav2 (mode `nav2`);
5. Per ogni vista salva:
   - `rgb.jpg`, `depth.npy`, `camera_info.pkl/.json`, `camera_pose_map.json`
   (con la posa camera nel frame `map` ottenuta via TF);
6. Scrive un `views_manifest.json` con metadati di tutte le viste;
7. La prima vista scrive anche gli output legacy (`scan.jpg`,
   `depth_pointcloud.pcd`, ecc.) per retrocompatibilita'.

Output strutturato:

```text
output/empower_output/therapy_task/views_manifest.json
output/empower_output/therapy_task/views/view_000/rgb.jpg
output/empower_output/therapy_task/views/view_000/depth.npy
output/empower_output/therapy_task/views/view_000/camera_info.pkl
output/empower_output/therapy_task/views/view_000/camera_pose_map.json
...
output/empower_output/therapy_task/views/view_NNN/...
```

### Multi-view aggregation (`multi_view_aggregator`)

File: `src/empower/python/empower/multi_view_aggregator.py`

Dopo che EMPOWER ha eseguito detection su ogni singola vista, l'aggregatore:

1. Legge il `views_manifest.json` e per ogni vista carica `detection.pkl`,
   `depth.npy`, `camera_info.pkl` e `camera_pose_map.json`;
2. Per ogni detection, proietta i pixel della maschera nello spazio 3D (camera
   frame) e li trasforma in coordinate `map` usando la posa camera;
3. Normalizza le label usando un sistema di alias configurabile
   (es. "teddy bear" -> "peluche", "laptop" -> "computer");
4. Raggruppa le detection di viste diverse che si riferiscono allo stesso
   oggetto fisico, usando distanza euclidea nel piano XY e soglia Z;
5. Calcola centroide, bounding box 3D e confidence per ogni oggetto globale;
6. Produce:
   - `global_detections.json`: lista di oggetti con centroide nel frame `map`;
   - `global_scene_graph.json`: oggetti + descrizione testuale;
   - `global_scene_description.txt`: descrizione leggibile.

### Agenti LLM con supporto multi-view

`agents.py` supporta ora immagini multiple: se riceve una lista di immagini
base64 (una per vista), costruisce prompt che istruiscono l'LLM a trattarle
come viste complementari della stessa scena (prefisso "View 0:", "View 1:",
ecc.). La pipeline multi-agent e' altrimenti invariata:

1. `enviroment_agent`: relazioni spaziali;
2. `description_agent`: descrizione scena;
3. planning agent: piano simbolico.

### Esecuzione low-level con global_detections.json

Quando `multi_view.enabled` e' true, `low_level_execution_node` carica
`global_detections.json` e lo usa per risolvere i target NAVIGATE:

- Per ogni step NAVIGATE, cerca il target tra gli oggetti globali usando le
  label normalizzate e gli alias;
- Costruisce un goal Nav2 usando il centroide dell'oggetto nel frame `map` con
  `standoff_distance`;
- I publisher point-cloud/marker (`color_pcl_node`, `marker_centroids_node`)
  vengono saltati in modalita' multi-view.

### Model cacher con supporto multi-view

`models_cacher_node` riceve i parametri `multi_view.enabled` e
`multi_view.merge.*`, e li passa al `EmpowerLoader`. Il loader supporta
la pipeline di detection per-vista e l'aggregazione successiva. Anche gli
alias degli oggetti (`object_simulation.objects.*.aliases`) vengono passati
al loader per normalizzare le label in fase di aggregazione.

## Flusso EMPOWER single-view (legacy)

Il flusso manuale tipico richiede terminali separati.

### 1. Gazebo

```bash
ros2 launch tiago_gazebo tiago_gazebo.launch.py \
  is_public_sim:=True \
  gazebo_version:=classic \
  world_name:=/root/exchange/exchange/scene.world
```

### 2. Nav2 con ground truth

```bash
ros2 launch empower tiago_nav2_ground_truth.launch.py \
  map:=/root/exchange/exchange/maps/scene.yaml
```

### 3. Capture RGB-D (single-view)

```bash
ros2 launch empower empower_capture.launch.py \
  params_file:=/root/exchange/src/empower/config/empower_config.yaml
```

Nodo coinvolto: `scene_capture_node`.

### 4. Model cacher

```bash
ros2 launch empower empower_models_cacher.launch.py \
  params_file:=/root/exchange/src/empower/config/empower_config.yaml
```

Nodo coinvolto: `models_cacher_node`.

Ruolo:

- carica una sola volta i modelli pesanti:
  - YOLO-World;
  - EfficientViT-SAM encoder/decoder ONNX;
  - spaCy `en_core_web_sm`;
  - word2vec `word2vec-google-news-300`;
- legge `OPENAI_API_KEY` oppure `openai_api_key.txt`;
- crea un `EmpowerLoader`, compatibile con il codice EMPOWER upstream;
- apre un socket TCP su `127.0.0.1:10000`;
- aspetta un messaggio JSON con `command: "detection"`;
- esegue detection, grounding e planning.

### 5. Planning EMPOWER

```bash
ros2 launch empower empower_plan.launch.py \
  params_file:=/root/exchange/src/empower/config/empower_config.yaml
```

Nodo coinvolto: `plan_generator_node`.

### 6. Visualizzazione EMPOWER

```bash
ros2 launch empower empower_visualization.launch.py \
  params_file:=/root/exchange/src/empower/config/empower_config.yaml
```

Nodi coinvolti: `color_pcl_node`, `marker_centroids_node`.

### 7. Esecuzione low-level

```bash
ros2 launch empower empower_execute.launch.py \
  params_file:=/root/exchange/src/empower/config/empower_config.yaml
```

Azioni supportate:

- `NAVIGATE` e' implementato via Nav2 `NavigateToPose`;
- `GRAB` e `DROP` sono simulati via `gz model` solo per oggetti configurati;
- `PUSH` e `PULL` vengono riconosciuti ma non implementati;
- non c'e' controllo reale di braccio/gripper.

## Missione orchestrata HRI + EMPOWER

Il launch integrato e':

```bash
ros2 launch empower hri_empower_mission.launch.py \
  hri_params_file:=/root/exchange/src/llm_interaction/config/llm_config.yaml \
  hri_input_mode:=voice
```

Questo launch avvia:

- `models_cacher_node` di EMPOWER;
- `voice_llm_node` di `llm_interaction`;
- `mission_orchestrator_node`.

La configurazione missione e':

```text
src/empower/config/task_config.yaml
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
MISSION_FAILED
```

Nel config attuale:

```yaml
capture_before_empower: true
execute_empower_plan: true
child_delivery_handled_by_empower: true
```

L'orchestratore:

1. naviga dal terapista;
2. aspetta il task su `/hri/user_utterance`;
3. fa ack via `/hri/dialog_command`;
4. aspetta la descrizione dell'attivita' per il bambino;
5. torna allo spawn;
6. esegue capture EMPOWER (multi-view se abilitato, altrimenti single-view);
7. lancia planning EMPOWER;
8. lancia esecuzione EMPOWER (con o senza point cloud/marker publishers a
   seconda della modalita' multi-view);
9. se `child_delivery_handled_by_empower` e' true, non fa una seconda
   navigazione hardcoded al bambino;
10. pubblica `/mission/child_ready`;
11. **lancia la gaze estimation come sottoprocesso** (se abilitata);
12. avvia l'attivita' bambino via HRI (stato `CHILD_ACTIVITY`);
13. al termine dell'attivita' ferma la gaze estimation;
14. transiziona a `MISSION_COMPLETE`.

Se la gaze estimation non riesce ad avviarsi (timeout, errore modelli, webcam
non disponibile), la missione transiziona a `MISSION_FAILED`.

## Interazione robot-bambino e gaze estimation (dettaglio)

Questa sezione descrive in dettaglio il flusso dell'interazione con il bambino,
che e' la parte piu' recente e integrata del sistema.

### Panoramica

Durante la fase `CHILD_ACTIVITY`, il sistema:

1. Il **mission orchestrator** lancia `gaze_with_attention.launch.py` come
   sottoprocesso, che avvia `gaze_webcam_node` e `attention_monitor_node`;
2. Il **gaze_webcam_node** cattura frame dalla webcam, stima la direzione
   dello sguardo e rileva oggetti target, pubblicando stato su `/gaze/status`;
3. L'**attention_monitor_node** monitora `/gaze/status` e genera alert di
   disattenzione su `/hri/attention_alert`;
4. Il **voice_llm_node** conduce l'attivita' con il bambino via LLM + TTS e,
   a fine turno, consuma eventuali alert per generare frasi di richiamo
   attenzione.

### Package `gaze_estimation`

Console script:

```text
gaze_webcam = gaze_estimation.gaze_webcam_node:main
attention_monitor = gaze_estimation.attention_monitor_node:main
```

Launch:

```bash
# Solo gaze estimation
ros2 launch gaze_estimation gaze_webcam.launch.py

# Gaze estimation + attention monitor (usato dalla missione)
ros2 launch gaze_estimation gaze_with_attention.launch.py
```

Config:

- `src/gaze_estimation/config/gaze_webcam.yaml`
- `src/gaze_estimation/config/attention_monitor.yaml`

#### `gaze_webcam_node`

File: `src/gaze_estimation/gaze_estimation/gaze_webcam_node.py`

Funzionamento:

1. Apre una webcam OpenCV (`camera_index`);
2. Ridimensiona frame a `resize_width`;
3. Usa Gazelle/DINOv2 per stimare face box, gaze point, heatmap e in/out score;
4. Usa YOLO-World per rilevare oggetti target (classi configurabili);
5. Decide se lo sguardo cade su uno degli oggetti con:
   - controllo punto gaze dentro bbox con margine;
   - opzionale controllo ratio heatmap dentro bbox;
6. Pubblica JSON su `/gaze/status` con campi:
   - `status`: uno fra `NO_FRAME`, `NO_FACE`, `GAZE_OUT_OF_FRAME`,
     `NO_TARGET_OBJECT`, `LOOKING_AT_OBJECT`, `LOOKING_ELSEWHERE`
   - `target_class`: nome classe oggetto guardato (o null)
   - `timestamp`: epoch float
7. Pubblica heartbeat su `/gaze/node_status` (`{"state": "READY"}`) ogni ~2s;
8. Mostra overlay OpenCV se `show_window` e' true.

Pesi attesi (sotto `weights_dir`, default `/root/exchange/models/gaze_estimation`):

```text
gazelle_dinov2_vitl14_inout.pt
yolov8x-worldv2.pt
yolov8n-face.pt
```

Target classes di default nella YAML:

```yaml
target_classes: ["book", "bottle", "cube", "sunglasses", "smartphone"]
```

#### `attention_monitor_node`

File: `src/gaze_estimation/gaze_estimation/attention_monitor_node.py`

Ruolo: monitora l'attenzione del bambino durante le attivita' HRI.

- **Sottoscrive**: `/gaze/status`, `/hri/dialog_state`, `/hri/dialog_command`
- **Pubblica**: `/hri/attention_alert`

Funzionamento:

1. Si attiva quando riceve `start_child_activity` su `/hri/dialog_command`,
   configurando le classi target valide in base al tipo di attivita';
2. Si arma (`armed=true`) quando `/hri/dialog_state` diventa `speaking`
   (il robot sta parlando e il bambino dovrebbe guardare il target);
3. Si disarma quando lo stato cambia (es. `listening`, il bambino parla);
4. Mentre e' armato, conta il tempo in cui lo sguardo NON e' su un target
   valido;
5. Se la disattenzione supera `attention_loss_threshold_sec` (default 2.5s),
   pubblica un alert JSON su `/hri/attention_alert`;
6. Cooldown di `attention_cooldown_sec` (default 20s) tra alert consecutivi;
7. Si disarma quando riceve `stop` su `/hri/dialog_command`.

Mappa attivita' -> target validi:

```yaml
activity_target_map:
  storytelling: ["book", "robot"]
  mood_check_teddy: ["teddy bear", "robot"]
  computer_emotion_recognition: ["laptop", "computer"]
  generic_child_activity: ["robot"]
```

#### Integrazione gaze nel mission orchestrator

File: `src/empower/python/empower/mission_orchestrator_node.py`

Parametri gaze nella configurazione missione (`task_config.yaml`):

```yaml
gaze_estimation:
  enabled: true
  camera_index: 0
  device: "auto"
  show_window: true
  startup_timeout_sec: 120.0
```

L'orchestratore:

- Sottoscrive `/gaze/node_status` per attendere che la gaze diventi `READY`;
- Sottoscrive `/mission/gaze_command` per stop manuale via
  `{"command": "stop_gaze"}`;
- Prima di `CHILD_ACTIVITY`, lancia `gaze_with_attention.launch.py` come
  sottoprocesso;
- Attende `READY` su `/gaze/node_status` con timeout configurabile (120s);
- Se il processo gaze muore prima del READY o va in timeout, la missione
  transiziona a `MISSION_FAILED`;
- A fine `CHILD_ACTIVITY` ferma il sottoprocesso gaze (SIGTERM/SIGKILL);
- Cleanup gaze anche nel `destroy_node()` e in caso di eccezione.

#### Integrazione gaze nel voice_llm_node (richiamo attenzione)

File: `src/llm_interaction/llm_interaction/voice_llm_node.py`

Il nodo HRI e' stato esteso per consumare gli alert di disattenzione:

- Sottoscrive `/hri/attention_alert`;
- Dopo ogni risposta TTS del turno di attivita' bambino, controlla se c'e' un
  alert pendente;
- Se c'e', genera una frase di richiamo attenzione con un `OllamaClient`
  dedicato (timeout 4s, max 30 token);
- La frase di richiamo viene pronunciata con TTS ma NON aggiunta alla history
  narrativa dell'attivita';
- Se il LLM non risponde in tempo, usa un fallback hardcoded per tipo di
  attivita':
  - `storytelling`: "Hey! Can you look at the book with me?"
  - `mood_check_teddy`: "The teddy bear wants to see your eyes!"
  - `computer_emotion_recognition`: "Let's look at the screen together!"
  - `generic_child_activity`: "Hey, look at me! Let's continue together!"

### Flusso completo interazione bambino

```text
1. Orchestratore -> stato CHILD_READY
2. Orchestratore -> lancia gaze_with_attention.launch.py (sottoprocesso)
3. gaze_webcam_node si inizializza (carica Gazelle, YOLO-World, apre webcam)
4. gaze_webcam_node pubblica {"state": "READY"} su /gaze/node_status
5. Orchestratore riceve READY -> stato CHILD_ACTIVITY
6. Orchestratore -> pubblica start_child_activity su /hri/dialog_command
7. attention_monitor_node riceve start_child_activity, configura target validi
8. voice_llm_node inizia l'attivita' (es. storytelling)
9. [Loop turni dialogo]:
   a. voice_llm_node genera risposta LLM e la pronuncia con TTS
   b. voice_llm_node pubblica "speaking" su /hri/dialog_state
   c. attention_monitor_node si arma
   d. gaze_webcam_node pubblica continuamente lo stato gaze su /gaze/status
   e. attention_monitor_node conta tempo disattenzione
   f. Se disattenzione > 2.5s -> pubblica alert su /hri/attention_alert
   g. voice_llm_node finisce TTS
   h. voice_llm_node controlla alert pendente
   i. Se alert: genera/pronuncia frase richiamo (non in history)
   j. voice_llm_node pubblica "listening" su /hri/dialog_state
   k. attention_monitor_node si disarma
   l. voice_llm_node ascolta risposta bambino (STT)
10. Fine attivita' -> voice_llm_node pubblica su /hri/dialog_complete
11. Orchestratore -> ferma gaze estimation
12. Orchestratore -> stato MISSION_COMPLETE
```

### Topic ROS 2 per gaze/interazione

| Topic | Tipo | Publisher | Formato |
|-------|------|-----------|---------|
| `/gaze/status` | `std_msgs/String` | `gaze_webcam_node` | `{"status": "...", "target_class": ... \| null, "timestamp": float}` |
| `/gaze/node_status` | `std_msgs/String` | `gaze_webcam_node` | `{"state": "READY" \| "ERROR", "message": "..."}` |
| `/hri/attention_alert` | `std_msgs/String` | `attention_monitor_node` | `{"reason": "...", "elapsed_sec": float, "activity_type": "...", "expected_target": "..."}` |
| `/mission/gaze_command` | `std_msgs/String` | (esterno/script) | `{"command": "stop_gaze"}` |
| `/hri/dialog_state` | `std_msgs/String` | `voice_llm_node` | `"speaking"`, `"listening"`, ecc. |
| `/hri/dialog_command` | `std_msgs/String` | `mission_orchestrator_node` | JSON con command |

## Package `llm_interaction`

`llm_interaction` implementa il dialogo locale. Console script:

```text
voice_llm_node = llm_interaction.voice_llm_node:main
```

Launch:

- `hri_voice_llm_english.launch.py`
- `hri_voice_llm_cpu.launch.py`

Config principali:

- `src/llm_interaction/config/llm_config.yaml`
- `src/llm_interaction/config/hri_cpu_local.yaml`

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
- `/hri/attention_alert`

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

## EMPOWER: come e' realizzato internamente

### Loader ROS 2 compatibile con EMPOWER

`loader_adapter.py` definisce `EmpowerLoader`, che espone gli attributi che
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
- `multi_view_enabled`
- `multi_view_config`
- `object_aliases`

Questo adapter e' il ponte principale fra ROS 2 e la logica EMPOWER portata
dalla repo upstream.

### Agenti LLM

`agents.py` usa OpenAI Chat Completions con modello `gpt-4o`.

La pipeline multi-agent e':

1. `enviroment_agent`:
   - input: immagine/i RGB base64 + task visuale;
   - output: triple `(subject, relation, object)`;
   - relazioni consentite/promosse: `right to`, `left to`, `behind to`,
     `beside to`, `on`, ecc.
   - in multi-view: riceve tutte le viste come immagini separate con prefisso
     "View N:" e istruzione di trattarle come viste complementari.
2. `description_agent`:
   - input: immagine/i + relazioni del primo agente;
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
   - legge `scan.jpg` (o le immagini di tutte le viste in multi-view);
   - codifica l'immagine/i in base64;
   - crea `Agents` (passando lista immagini in multi-view);
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
   - genera immagini debug.
   - In multi-view: esegue detection su ogni vista separatamente, poi lancia
     `multi_view_aggregator.run_aggregation()` per fondere le detection in
     oggetti globali nel frame `map`.

### Parsing del piano

`plan_parser.py` converte il testo LLM in JSON strutturato.

Il parser normalizza `child`, `the child`, `kid` in `child`. Per `DROP` prova a
riconoscere relazioni spaziali come `left to`, `right to`, `on`, `into`, `near`,
`at`, e compila eventuali campi `spatial_relation` e `reference`.

### Report leggibili

`report_writer.py` rigenera file `.txt` e `.json` a partire da pickle e piano.
`export_reports` permette di rigenerare solo i report senza rifare detection o
planning.

## Comandi operativi principali

Build dei package principali:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
colcon build --symlink-install --packages-select empower llm_interaction gaze_estimation
source /root/exchange/install/setup.bash
```

Gazebo:

```bash
export GAZEBO_MODEL_PATH=/root/exchange/exchange/models:${GAZEBO_MODEL_PATH:-}
ros2 launch tiago_gazebo tiago_gazebo.launch.py \
  is_public_sim:=True \
  gazebo_version:=classic \
  world_name:=/root/exchange/exchange/scene.world
```

Nav2:

```bash
ros2 launch empower tiago_nav2_ground_truth.launch.py \
  map:=/root/exchange/exchange/maps/scene.yaml
```

Multi-view capture manuale:

```bash
ros2 launch empower empower_multiview_capture.launch.py \
  params_file:=/root/exchange/src/empower/config/empower_config.yaml
```

Pipeline EMPOWER manuale (single-view legacy):

```bash
ros2 launch empower empower_capture.launch.py \
  params_file:=/root/exchange/src/empower/config/empower_config.yaml

ros2 launch empower empower_models_cacher.launch.py \
  params_file:=/root/exchange/src/empower/config/empower_config.yaml

ros2 launch empower empower_plan.launch.py \
  params_file:=/root/exchange/src/empower/config/empower_config.yaml

ros2 launch empower empower_execute.launch.py \
  params_file:=/root/exchange/src/empower/config/empower_config.yaml
```

Gaze estimation standalone:

```bash
ros2 launch gaze_estimation gaze_webcam.launch.py

# Con attention monitor
ros2 launch gaze_estimation gaze_with_attention.launch.py
```

Missione integrata:

```bash
ros2 launch empower hri_empower_mission.launch.py \
  hri_params_file:=/root/exchange/src/llm_interaction/config/llm_config.yaml \
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
- In multi-view, la qualita' dell'aggregazione dipende dalla precisione della
  posa camera (TF) e dalla qualita' della depth; oggetti piccoli o con
  depth rumoroso possono avere centroidi imprecisi.
- Le dipendenze Python di `gaze_estimation` (torch, torchvision, timm,
  ultralytics, ftfy) e i pesi DINOv2 (~350MB via torch.hub) non sono tutti
  nel Dockerfile e devono essere installati manualmente nel container o
  aggiunti al Dockerfile.
- La gaze estimation richiede una webcam funzionante; in simulazione pura
  senza webcam esterna, la gaze estimation fallisce e la missione si ferma.
- L'audio TTS richiede PulseAudio funzionante nel container (socket +
  cookie montati, `pulseaudio-utils` installato).
- Ci sono file `__pycache__` e output generati versionati/non puliti nella repo.
- Alcuni README/config non sono completamente allineati allo stato corrente.

## Punti da tenere presenti per modifiche future

Per cambiare il comportamento del sistema bisogna prima decidere quale
livello modificare:

1. Prompt/planning:
   - modificare `src/empower/python/agents.py`;
   - impatta relazioni, descrizione scena e piano simbolico.
2. Detection/grounding:
   - modificare `src/empower/python/detection.py`;
   - impatta classi YOLO-World, matching relazioni-detection, label finali.
3. Multi-view aggregazione:
   - modificare `src/empower/python/empower/multi_view_aggregator.py`;
   - impatta alias, merge soglie, centroidi globali.
4. Multi-view capture:
   - modificare `src/empower/python/empower/multi_view_capture_node.py`;
   - impatta numero viste, rotazione, output.
5. Parsing piano:
   - modificare `src/empower/python/empower/plan_parser.py`;
   - impatta `planning.json` e quindi l'esecuzione.
6. Esecuzione low-level:
   - modificare `src/empower/python/empower/low_level_execution_node.py`;
   - impatta Nav2, target non visuali, simulazione Gazebo, risoluzione
     target da global_detections.json.
7. Configurazione scenario:
   - modificare `src/empower/config/empower_config.yaml`;
   - impatta task, topic, pose, alias, oggetti simulabili, multi-view config.
8. Orchestrazione missione:
   - modificare `src/empower/python/empower/mission_orchestrator_node.py`;
   - impatta quando viene lanciata EMPOWER, gaze estimation e come interagisce
     con HRI/Nav2.
9. Gaze estimation:
   - modificare `src/gaze_estimation/gaze_estimation/gaze_webcam_node.py` per
     la pipeline gaze;
   - modificare `src/gaze_estimation/gaze_estimation/attention_monitor_node.py`
     per la logica di disattenzione;
   - modificare `src/gaze_estimation/config/*.yaml` per soglie e target.
10. Interazione bambino (HRI):
    - modificare `src/llm_interaction/llm_interaction/voice_llm_node.py`;
    - impatta dialogo, richiamo attenzione, system prompt per attivita'.
