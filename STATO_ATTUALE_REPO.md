# Stato attuale della repo ElectiveAI

Questo documento e' pensato per essere passato a un assistente AI come contesto
tecnico della repo. Descrive lo stato attuale del codice, l'architettura dei
package ROS 2, il flusso di esecuzione della simulazione TIAGo in Gazebo e, in
modo particolare, la pipeline EMPOWER, lo scene graph statico, l'interazione
robot-bambino con gaze estimation integrata e il sistema di memoria bambino
basato su RDF.

## Sintesi generale

La repo contiene il codice necessario per eseguire una simulazione del robot
TIAGo in Gazebo Classic, con ROS 2 Humble, Nav2 e una pipeline di task planning
basata su EMPOWER. La scena supportata e' una scena custom:

- world: `exchange/scene.world`
- mappa statica Nav2: `exchange/maps/scene.yaml`
- configurazione EMPOWER: `src/empower/config/empower_config.yaml`
- scene graph statico: `src/empower/config/scenes/therapy_room_scene_graph.json`

La repo e' organizzata intorno a tre componenti principali:

1. `empower`: adapter ROS 2 della pipeline EMPOWER, piu' orchestrazione
   missione, Nav2, capture RGB-D (single-view e multi-view), planning,
   grounding, aggregazione multi-vista, esecuzione low-level e scene graph
   statico per la risoluzione delle pose di navigazione.
2. `llm_interaction`: componente di interazione vocale/testuale. Usa STT,
   LLM locale via Ollama e TTS (Piper) per raccogliere istruzioni dal
   terapista e dialogare con il bambino. Include richiamo attenzione basato
   su gaze, soglia di disengagement secondaria (basata su alert attenzione
   accumulati e ratio emozioni negative), graceful stop da terapista,
   memoria bambino basata su RDF, generazione report sessione e sistema di
   punteggi sessione (session scores) con persistenza JSON.
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

- `multi_view_capture_node`
- `multi_view_aggregator`
- `models_cacher_node`
- `plan_generator_node`
- `ground_truth_localizer_node`
- `low_level_execution_node`
- `mission_orchestrator_node`

NOTA: `child_interaction_test_node` e' ancora elencato in `setup.py` come entry
point, ma il modulo sorgente corrispondente e' stato cancellato (rimane solo
`__pycache__`). Non e' utilizzabile.

Il package contiene due livelli di codice:

- moduli EMPOWER stile upstream in `src/empower/python/*.py`
  (`agents.py`, `detection.py`, `models.py`, `loader.py`, `paths.py`);
- adapter ROS 2 in `src/empower/python/empower/*.py`.

I file `agents.py`, `detection.py`, `models.py`, `loader.py`, `paths.py` sono
presenti anche direttamente in `src/empower/` e risultano identici alle
copie sotto `src/empower/python/`. La configurazione `setup.py` installa
questi file come `py_modules`, mentre il package ROS 2 vero e proprio sta in
`python/empower`.

### Launch files del package empower

I launch file attualmente presenti sono solo tre:

- `empower_plan.launch.py`
- `hri_empower_mission.launch.py`
- `tiago_nav2_ground_truth.launch.py`

I vecchi launch file (`empower_capture.launch.py`, `empower_models_cacher.launch.py`,
`empower_multiview_capture.launch.py`, `empower_visualization.launch.py`,
`empower_execute.launch.py`) sono stati rimossi.

NOTA: NON esiste un launch file `child_interaction_test.launch.py`.

## Static Scene Graph

Il sistema usa ora uno scene graph statico caricato da JSON al posto delle pose
hardcoded nei file YAML. Questo e' il cambiamento architetturale piu' importante
rispetto alle versioni precedenti.

### Modulo e file

- Modulo Python: `src/empower/python/empower/static_scene_graph.py`
  (classe `StaticSceneGraph`)
- File JSON: `src/empower/config/scenes/therapy_room_scene_graph.json`

### Struttura dello scene graph

Lo scene graph definisce nodi e archi semantici:

Nodi (ognuno con tipo, pose opzionale, alias, ruoli):

