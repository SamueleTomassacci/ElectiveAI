# Stato dell'arte tecnico della repo ElectiveAI

Ultimo aggiornamento del documento: 2026-06-17 (rev 2).

Questo file descrive lo stato attuale del codice nella repo `/home/lorenzo/Scrivania/ElectiveAI` (montata nel container come `/root/exchange`).
L'obiettivo e' fornire contesto tecnico preciso a un altro LLM o a un nuovo sviluppatore.

## Sintesi

La repo e' un workspace ROS 2/colcon orientato a una simulazione TIAGo in Gazebo e contiene due componenti principali, **ora collegate tra loro** tramite un nodo orchestratore:

1. `empower_ros2`: adattamento ROS 2 della pipeline EMPOWER per percezione, grounding, pianificazione simbolica, visualizzazione e esecuzione low-level su TIAGo.
2. `empower_hri`: MVP locale di dialogo vocale/testuale basato su STT -> LLM -> TTS, usabile con microfono e speaker dentro il container.

Le due componenti sono integrate dal `mission_orchestrator_node` (in `empower_ros2`), che coordina la navigazione Nav2, attende un task dall'HRI via `/hri/user_utterance`, lancia la pipeline EMPOWER, porta TIAGo fino al bambino e avvia un loop interattivo di storytelling con il bambino stesso.

L'avvio completo richiede **tre terminali**:
1. Gazebo
2. Nav2 con localizzazione ground truth
3. `hri_empower_mission.launch.py` (HRI + models cacher + mission orchestrator)

## Layout rilevante della repo

```text
.
|-- README.md
|-- README_QWEN_ENGLISH.md
|-- STATO_ARTE_CODICE.md
|-- .gitignore
|-- start_tiago.sh
|-- docker/
|   `-- entrypoint_tiago.sh
|-- dockerfiles/
|   |-- Dockerfile
|   `-- entrypoint_tiago.sh
|-- exchange/
|   |-- person_table_child.world
|   |-- maps/person_table_child_static.yaml
|   |-- models/
|   `-- scan_relay.py
|-- models/
|   `-- piper/it_IT-paola-medium.onnx
|-- empower_data/
|   `-- output/person_table_child_task/
`-- src/
    |-- empower_ros2/
    `-- empower_hri/