- `room_therapy`: type=room
- `zone_center`: type=zone, pose=[4.8, 0.0, 1.72], roles=[capture_origin,
  mission_reset], aliases=[center, spawn, ...]
- `therapist`: type=person, aliases=[therapist, person, adult, ...]
- `therapist_interaction_pose`: type=nav_pose, target=therapist,
  pose=[0.0, -1.3, -1.5708], roles=[receive_instruction, ask_activity]
- `child`: type=person, aliases=[child, kid, ...]
- `child_delivery_pose`: type=nav_pose, target=child,
  pose=[0.0, 0.95, 1.5708], roles=[deliver_object, start_child_interaction],
  via=[child_approach_waypoint]
- `child_approach_waypoint`: type=waypoint, pose=[3.0, 0.0, 3.14159]
- `child_delivery_table`: type=surface,
  drop_pose=[0.0, 1.65, 0.65, 1.5708], roles=[child_delivery_drop]

Archi (relazioni semantiche): `located_in`, `approaches`, `reachable_via`,
`used_for`.

### Utilizzo nello scene graph

L'orchestratore missione chiama `self.static_scene_graph.resolve_nav_pose(target,
role=role)` per ottenere le pose di navigazione, al posto delle vecchie chiavi
YAML `therapist_pose`, `spawn_pose`, `child_pose` (che non esistono piu' in
`task_config.yaml`).

`low_level_execution_node` usa lo scene graph per:

- Risoluzione alias child/person
- Risoluzione nav pose per target non visuali
- Risoluzione drop pose (es. `child_delivery_table`)
- Risoluzione waypoint intermedi (es. `child_approach_waypoint` prima di
  arrivare al bambino)

### Configurazione

In `task_config.yaml`:

```yaml
static_scene_graph_path: "/root/exchange/src/empower/config/scenes/therapy_room_scene_graph.json"
```

In `empower_config.yaml`:

```yaml
static_scene_graph_path: "/root/exchange/src/empower/config/scenes/therapy_room_scene_graph.json"
```

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
depth_unit_scale_to_mm: ...
static_scene_graph_path: "/root/exchange/src/empower/config/scenes/therapy_room_scene_graph.json"
```

Campi rimossi rispetto alle versioni precedenti:

- `child_navigation` (intera sezione rimossa)
- `person_navigation` (intera sezione rimossa)
- `object_simulation.child_delivery_table_drop_*` (le drop pose vengono dallo
  scene graph)

Campi aggiunti:

- `static_scene_graph_path`
- `depth_unit_scale_to_mm`
- `multi_view.merge.min_detection_confidence`
- `object_simulation.world_sdf_path`
- `object_simulation.gz_cli_timeout_sec`
- `object_simulation.world_frame: world`
- `object_simulation.robot_base_frame: base_footprint`

### Multi-view EMPOWER

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
    min_detection_confidence: ...
```

Quando `multi_view.enabled` e' true, l'orchestratore usa `multi_view_capture_node`
al posto del vecchio `scene_capture_node` (che non esiste piu' come entry point).
Il nodo ruota il robot sul posto (via Nav2) catturando una serie di viste RGB-D
a 360 gradi con sovrapposizione configurabile.

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

### Navigazione verso target non visuali (via scene graph)

Le vecchie sezioni `child_navigation` e `person_navigation` con pose hardcoded
sono state rimosse dalla YAML. Ora `low_level_execution_node` usa lo scene graph
statico per risolvere le pose di navigazione verso target non visuali (child,
therapist, ecc.). Il nodo intercetta `NAVIGATE to child` e `NAVIGATE to person`
e genera goal Nav2 dalle pose definite nello scene graph, inclusi eventuali
waypoint intermedi (es. `child_approach_waypoint` prima di raggiungere il
bambino).

### Simulazione oggetti

La manipolazione non e' reale. La YAML abilita pero' una simulazione via Gazebo:

```yaml
object_simulation:
  enabled: true
  drop_distance: 0.75
  world_frame: world
  robot_base_frame: base_footprint
  world_sdf_path: ...
  gz_cli_timeout_sec: ...
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

Le drop pose per il tavolo di consegna bambino (`child_delivery_table`)
vengono ora dallo scene graph (campo `drop_pose` del nodo
`child_delivery_table`: `[0.0, 1.65, 0.65, 1.5708]`) e non piu' da
campi `child_delivery_table_drop_*` nella YAML.

Quando `low_level_execution_node` esegue:

- `GRAB peluche`: sposta il modello Gazebo del peluche nella posizione nascosta
  (`hidden_z = -10.0`);
- `DROP peluche`: riposiziona il modello. Se l'ultima navigazione era verso il
  bambino, usa la drop pose dal nodo `child_delivery_table` dello scene graph.

## Pipeline EMPOWER multi-view

### Multi-view capture (`multi_view_capture_node`)

File: `src/empower/python/empower/multi_view_capture_node.py`

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

### Esecuzione low-level con global_detections.json e scene graph

Quando `multi_view.enabled` e' true, `low_level_execution_node` carica
`global_detections.json` e lo usa per risolvere i target NAVIGATE:

- Per ogni step NAVIGATE, cerca il target tra gli oggetti globali usando le
  label normalizzate e gli alias;
- Costruisce un goal Nav2 usando il centroide dell'oggetto nel frame `map` con
  `standoff_distance`;
- Per target non visuali (child, person), usa lo scene graph per risolvere
  la nav pose, inclusi waypoint intermedi.

### Model cacher con supporto multi-view

`models_cacher_node` riceve i parametri `multi_view.enabled` e
`multi_view.merge.*`, e li passa al `EmpowerLoader`. Il loader supporta
la pipeline di detection per-vista e l'aggregazione successiva. Anche gli
alias degli oggetti (`object_simulation.objects.*.aliases`) vengono passati
al loader per normalizzare le label in fase di aggregazione.

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

Contenuto corrente di `task_config.yaml`:

```yaml
mission_orchestrator_node:
  ros__parameters:
    map_frame: "map"
    static_scene_graph_path: "/root/exchange/src/empower/config/scenes/therapy_room_scene_graph.json"
    session_scores_path: "/root/exchange/output/interaction_output/session_scores.json"
    nav_action_name: "navigate_to_pose"
    params_file: "/root/exchange/src/empower/config/empower_config.yaml"
    wait_for_task_timeout_sec: 300.0
    wait_for_activity_timeout_sec: 300.0
    wait_for_child_name_timeout_sec: 300.0
    proposal_acceptance_timeout_sec: 15.0
    task_ack_text: "Okay, I understood the task."
    activity_ack_text: "Okay, I understood the activity. What is the child's name?"
    child_name_ack_text: "Okay, I will personalize the activity for {child_name}."
    child_name_ask_text: "What is the child's name?"
    no_history_text: "I don't have session history for this child. Please tell me the task."
    proposal_text: "Based on past sessions, I suggest {activity}. Say something to override, or wait to accept."
    default_activity_type: "generic_child_activity"
    auto_start: true
    action_server_timeout_sec: 20.0
    nav_result_timeout_sec: 300.0
    empower_process_timeout_sec: 0.0
    capture_timeout_sec: 300.0
    capture_before_empower: true
    execute_empower_plan: true
    child_delivery_handled_by_empower: true
    use_case: "therapy_task"
    output_root: "/root/exchange/output"
    storytelling_timeout_sec: 300.0
    child_activity_timeout_sec: 300.0
    gaze_estimation:
      enabled: true
      camera_index: 0
      device: "auto"
      show_window: true
      startup_timeout_sec: 120.0
```

NOTA: le vecchie chiavi `therapist_pose`, `spawn_pose`, `child_pose` NON
esistono piu'. Le pose vengono ora dallo scene graph.

Parametri nuovi rispetto alla versione precedente:

- `session_scores_path`: path del file JSON dei punteggi sessione.
- `proposal_acceptance_timeout_sec`: timeout (in secondi) per l'accettazione
  automatica della proposta di attivita'.