```

Sono presenti anche directory generate da colcon (`build/`, `install/`, `log/`). Non vanno considerate sorgente primario.

`empower_data/images/` e `empower_data/output/` sono escluse dal tracking git (output runtime rigenerabili).

## Ambiente di esecuzione previsto

La repo e' pensata per essere montata nel container Docker `hrai_sim` sotto `/root/exchange`.

Script host principale:

```bash
bash start_tiago.sh
```

Lo script:

- usa l'immagine Docker `hrai-2025:v1`;
- monta la repo host in `/root/exchange`;
- abilita X11, host network e privilegi;
- usa GPU NVIDIA se `nvidia-container-toolkit` e' disponibile;
- monta il socket PulseAudio dell'host (`/run/user/<uid>/pulse/native`) in `/run/pulse/native` e imposta `PULSE_SERVER` di conseguenza, per consentire l'accesso al microfono fisico dall'interno del container;
- avvia `/root/exchange/docker/entrypoint_tiago.sh`.

L'entrypoint sorgente ROS 2 Humble, il workspace TIAGo e l'eventuale install locale:

```bash
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
```

Imposta inoltre:

```bash
RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
```

### Immagine Docker `hrai-2025:v1`

Il `Dockerfile` e' in `dockerfiles/Dockerfile`. Contiene:

- ROS 2 Humble + TIAGo public workspace (compilato)
- Dipendenze EMPOWER: YOLO-World, EfficientViT-SAM, spaCy, word2vec, CLIP
- Pesi EMPOWER pre-scaricati: `yolow.pth`, `l2_encoder.onnx`, `l2_decoder.onnx`
- Dipendenze HRI audio/voce: `libportaudio2`, `espeak-ng`, `sounddevice`, `soundfile`, `faster-whisper`, `pyttsx3`

Per ricostruire l'immagine (prima pulire lo spazio Docker con `docker system prune -f`):

```bash
cd ~/Scrivania/ElectiveAI
docker build -f dockerfiles/Dockerfile -t hrai-2025:v1 .
```

### Apertura di terminali aggiuntivi nel container in esecuzione

Per i terminali 2 e 3, con il container gia' avviato:

```bash
docker exec -it hrai_sim bash
# Poi dentro il container:
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
source /root/exchange/install/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
```

## Scena supportata

La scena supportata dalla documentazione attuale e':

```text
exchange/person_table_child.world
exchange/maps/person_table_child_static.yaml
src/empower_ros2/config/empower_person_table_child.yaml
```

La scena mette TIAGo al centro della stanza:

- persona (terapista) sul lato `-Y`;
- bambino sul lato `+Y`;
- tavolo con libro sul lato `+X`;
- TIAGo deve partire all'origine guardando verso `+X`, yaw `0`, per catturare correttamente tavolo e libro con la camera frontale.

Il task di default e':

```yaml
use_case: person_table_child_task
task_description: "Take the book from the table"
```

## Package `empower_ros2`

Percorso:

```text
src/empower_ros2
```

Tipo package:

```text
ament_python
```

### Entry point ROS 2

Definiti in `src/empower_ros2/setup.py`:

```text
scene_capture_node
models_cacher_node
plan_generator_node
export_reports
color_pcl_node
marker_centroids_node
ground_truth_localizer_node
low_level_execution_node
mission_orchestrator_node
```

Il package include anche moduli Python upstream/adattati:

```text
agents.py
detection.py
loader.py
models.py
paths.py
```

e package Python locali:

```text
empower_ros2/
efficientvit/
yolo_world/
```

### Config principale

File:

```text
src/empower_ros2/config/empower_person_table_child.yaml
```

Parametri principali:

```yaml
use_case: person_table_child_task
task_description: "Take the book from the table"
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
standoff_distance: 1.0
continue_on_nav_failure: false
```

### Flusso EMPOWER attuale

La pipeline prevista e' sequenziale:

```text
Gazebo/TIAGo
  -> capture RGB-D
  -> output su file in empower_data
  -> model cacher carica i modelli
  -> plan_generator invia trigger socket al model cacher
  -> detection.py usa OpenAI + YOLO-World + EfficientViT-SAM
  -> vengono prodotti relazioni, descrizione, piano simbolico, detection e maschere
  -> visualizzazione pubblica point cloud e marker
  -> low_level_execution legge il piano e prova a eseguire solo NAVIGATE via Nav2