- `child_name_ask_text`: testo pronunciato per chiedere il nome del bambino.
- `no_history_text`: testo pronunciato se non c'e' storico per il bambino.
- `proposal_text`: template per la proposta di attivita' (con `{activity}`).

### Stati missione

Il flusso e' ora "child-first": il nome del bambino e' il PRIMO input
richiesto. Se esiste uno storico di punteggi sessione per quel bambino,
il robot propone automaticamente l'attivita' con il miglior punteggio medio;
altrimenti chiede task e attivita' manualmente alla terapista.

```text
GO_TO_THERAPIST
WAIT_CHILD_NAME
ACK_CHILD_NAME
PROPOSE_ACTIVITY
WAIT_THERAPIST_OVERRIDE
ACK_PROPOSAL
GO_TO_SPAWN
RUN_EMPOWER
GO_TO_CHILD
CHILD_READY
CHILD_ACTIVITY
GO_BACK_TO_THERAPIST
MISSION_COMPLETE
MISSION_FAILED
```

Se non c'e' storico per il bambino, dopo ACK_CHILD_NAME il flusso passa
per gli stati WAIT_TASK -> ACK_TASK -> WAIT_ACTIVITY -> ACK_ACTIVITY prima
di GO_TO_SPAWN (fallback al flusso manuale).

Il flusso completo dell'orchestratore (caso con storico):

1. Naviga dal terapista (pose risolta dallo scene graph via ruolo
   `receive_instruction`) (`GO_TO_THERAPIST`);
2. Aspetta il nome del bambino su `/hri/user_utterance`
   (`WAIT_CHILD_NAME`);
3. `ACK_CHILD_NAME`: conferma il nome ricevuto;
4. `PROPOSE_ACTIVITY`: chiama `best_activity_for_child()` dal modulo
   `session_scores` per trovare l'attivita' con il miglior punteggio medio
   storico. Se trova uno storico, propone l'attivita' alla terapista
   (es. "I suggest storytelling based on past sessions");
5. `WAIT_THERAPIST_OVERRIDE`: attende un timeout
   (`proposal_acceptance_timeout_sec`). Se la terapista parla prima del
   timeout, la proposta viene sovrascritta con l'input manuale; se il
   timeout scade senza input, la proposta viene accettata
   automaticamente;
6. `ACK_PROPOSAL`: conferma l'attivita' scelta;
7. Torna allo spawn (pose risolta dallo scene graph via ruolo
   `mission_reset`) (`GO_TO_SPAWN`);
8. Esegue capture EMPOWER (multi-view se abilitato) (`RUN_EMPOWER`);
9. Lancia planning EMPOWER;
10. Lancia esecuzione EMPOWER;
11. Se `child_delivery_handled_by_empower` e' true, NON fa una seconda
    navigazione hardcoded al bambino (stato `GO_TO_CHILD` saltato);
12. Pubblica `/mission/child_ready`;
13. Lancia la gaze estimation come sottoprocesso (se abilitata);
14. Avvia l'attivita' bambino via HRI (stato `CHILD_ACTIVITY`), passando
    `child_name` nel contesto;
15. Al termine dell'attivita' ferma la gaze estimation;
16. `GO_BACK_TO_THERAPIST`: il robot torna dalla terapista e pronuncia un
    riepilogo dell'attivita' con i punteggi sessione;
17. Transiziona a `MISSION_COMPLETE`.

Se la gaze estimation non riesce ad avviarsi (timeout, errore modelli, webcam
non disponibile), la missione transiziona a `MISSION_FAILED`.

### Mapping attivita' -> task EMPOWER

Il modulo `session_scores` definisce `ACTIVITY_TO_TASK`:

```text
storytelling           -> book
mood_check_teddy       -> teddy bear
computer_emotion_recognition -> computer
```

Questo mapping viene usato dall'orchestratore per associare automaticamente
il task fisico EMPOWER quando il robot propone un'attivita' basata sullo
storico.

### MissionInteractionSpec

Il dataclass che passa informazioni tra orchestratore e HRI:

```python
@dataclass
class MissionInteractionSpec:
    task_description: str = ""
    activity_description: str = ""
    activity_type: str = "generic_child_activity"
    child_name: str = ""
```

Il campo `child_name` e' stato aggiunto per supportare la personalizzazione
dell'attivita' in base al nome del bambino.

### Sistema di punteggi sessione (session_scores)

Il modulo `session_scores.py` calcola 4 punteggi (0-100) alla fine di ogni
attivita' bambino:

- **attention**: basato sugli alert di attenzione accumulati durante la
  sessione (`_session_attention_alerts`).
- **emotion**: basato sui campioni emozionali raccolti durante la sessione
  (`_session_emotion_samples`).
- **participation**: basato sulla partecipazione attiva del bambino.
- **overall**: media pesata dei tre punteggi precedenti.

I punteggi vengono salvati in:

```text
output/interaction_output/session_scores.json
```

Il file JSON e' organizzato per bambino e per sessione. La funzione
`best_activity_for_child(child_name)` interroga lo storico e restituisce
l'attivita' con il miglior punteggio overall medio. Il mapping
`ACTIVITY_TO_TASK` converte il tipo di attivita' nel task fisico EMPOWER
corrispondente (es. `storytelling` -> `book`).

Nel `voice_llm_node`, a fine sessione vengono chiamati
`compute_session_scores()` e `save_session_score()`. Il payload di
`/hri/dialog_complete` ora include un campo `scores` con i punteggi.
Il report Markdown include una sezione "Session scores".

## Interazione robot-bambino e gaze estimation (dettaglio)

Questa sezione descrive in dettaglio il flusso dell'interazione con il bambino.

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

#### Regole di stile per risposte al bambino

Il `voice_llm_node` applica regole di stile esplicite per le risposte
child-facing tramite la costante `_CHILD_ACTIVITY_STYLE_RULES`:

```text
"Use exactly two short sentences for each child-facing response.
 Sentence 1: contribute one small new piece of the activity.
 Sentence 2: ask one simple question or invitation for the child.
 ..."
```

### Flusso completo interazione bambino

```text
1. Orchestratore -> stato CHILD_READY
2. Orchestratore -> lancia gaze_with_attention.launch.py (sottoprocesso)
3. gaze_webcam_node si inizializza (carica Gazelle, YOLO-World, apre webcam)
4. gaze_webcam_node pubblica {"state": "READY"} su /gaze/node_status
5. Orchestratore riceve READY -> stato CHILD_ACTIVITY
6. Orchestratore -> pubblica start_child_activity su /hri/dialog_command
   (con child_name nel contesto)
7. attention_monitor_node riceve start_child_activity, configura target validi
8. voice_llm_node inizia l'attivita' (es. storytelling), con memoria bambino
   iniettata nel system prompt se disponibile
9. [Loop turni dialogo]:
   a. voice_llm_node genera risposta LLM e la pronuncia con TTS
   b. voice_llm_node pubblica "speaking" su /hri/dialog_state
   c. attention_monitor_node si arma
   d. gaze_webcam_node pubblica continuamente lo stato gaze su /gaze/status
   e. attention_monitor_node conta tempo disattenzione
   f. Se disattenzione > 2.5s -> pubblica alert su /hri/attention_alert
   g. voice_llm_node finisce TTS
   h. voice_llm_node controlla alert pendente (richiamo attenzione breve)
   i. Se alert: genera/pronuncia frase richiamo (non in history)
   j. voice_llm_node incrementa _session_attention_alerts e
      _disengagement_alert_count
   k. voice_llm_node chiama check_disengagement():
      - Se alert count >= disengagement_alert_threshold (default 4)
        OPPURE negative emotion ratio >= disengagement_negative_emotion_ratio
        (default 0.6) su finestra disengagement_check_window_sec (default 120s)
      - Chiede al bambino "Would you prefer to stop?" via LLM (con fallback)
      - Se il bambino vuole fermarsi: reason = "child_requested_stop"
      - Se il bambino vuole continuare: reset contatori, cooldown
        disengagement_cooldown_sec (default 180s)
      - Max richieste per sessione: disengagement_max_asks (default 2)
   l. voice_llm_node pubblica "listening" su /hri/dialog_state
   m. attention_monitor_node si disarma
   n. voice_llm_node ascolta risposta bambino (STT)
   o. Ogni utterance viene registrata nel _session_transcript
   p. emotion_status_callback appende a _session_emotion_samples
   q. Se graceful stop attivato: il robot pronuncia un saluto (LLM con
      fallback), poi termina. Reason = "graceful_stop_by_therapist"
10. Fine attivita':
    a. voice_llm_node calcola punteggi sessione (compute_session_scores)
    b. voice_llm_node salva punteggi (save_session_score)
    c. voice_llm_node genera report Markdown (con sezione "Session scores")
    d. voice_llm_node pubblica su /hri/dialog_complete
       (payload include child_name, memory_report_path e scores)
11. Orchestratore -> ferma gaze estimation
12. Orchestratore -> stato GO_BACK_TO_THERAPIST: naviga verso la terapista
    e pronuncia un riepilogo dell'attivita' con i punteggi
13. Orchestratore -> stato MISSION_COMPLETE
```