```

Il collegamento tra `plan_generator_node` e `models_cacher_node` non e' un topic ROS, ma un socket TCP locale:

```text
127.0.0.1:10000
```

### `scene_capture_node`

File:

```text
src/empower_ros2/python/empower_ros2/scene_capture_node.py
```

Ruolo:

- sottoscrive immagine RGB, immagine depth e `CameraInfo`;
- aspetta di avere tutti e tre i messaggi;
- salva uno snapshot della scena;
- genera una point cloud da depth + intrinseci camera;
- termina il nodo se `capture_once=true`.

Topic letti:

```text
/head_front_camera/rgb/image_raw
/head_front_camera/depth/image_raw
/head_front_camera/depth/camera_info
```

Output principali:

```text
empower_data/images/<use_case>/scan.jpg
empower_data/output/<use_case>/depth.npy
empower_data/output/<use_case>/depth_pointcloud.pcd
empower_data/output/<use_case>/camera_info.pkl
empower_data/output/<use_case>/camera_info.json
```

La depth viene convertita in millimetri. Se `depth_unit_scale_to_mm < 0`, il nodo sceglie automaticamente:

- `1000.0` per depth float;
- `1.0` per depth non-float.

### `models_cacher_node`

File:

```text
src/empower_ros2/python/empower_ros2/models_cacher_node.py
```

Ruolo:

- carica i modelli EMPOWER una volta sola;
- crea un `EmpowerRos2Loader`;
- carica `OPENAI_API_KEY` da env o dal file configurato;
- apre un server socket TCP;
- quando riceve un comando che contiene `"detection"`, esegue la pipeline di detection/grounding/planning.

Modelli/dipendenze caricate:

- YOLO-World (`YOLOW`);
- EfficientViT-SAM (`VitSam`);
- spaCy (`en_core_web_sm` di default);
- word2vec (`word2vec-google-news-300` di default);
- OpenAI API, usata da `agents.py`.

I pesi richiesti sono inclusi nell'immagine Docker:

```text
/opt/empower_models/config/yolow/yolow.pth
/opt/empower_models/config/efficientvitsam/l2_encoder.onnx
/opt/empower_models/config/efficientvitsam/l2_decoder.onnx
```

### `plan_generator_node`

File:

```text
src/empower_ros2/python/empower_ros2/plan_generator_node.py
```

Ruolo:

- crea `empower_data/output/<use_case>`;
- rimuove `planning.txt` precedente;
- invia via socket a `models_cacher_node` il payload:

```json
{
  "command": "detection",
  "task_description": "<task_description>"
}
```

- aspetta la creazione di `planning.txt`;
- converte il piano testuale in `planning.json`;
- rigenera report leggibili.

Output:

```text
planning.txt
planning.json
relations.txt
relations.json
scene_description.txt
symbolic_plan.txt
detections.json
empower_report.txt
```

### Codice EMPOWER upstream: `agents.py`, `detection.py`, `models.py`

File:

```text
src/empower_ros2/python/agents.py
src/empower_ros2/python/detection.py
src/empower_ros2/python/models.py
```

Funzionamento attuale:

- `agents.py` usa `openai.OpenAI()` e chiama `gpt-4o`.
- `Agents.multi_agent_vision_planning()` effettua tre passi:
  - environment agent: produce relazioni essenziali in forma di triple;
  - description agent: produce descrizione della scena e istruzioni preliminari;
  - planning agent: produce un piano simbolico usando solo le azioni `NAVIGATE`, `GRAB`, `DROP`, `PULL`, `PUSH`.
- `detection.py`:
  - codifica `scan.jpg` in base64;
  - chiede a OpenAI relazioni/descrizione/piano;
  - estrae classi dalle relazioni con spaCy + word2vec;
  - configura YOLO-World sulle classi estratte;
  - esegue detection sull'immagine;
  - usa EfficientViT-SAM per le maschere;
  - riordina/grounda label e bounding box rispetto alle relazioni;
  - salva detection e artefatti visuali.

Output principali prodotti da `detection.py`:

```text
planning.pkl
planning.txt
detection.pkl
mask.jpg
yolo.jpg
rgb_<n>.jpg
scan_with_bb.jpg
```

`models.py` contiene wrapper per:

- `YOLOW`: MMEngine/MMYOLO/YOLO-World;
- `VitSam`: EfficientViT-SAM encoder/decoder ONNX.

### Parser e report

File:

```text
src/empower_ros2/python/empower_ros2/plan_parser.py
src/empower_ros2/python/empower_ros2/report_writer.py
src/empower_ros2/python/empower_ros2/export_reports.py
```

`plan_parser.py` riconosce azioni:

```text
NAVIGATE
GRAB
DROP
PULL
PUSH
```

Produce step JSON con campi come:

```json
{
  "step": 1,
  "action": "NAVIGATE",
  "description": "NAVIGATE to the table",
  "target": "the table"
}
```

Per `DROP`, prova anche a estrarre:

```text
spatial_relation
reference
```

`report_writer.py` legge `planning.pkl`, `planning.txt` e `detection.pkl` e produce versioni `.txt`/`.json` piu' leggibili.

### Visualizzazione: `color_pcl_node`

File:

```text
src/empower_ros2/python/empower_ros2/color_pcl_node.py
```

Ruolo:

- legge `depth_pointcloud.pcd`, `scan.jpg`, `detection.pkl`, `camera_info.pkl`;
- colora la point cloud in base alle maschere di detection;
- salva `colored_image.png`, `colored_pcl.pcd`, `colors_dict.pkl`;
- pubblica `sensor_msgs/PointCloud2`.

Topic pubblicato:

```text
/empower/colored_pointcloud
```

QoS: transient local, depth 1.

### Visualizzazione: `marker_centroids_node`

File:

```text
src/empower_ros2/python/empower_ros2/marker_centroids_node.py
```

Ruolo:

- aspetta `detection.pkl` e `colors_dict.pkl`;
- calcola centroidi e punti massimi dei cluster colorati;
- pubblica marker RViz con centroidi, nomi e massimi;
- prova a trasformare i marker nel frame `map`; se la TF non e' disponibile, pubblica nel frame camera.

Topic pubblicati:

```text
/pcl_centroids
/pcl_maximum
/pcl_names
```

Tipo:

```text
visualization_msgs/MarkerArray
```

### Nav2 e localizzazione ground truth

Launch:

```text
src/empower_ros2/launch/tiago_nav2_ground_truth.launch.py
```

Avvia:

- `nav2_map_server`;
- lifecycle manager per map server;
- `ground_truth_localizer_node`;
- `nav2_bringup/launch/navigation_launch.py`.

`ground_truth_localizer_node`:

- sottoscrive `/ground_truth_odom`;
- usa TF `odom -> base_footprint`;
- pubblica `map -> odom`;
- sostituisce AMCL usando la ground truth di Gazebo.

Serve a far lavorare Nav2 in simulazione con una localizzazione ideale.

### Esecuzione low-level

File:

```text
src/empower_ros2/python/empower_ros2/low_level_execution_node.py
src/empower_ros2/python/empower_ros2/primitive_actions.py
```

Launch:

```text
src/empower_ros2/launch/empower_execute.launch.py
```

Il launch avvia:

```text
color_pcl_node
marker_centroids_node
low_level_execution_node
```

Capacita' attuale:

- legge il piano da `planning.json` o `planning.txt`;
- aspetta i marker `/pcl_centroids` e `/pcl_names`;
- risolve il target testuale del piano confrontandolo con le label dei marker;
- esegue solo `NAVIGATE` tramite action Nav2 `NavigateToPose`;
- calcola un goal a distanza `standoff_distance` dal centroide, lungo la linea robot-oggetto, orientando TIAGo verso il target;
- salta esplicitamente `GRAB`, `DROP`, `PUSH`, `PULL`.

### `mission_orchestrator_node`

File:

```text
src/empower_ros2/python/empower_ros2/mission_orchestrator_node.py
```

Config:

```text
src/empower_ros2/config/hri_empower_mission.yaml
```

Ruolo: nodo bridge che collega HRI, Nav2 e la pipeline EMPOWER in una missione sequenziale hardcoded.

Stati della missione (`/mission/state`):

```text
GO_TO_THERAPIST  -> naviga verso il terapista (x=0, y=-1.6)
WAIT_TASK        -> aspetta utterance su /hri/user_utterance (timeout 120s)
GO_TO_SPAWN      -> naviga all'origine (x=0, y=0)
RUN_EMPOWER      -> esegue capture -> planning -> execution
GO_TO_CHILD      -> naviga verso il bambino (x=0, y=1.6)
CHILD_READY      -> pubblica "child_ready" su /mission/child_ready
STORYTELLING     -> attiva loop storytelling sull'HRI (timeout 300s)
MISSION_COMPLETE -> missione terminata
```

Topic pubblicati:

```text
/mission/state            std_msgs/String  (QoS transient local)
/mission/feedback         std_msgs/String
/mission/task_description std_msgs/String  (QoS transient local)
/mission/child_ready      std_msgs/String  (QoS transient local)
/hri/dialog_command       std_msgs/String  (JSON)
```

Topic sottoscritti:

```text
/hri/user_utterance     std_msgs/String
/hri/dialog_complete    std_msgs/String  (JSON)
```

Comportamento in `RUN_EMPOWER`:

- se `capture_before_empower: true`, lancia `scene_capture_node` come subprocess e aspetta i file output;
- lancia `empower_plan.launch.py` (subprocess bloccante) con il `task_description` ricevuto dall'HRI;
- se `execute_empower_plan: true`, avvia `color_pcl_node`, `marker_centroids_node`, `low_level_execution_node` (subprocess) in sequenza.

Comportamento in `STORYTELLING`:

- pubblica su `/hri/dialog_command` il JSON `{"command": "start_storytelling", "context": {"task": "<task>"}}`;
- blocca in attesa di un messaggio su `/hri/dialog_complete`;
- timeout configurabile con `storytelling_timeout_sec` (default 300s);
- se il timeout scade, pubblica `{"command": "stop"}` e avanza comunque a MISSION_COMPLETE.

Parametri principali in `hri_empower_mission.yaml`:

```yaml
therapist_pose: [0.0, -1.6, -1.5708]
spawn_pose: [0.0, 0.0, 0.0]
child_pose: [0.0, 1.6, 1.5708]
wait_for_task_timeout_sec: 120.0
auto_start: true
capture_before_empower: true
execute_empower_plan: true
use_case: "person_table_child_task"
output_root: "/root/exchange/empower_data"
storytelling_timeout_sec: 300.0
```

### Launch EMPOWER disponibili

```text
empower_capture.launch.py
  -> scene_capture_node