### Graceful stop

La terapista puo' fermare l'attivita' bambino in modo controllato:

- In modalita' voice: preme il tasto `q` (configurabile via
  `graceful_stop_key`). Un thread monitor tastiera usa `select.select`
  su stdin per rilevare la pressione.
- In modalita' text: digita `q` come input.
- Via topic: pubblica `{"command": "graceful_stop"}` su
  `/hri/dialog_command`.

Dopo lo scambio corrente, il robot pronuncia un saluto (generato dall'LLM
con fallback hardcoded), poi termina la sessione. I punteggi e il report
vengono generati prima della chiusura. La reason nel payload di
`/hri/dialog_complete` e' `"graceful_stop_by_therapist"`.

### Topic ROS 2 per gaze/interazione

| Topic | Tipo | Publisher | Formato |
|-------|------|-----------|---------|
| `/gaze/status` | `std_msgs/String` | `gaze_webcam_node` | `{"status": "...", "target_class": ... \| null, "timestamp": float}` |
| `/gaze/node_status` | `std_msgs/String` | `gaze_webcam_node` | `{"state": "READY" \| "ERROR", "message": "..."}` |
| `/hri/attention_alert` | `std_msgs/String` | `attention_monitor_node` | `{"reason": "...", "elapsed_sec": float, "activity_type": "...", "expected_target": "..."}` |
| `/mission/gaze_command` | `std_msgs/String` | (esterno/script) | `{"command": "stop_gaze"}` |
| `/hri/dialog_state` | `std_msgs/String` | `voice_llm_node` | `"speaking"`, `"listening"`, ecc. |
| `/hri/dialog_command` | `std_msgs/String` | `mission_orchestrator_node` | JSON con command |
| `/hri/dialog_complete` | `std_msgs/String` | `voice_llm_node` | JSON con child_name, memory_report_path, scores |

## Sistema di memoria bambino (RDF + SPARQL)

### Modulo

File: `src/llm_interaction/llm_interaction/rdf_child_memory.py`

### Architettura

Il sistema usa `rdflib` con file in formato Turtle (.ttl) e query SPARQL per
recuperare e gestire la memoria dei bambini. Definisce un'ontologia custom con
namespace HRI e predicati per:

- Preferenze sulle storie
- Preferenze sulle emozioni
- Emozioni riconosciute
- Stimoli preferiti
- Tipi di attivita'

### Seed iniziale

Se il file `.ttl` non esiste, il sistema crea automaticamente due bambini
di esempio (Luca e Sofia) con dati di seed, purche' `memory_seed_if_missing`
sia true.

### Iniezione nel prompt

La memoria del bambino viene iniettata nel system prompt dell'attivita'
bambino come sezione "Child personalization memory". Il numero massimo di
fatti iniettati e' controllato da `memory_max_prompt_facts` (default 12).

### Report sessione