empower_models_cacher.launch.py
  -> models_cacher_node

empower_plan.launch.py
  -> plan_generator_node

empower_visualization.launch.py
  -> color_pcl_node + marker_centroids_node

empower_execute.launch.py
  -> color_pcl_node + marker_centroids_node + low_level_execution_node

tiago_nav2_ground_truth.launch.py
  -> map_server + lifecycle_manager_map + ground_truth_localizer_node + Nav2 navigation_launch.py

hri_empower_mission.launch.py
  -> models_cacher_node + voice_llm_node + mission_orchestrator_node
```

`hri_empower_mission.launch.py` e' il launch principale per l'avvio integrato. Argomenti disponibili:

```text
mission_params_file    default: hri_empower_mission.yaml
empower_params_file    default: empower_person_table_child.yaml
hri_params_file        default: hri_cpu_english.yaml
start_hri              default: true
start_models_cacher    default: true
hri_input_mode         default: voice
stt_device_index       default: -1  (usa device di default del sistema; specificare l'indice se serve)
use_sim_time           default: true
```

## Package `empower_hri`

Percorso:

```text
src/empower_hri
```

Tipo package:

```text
ament_python
```

Entry point:

```text
voice_llm_node = empower_hri.voice_llm_node:main
```

### Flusso HRI attuale

```text
microfono -> faster-whisper -> Ollama/Qwen -> pyttsx3/espeak-ng/mock -> speaker
```

Il nodo principale e':

```text
src/empower_hri/empower_hri/voice_llm_node.py
```

### `voice_llm_node`

Ruolo:

- coordina input testuale o vocale;
- mantiene una history conversazionale limitata;
- chiama un provider LLM;
- riproduce la risposta con TTS;
- pubblica topic ROS informativi;
- gestisce due modalita' operative distinte: terapista e storytelling con bambino.

Topic pubblicati:

```text
/hri/user_utterance     std_msgs/String
/hri/robot_utterance    std_msgs/String
/hri/dialog_state       std_msgs/String
/hri/dialog_complete    std_msgs/String  (JSON: {"reason": "...", "turns": N})
```

Topic sottoscritti:

```text
/hri/dialog_command     std_msgs/String  (JSON)
```

Stati pubblicati (`/hri/dialog_state`):

```text
ready
waiting_for_text
waiting_for_push_to_talk
recording
transcribing
thinking
speaking
storytelling_active
error
stopped
```

### Modalita' operative

Il nodo opera in due modalita' selezionate dal parametro interno `_mode`:

**`therapist_command`** (default):
- ciclo standard STT/LLM/TTS;
- system prompt orientato al dialogo con il terapista;
- il terapista da' il task all'orchestratore tramite questo canale.

**`child_storytelling`**:
- attivata dal comando `start_storytelling` su `/hri/dialog_command`;
- la history viene svuotata e il contesto del task viene iniettato nel system prompt;
- il robot genera l'apertura della storia autonomamente (messaggio speciale `[START_STORY]`) senza aspettare input del bambino;
- loop interattivo: il bambino risponde via STT, il robot reagisce e continua la storia;
- il prompt storytelling mantiene continuita' narrativa tra i turni grazie alla history;
- al termine (per `max_turns` o comando `stop`) pubblica su `/hri/dialog_complete` e torna in modalita' `therapist_command`.

### System prompt storytelling

Il template in `STORYTELLING_SYSTEM_PROMPT_TEMPLATE` include il task come contesto e istruisce il modello a:
- rispondere in 2-4 frasi brevi e coinvolgenti;
- mantenere la continuita' narrativa tra i turni;
- chiudere ogni risposta con una domanda semplice per il bambino;
- rispondere solo in inglese.

### Parametri aggiuntivi

```yaml
storytelling_max_turns: 10   # numero massimo di turni bambino (0 = illimitato)
```

### Provider LLM

File:

```text
src/empower_hri/empower_hri/ollama_client.py
```

Provider supportati:

```text
ollama
mock
```

Per Ollama:

- usa API HTTP locali;
- endpoint base default: `http://localhost:11434`;
- modello default: `qwen2.5:1.5b`;
- fallback default: `llama3.2:1b`;
- endpoint usati: `/api/tags` e `/api/chat`;
- risposta non-streaming.

Ollama gira sull'**host**, non nel container. Il container lo raggiunge via `--net host`.

### Speech-to-text

File:

```text
src/empower_hri/empower_hri/speech_to_text.py
```

Provider supportati:

```text
faster_whisper
mock
```

`FasterWhisperSpeechToText`:

- e' esplicitamente CPU-only;
- registra audio con `sounddevice`;
- salva temporaneamente un WAV;
- trascrive con `faster_whisper.WhisperModel`;
- usa `vad_filter=True`;
- rimuove il file temporaneo.

Il push-to-talk e' gestito da terminale:

```text
Premi INVIO per registrare (oppure digita /quit):
```

Se l'utente scrive qualcosa invece di premere solo INVIO, quel testo viene usato direttamente senza registrare audio.

Parametro `device_index` (aggiunto di recente):

- `-1` (default): usa il device di input di default del sistema (ALSA/PulseAudio);
- `>= 0`: usa il device con quell'indice (vedere `sounddevice.query_devices()`).

Per listare i device disponibili dentro il container:

```bash
python3 -c "import sounddevice; print(sounddevice.query_devices())"
```

### Text-to-speech

File:

```text
src/empower_hri/empower_hri/text_to_speech.py
```

Provider supportati con fallback automatico:

```text
piper -> pyttsx3 -> system -> mock
```

Provider `system` prova nell'ordine:

```text
espeak-ng
espeak
spd-say
```

E' presente un modello Piper italiano:

```text
models/piper/it_IT-paola-medium.onnx
models/piper/it_IT-paola-medium.onnx.json
```

### Config HRI

File inglese (usato come default da `hri_empower_mission.launch.py`):

```text
src/empower_hri/config/hri_cpu_english.yaml
```

Valori principali:

```yaml
input_mode: "voice"
llm_provider: "ollama"
llm_model: "qwen2.5:1.5b"
llm_fallback_model: "llama3.2:1b"
stt_provider: "faster_whisper"
stt_model: "base"
stt_device: "cpu"
stt_compute_type: "int8"
stt_device_index: -1
language: "en"
record_seconds: 5.0
tts_provider: "system"
```

File locale/italiano:

```text
src/empower_hri/config/hri_cpu_local.yaml
```

Valori principali:

```yaml
input_mode: "text"
llm_provider: "ollama"
llm_model: "qwen2.5:1.5b"
stt_provider: "faster_whisper"
stt_model: "tiny"
stt_device_index: -1
language: "it"
record_seconds: 4.0
tts_provider: "pyttsx3"
```

### Launch HRI disponibili

```text
src/empower_hri/launch/hri_voice_llm_english.launch.py
src/empower_hri/launch/hri_voice_llm_cpu.launch.py
```

Entrambi lanciano `voice_llm_node` con argomenti override.

### Test presenti

```text
src/empower_hri/test/test_ollama_client.py
src/empower_hri/test/test_text_to_speech.py
```

Coprono: mock LLM, fallback modello Ollama, TTS mock, `espeak-ng` nel provider `system`.

Non risultano test automatici per `empower_ros2`.

## Stato del collegamento fra HRI e EMPOWER

Le due componenti sono collegate tramite `mission_orchestrator_node`.

Flusso integrato completo:

```text
hri_empower_mission.launch.py avvia:
  models_cacher_node        (carica modelli, ascolta socket TCP)
  voice_llm_node            (dialogo vocale con terapista e bambino)
  mission_orchestrator_node (macchina a stati che coordina tutto)

mission_orchestrator_node:
  GO_TO_THERAPIST  -> naviga dal terapista
  WAIT_TASK        -> ascolta /hri/user_utterance (task vocale)
  GO_TO_SPAWN      -> torna all'origine
  RUN_EMPOWER      -> capture -> plan (GPT-4o) -> execute (Nav2)
  GO_TO_CHILD      -> naviga dal bambino
  CHILD_READY      -> pubblica su /mission/child_ready
  STORYTELLING     -> pubblica start_storytelling su /hri/dialog_command
                      voice_llm_node switcha in child_storytelling mode:
                        [robot apre storia autonomamente]
                        [loop STT bambino -> LLM -> TTS -> ...]
                      attende /hri/dialog_complete
  MISSION_COMPLETE -> missione terminata
```

Canali di comunicazione HRI <-> orchestratore:

```text
/hri/user_utterance  (HRI -> orchestratore)  task del terapista
/hri/dialog_command  (orchestratore -> HRI)  comandi di modalita'
/hri/dialog_complete (HRI -> orchestratore)  fine sessione storytelling
```

Cosa rimane disaccoppiato:

- `voice_llm_node` non legge feedback dalla pipeline EMPOWER (non sa se la navigazione e' riuscita);
- non esiste un canale di ritorno da EMPOWER verso HRI per comunicare il risultato del piano all'utente via voce;
- il `task_description` passato a EMPOWER e' l'utterance grezza del terapista, non normalizzata.

## Limiti attuali noti

### Manipolazione non implementata

La pipeline produce azioni simboliche `NAVIGATE`, `GRAB`, `DROP`, `PULL`, `PUSH` ma l'esecuzione ROS 2 implementa solo `NAVIGATE`. Le altre azioni sono riconosciute e saltate con log. Non ci sono MoveIt, braccio o gripper.

### EMPOWER dipende da OpenAI

`agents.py` usa `gpt-4o`. Serve una chiave OpenAI valida in env o in:

```text
/root/exchange/openai_api_key.txt
```

Il file e' escluso dal tracking git.

### HRI e' CPU-only

Il nodo HRI valida che `stt_device == cpu`. Non supporta STT GPU.

### Microfono: device index fisso per macchina

Il parametro `stt_device_index` e' `-1` (default di sistema) nei YAML. Se il microfono desiderato non e' il device di default, va specificato al launch:

```bash
ros2 launch empower_ros2 hri_empower_mission.launch.py stt_device_index:=8
```

L'indice cambia da macchina a macchina. Verificarlo con `sounddevice.query_devices()`.

### Feedback vocale dell'avanzamento missione non implementato

Il `mission_orchestrator_node` pubblica su `/mission/feedback` e `/mission/state` ma `voice_llm_node` non li legge. Il terapista non riceve conferma vocale dell'avanzamento (es. "sto andando dal bambino", "ho trovato il libro").

### Task non interpretato

L'utterance del terapista viene passata direttamente come `task_description` a EMPOWER senza normalizzazione. Se l'utterance e' ambigua o non corrisponde a un'azione fisica valida, la pipeline EMPOWER la gestira' comunque come task.

### Storytelling: cambio modalita' non interrompe il turno corrente

Se il comando `start_storytelling` arriva mentre `voice_llm_node` e' in attesa di input vocale (push-to-talk bloccante), il cambio di modalita' viene applicato ma non diventa attivo fino al turno successivo. Il robot entra in storytelling mode al prossimo ciclo dell'interaction loop, non immediatamente.

### Storia: prompt e numero di turni fissi per configurazione

Il system prompt storytelling e' un template con solo il `task` come variabile. Non e' personalizzabile per nome del bambino, eta' o altra informazione contestuale senza modificare il codice o aggiungere parametri.

## Comandi operativi principali

### Build

Build `empower_ros2`:

```bash
cd /root/exchange
colcon build --symlink-install --packages-select empower_ros2
source install/setup.bash
```

Build `empower_hri`:

```bash
cd /root/exchange
colcon build --symlink-install --packages-select empower_hri
source install/setup.bash
```

### Avvio con 3 terminali

**Terminale 1 — Gazebo** (avvia anche il container con `bash start_tiago.sh`):

```bash
ros2 launch tiago_gazebo tiago_gazebo.launch.py \
  is_public_sim:=True \
  gazebo_version:=classic \
  world_name:=/root/exchange/exchange/person_table_child.world
```

**Terminale 2 — Nav2**:

```bash
ros2 launch empower_ros2 tiago_nav2_ground_truth.launch.py \
  map:=/root/exchange/exchange/maps/person_table_child_static.yaml
```

**Terminale 3 — HRI + EMPOWER** (launch integrato):

```bash
ros2 launch empower_ros2 hri_empower_mission.launch.py
```

Con microfono non-default (es. webcam Logitech C920 al device 8):

```bash
ros2 launch empower_ros2 hri_empower_mission.launch.py stt_device_index:=8
```

### Launch EMPOWER singoli (uso standalone)

```bash
# Cattura
ros2 launch empower_ros2 empower_capture.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml

# Model cacher
ros2 launch empower_ros2 empower_models_cacher.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml

# Planning
ros2 launch empower_ros2 empower_plan.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml

# Visualizzazione
ros2 launch empower_ros2 empower_visualization.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml

# Esecuzione low-level
ros2 launch empower_ros2 empower_execute.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
```

### HRI standalone

```bash
# Inglese (voce)
ros2 launch empower_hri hri_voice_llm_english.launch.py

# Italiano (locale, input testo)
ros2 launch empower_hri hri_voice_llm_cpu.launch.py
```

## Contratto mentale per sviluppo futuro

La repo ha ora una base funzionante end-to-end:

- pipeline EMPOWER quasi completa fino a navigazione mobile;
- HRI vocale/testuale funzionante con Ollama/Qwen locale;
- missione integrata: voce -> task -> EMPOWER -> navigazione.

I prossimi passi tecnici suggeriti dal codice attuale:

1. **Feedback vocale avanzamento**: far leggere a `voice_llm_node` i topic `/mission/state` o `/mission/feedback` per comunicare vocalmente al terapista l'avanzamento (es. TTS "sto andando dal bambino").
2. **Normalizzazione del task**: aggiungere logica che trasformi l'utterance grezza del terapista in un `task_description` strutturato prima di passarlo a EMPOWER.
3. **Contesto storytelling ricco**: aggiungere parametri come `child_name`, `child_age` al comando `start_storytelling` e usarli nel prompt per personalizzare la storia.
4. **Manipolazione**: integrare MoveIt per implementare `GRAB`, `DROP`, `PUSH`, `PULL` in `primitive_actions.py`.
5. **LLM locale per EMPOWER**: sostituire o affiancare OpenAI `gpt-4o` in `agents.py` con Ollama, eliminando la dipendenza da rete e API key.
6. **Multi-turno missione**: supportare piu' task consecutivi senza riavviare il launch (tornare a GO_TO_THERAPIST dopo MISSION_COMPLETE).