Al termine di ogni attivita' bambino, viene generato un report in Markdown
nella directory `output/interaction_output/reports/`. Il report include:

- Metadati sessione (data, bambino, tipo attivita')
- Memoria utilizzata durante la sessione
- Riassunto sessione (generato dall'LLM se possibile)
- Transcript compatto della conversazione
- Suggerimenti di aggiornamento memoria (candidati, NON scritti nel RDF)

NOTA: `memory_update_enabled` e' false nello stato corrente. Non vengono
effettuati aggiornamenti automatici al file RDF. I suggerimenti nei report
sono solo proposte da valutare manualmente o da implementare in futuro.

### Configurazione memoria

Parametri in `llm_config.yaml`:

```yaml
memory_enabled: true
memory_rdf_path: "/root/exchange/output/interaction_output/children_memory.ttl"
memory_report_dir: "/root/exchange/output/interaction_output/reports"
memory_max_prompt_facts: 12
memory_seed_if_missing: true
memory_update_enabled: false
```

## Package `llm_interaction`

`llm_interaction` implementa il dialogo locale. Console script:

```text
voice_llm_node = llm_interaction.voice_llm_node:main
```

Moduli interni:

```text
ollama_client.py      – client HTTP per Ollama
rdf_child_memory.py   – memoria bambino RDF/SPARQL
session_scores.py     – calcolo e persistenza punteggi sessione (NUOVO)
speech_to_text.py     – STT via faster-whisper
terminal_io.py        – input da terminale
text_to_speech.py     – TTS multi-provider
voice_llm_node.py     – nodo ROS 2 principale
```

Launch files: NESSUNO. La directory launch e' vuota. Il nodo viene avviato
dal launch integrato `hri_empower_mission.launch.py` del package `empower`.

Test: NESSUNO. La directory test e' vuota (i vecchi test `test_ollama_client.py`
e `test_text_to_speech.py` sono stati rimossi).

Config principali:

- `src/llm_interaction/config/llm_config.yaml`
- `src/llm_interaction/config/hri_cpu_local.yaml`

Valori correnti in `llm_config.yaml` (quelli che differiscono dalle versioni
precedenti):

```yaml
llm_model: "gemma3:12b"          # era qwen2.5:1.5b
llm_timeout: 600.0                # era 60.0
max_response_tokens: 0             # era 120 (0 = nessun limite)
tts_provider: "piper"              # era "system"
tts_timeout: 60.0                  # era 30.0
piper_model_path: "/root/exchange/models/piper/en_GB-jenny_dioco-medium.onnx"
piper_config_path: "/root/exchange/models/piper/en_GB-jenny_dioco-medium.onnx.json"
```

Pipeline inglese tipica:

```text
microfono -> faster-whisper base -> Ollama/Gemma3 -> Piper TTS -> speaker
```

Pipeline locale/testuale:

```text
terminal input -> Ollama/Gemma3 -> Piper TTS
```

Il nodo pubblica:

- `/hri/user_utterance`
- `/hri/robot_utterance`
- `/hri/dialog_state`
- `/hri/dialog_complete` (con `child_name`, `memory_report_path` e `scores`
  nel payload)

Il nodo sottoscrive:

- `/hri/dialog_command`
- `/mission/state`
- `/hri/attention_alert`
- `/child/emotion_status_smoothed`

Quando `follow_mission_state_for_therapist_input` e' true, l'HRI legge input
dal terapista solo negli stati missione `WAIT_CHILD_NAME`, `WAIT_TASK`,
`WAIT_ACTIVITY` e `WAIT_THERAPIST_OVERRIDE`. Questo evita che input vocali
casuali entrino nella missione fuori fase.

Per il bambino, il nodo supporta tipi di attivita':

- `computer_emotion_recognition`
- `mood_check_teddy`
- `storytelling`
- `generic_child_activity`

La normalizzazione dell'attivita' viene fatta nell'orchestratore in base a
keyword nella frase del terapista.

Il nodo mantiene un `_session_transcript` (lista) che traccia tutte le
utterance durante l'attivita' bambino, usato per la generazione del report
sessione a fine attivita'.

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
- Il bambino e' gestito come target non visuale nello scene graph, non tramite
  perception.
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
- `child_interaction_test_node` e' ancora in `setup.py` ma il modulo sorgente
  e' stato cancellato: l'entry point non funziona.
- La memoria bambino e' in sola lettura (`memory_update_enabled: false`):
  i suggerimenti di aggiornamento nei report non vengono scritti nel RDF
  automaticamente.
- Il graceful stop in modalita' voice dipende dalla lettura di stdin via
  `select.select`, che potrebbe non funzionare in tutti i terminali o
  ambienti containerizzati.
- La soglia di disengagement secondaria si basa su contatori che vengono
  resettati se il bambino sceglie di continuare; non c'e' un meccanismo
  di persistenza cross-sessione per questi dati.
- I punteggi sessione sono salvati in un file JSON locale: non c'e' un
  database o un servizio di aggregazione centralizzato.
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
     target da global_detections.json, risoluzione pose dallo scene graph.
7. Scene graph statico:
   - modificare `src/empower/config/scenes/therapy_room_scene_graph.json` per
     cambiare pose, alias, ruoli, waypoint, relazioni semantiche;
   - modificare `src/empower/python/empower/static_scene_graph.py` per
     cambiare la logica di risoluzione.
8. Configurazione scenario:
   - modificare `src/empower/config/empower_config.yaml`;
   - impatta task, topic, alias, oggetti simulabili, multi-view config.
   - NON contiene piu' pose di navigazione (sono nello scene graph).
9. Orchestrazione missione:
   - modificare `src/empower/python/empower/mission_orchestrator_node.py`;
   - impatta stati missione, flusso terapista/bambino, quando viene lanciata
     EMPOWER, gaze estimation e come interagisce con HRI/Nav2.
   - modificare `src/empower/config/task_config.yaml` per timeout, testi ack,
     parametri gaze.
10. Gaze estimation:
    - modificare `src/gaze_estimation/gaze_estimation/gaze_webcam_node.py` per
      la pipeline gaze;
    - modificare `src/gaze_estimation/gaze_estimation/attention_monitor_node.py`
      per la logica di disattenzione;
    - modificare `src/gaze_estimation/config/*.yaml` per soglie e target.
11. Interazione bambino (HRI):
    - modificare `src/llm_interaction/llm_interaction/voice_llm_node.py`;
    - impatta dialogo, richiamo attenzione, system prompt per attivita',
      regole di stile per risposte child-facing, session transcript.
12. Memoria bambino:
    - modificare `src/llm_interaction/llm_interaction/rdf_child_memory.py`;
    - impatta ontologia RDF, query SPARQL, iniezione memoria nel prompt,
      generazione report sessione, seed bambini di esempio.
    - modificare `src/llm_interaction/config/llm_config.yaml` per parametri
      memoria (path, max facts, seed, update enabled).
13. Report sessione e personalizzazione futura:
    - I report in `output/interaction_output/reports/` contengono suggerimenti
      di aggiornamento memoria che possono guidare l'implementazione di
      aggiornamenti automatici al RDF.
    - Per abilitare aggiornamenti automatici, impostare
      `memory_update_enabled: true` e implementare la logica di scrittura
      nel modulo `rdf_child_memory.py`.
14. Punteggi sessione:
    - modificare `src/llm_interaction/llm_interaction/session_scores.py`;
    - impatta calcolo punteggi (attention, emotion, participation, overall),
      persistenza in `session_scores.json`, query storico attivita' per
      bambino e mapping ACTIVITY_TO_TASK.
15. Disengagement secondario e graceful stop:
    - modificare `src/llm_interaction/llm_interaction/voice_llm_node.py`;
    - impatta soglie di disengagement (`disengagement_alert_threshold`,
      `disengagement_negative_emotion_ratio`, `disengagement_check_window_sec`,
      `disengagement_cooldown_sec`, `disengagement_max_asks`),
      monitor tastiera per graceful stop (`graceful_stop_key`),
      generazione farewell e reason di terminazione.
