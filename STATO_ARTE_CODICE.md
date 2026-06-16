# Stato dell'arte tecnico della repo ElectiveAI

Ultimo aggiornamento del documento: 2026-06-16.

Questo file descrive lo stato attuale del codice nella repo `/home/samuele/ElectiveAI`.
L'obiettivo e' fornire contesto tecnico preciso a un altro LLM o a un nuovo sviluppatore.

## Sintesi

La repo e' un workspace ROS 2/colcon orientato a una simulazione TIAGo in Gazebo e contiene due componenti principali, attualmente separate:

1. `empower_ros2`: adattamento ROS 2 della pipeline EMPOWER per percezione, grounding, pianificazione simbolica, visualizzazione e una prima esecuzione low-level su TIAGo.
2. `empower_hri`: MVP locale di dialogo vocale/testuale basato su STT -> LLM -> TTS, usabile con microfono e speaker dentro il container.

Le due componenti non sono ancora collegate tra loro. Il nodo HRI puo' conversare e pubblica topic ROS con utterance/stato, ma non invia comandi alla pipeline EMPOWER, non modifica `task_description`, non lancia planning e non attiva l'esecuzione robotica.

## Layout rilevante della repo

```text
.
|-- README.md
|-- README_QWEN_ENGLISH.md
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
|   |-- images/person_table_child_task/
|   `-- output/person_table_child_task/
`-- src/
    |-- empower_ros2/
    `-- empower_hri/
```

Sono presenti anche directory generate da colcon (`build/`, `install/`, `log/`). Non vanno considerate sorgente primario.

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

## Scena supportata

La scena supportata dalla documentazione attuale e':

```text
exchange/person_table_child.world
exchange/maps/person_table_child_static.yaml
src/empower_ros2/config/empower_person_table_child.yaml
```

La scena mette TIAGo al centro della stanza. La descrizione operativa nel README indica:

- persona sul lato `-Y`;
- bambino sul lato `+Y`;
- tavolo con libro sul lato `+X`;
- TIAGo deve partire all'origine guardando verso `+X`, quindi yaw `0`, per catturare correttamente tavolo e libro con la camera frontale.

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

Descrizione nel `package.xml`:

```text
ROS 2 Humble adapters for the EMPOWER planning pipeline on TIAGo/Gazebo.
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

Nota importante: nel sorgente attuale sono presenti la config YOLO-World e la directory `efficientvitsam`, ma non risultano versionati i pesi richiesti:

```text
src/empower_ros2/config/yolow/yolow.pth
src/empower_ros2/config/efficientvitsam/l2_encoder.onnx
src/empower_ros2/config/efficientvitsam/l2_decoder.onnx
```

`EmpowerRos2Loader` fallisce con `FileNotFoundError` se questi file non sono disponibili nel path configurato o passato via parametro.

### `EmpowerRos2Loader`

File:

```text
src/empower_ros2/python/empower_ros2/loader_adapter.py
```

Ruolo:

- replica gli attributi attesi dal codice upstream EMPOWER (`CONFIG`, `IMAGES`, `OUTPUT`, `YOLOW_PATH`, `ENCODER_PATH`, `DECODER_PATH`, `SCAN_DIR`, `DUMP_DIR`);
- crea directory immagini/output per il `use_case`;
- controlla l'esistenza dei file modello;
- istanzia YOLO-World, EfficientViT-SAM, spaCy e word2vec.

Serve da ponte tra codice ROS 2 e il codice EMPOWER originale in `detection.py`.

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

Azioni non implementate:

```text
GRAB
DROP
PUSH
PULL
```

Quindi manipolazione, MoveIt, controllo braccio e gripper non sono ancora integrati.

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
```

### Dati generati attualmente

La repo contiene gia' output per:

```text
empower_data/images/person_table_child_task/
empower_data/output/person_table_child_task/
```

Esempi:

```text
scan.jpg
depth.npy
depth_pointcloud.pcd
camera_info.json
detection.pkl
detections.json
relations.txt
relations.json
scene_description.txt
planning.txt
planning.json
planning.pkl
symbolic_plan.txt
empower_report.txt
colored_image.png
colored_pcl.pcd
colors_dict.pkl
scan_with_bb.jpg
```

Questi file sono artefatti runtime della pipeline e possono essere rigenerati rilanciando capture/planning/visualization.

## Package `empower_hri`

Percorso:

```text
src/empower_hri
```

Tipo package:

```text
ament_python
```

Descrizione nel `package.xml`:

```text
CPU-only local voice dialogue MVP for TIAGo and EMPOWER.
```

Entry point:

```text
voice_llm_node = empower_hri.voice_llm_node:main
```

### Flusso HRI attuale

Flusso inglese documentato:

```text
microfono -> faster-whisper base -> Ollama/Qwen -> espeak-ng -> speaker
```

Flusso italiano/config locale:

```text
text oppure microfono -> faster-whisper tiny -> Ollama/Qwen -> pyttsx3/system/mock -> speaker/terminal
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
- pubblica topic ROS informativi.

Topic pubblicati:

```text
/hri/user_utterance
/hri/robot_utterance
/hri/dialog_state
```

Tipo:

```text
std_msgs/String
```

Stati pubblicati tipici:

```text
ready
waiting_for_text
waiting_for_push_to_talk
recording
transcribing
thinking
speaking
error
stopped
```

Non ci sono subscriber nel nodo HRI. Non riceve comandi ROS da altri nodi e non controlla robot o EMPOWER.

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

Il client verifica se il modello configurato e' disponibile. Se non lo e', prova il fallback. Se nessuno e' disponibile, produce un errore leggibile con suggerimento `ollama pull`.

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
Premi INVIO per registrare
```

Se l'utente scrive qualcosa invece di premere solo INVIO, quel testo viene usato direttamente.

### Text-to-speech

File:

```text
src/empower_hri/empower_hri/text_to_speech.py
```

Provider supportati:

```text
piper
pyttsx3
system
mock
```

Fallback:

- `piper` -> `pyttsx3` -> `system` -> `mock`;
- `pyttsx3` -> `system` -> `mock`;
- `system` -> `mock`;
- `mock` stampa su terminale.

Provider `system` prova:

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

ma i config HRI attuali usano `system` per inglese e `pyttsx3` per locale/italiano.

### Config HRI

File inglese:

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
language: "en"
record_seconds: 5.0
tts_provider: "system"
```

Prompt inglese: TIAGo deve dire che per ora puo' solo parlare e non puo' muoversi, afferrare oggetti, riconoscere persone o fare azioni fisiche da questo modulo.

File locale/italiano:

```text
src/empower_hri/config/hri_cpu_local.yaml
```

Valori principali:

```yaml
input_mode: "text"
llm_provider: "ollama"
llm_model: "qwen2.5:1.5b"
llm_fallback_model: "llama3.2:1b"
stt_provider: "faster_whisper"
stt_model: "tiny"
language: "it"
record_seconds: 4.0
tts_provider: "pyttsx3"
```

Prompt italiano: TIAGo deve dire che per ora puo' solo parlare e non deve inventare capacita' fisiche non implementate.

### Launch HRI disponibili

```text
src/empower_hri/launch/hri_voice_llm_english.launch.py
src/empower_hri/launch/hri_voice_llm_cpu.launch.py
```

Entrambi lanciano:

```text
voice_llm_node
```

Entrambi dichiarano launch arguments per sovrascrivere provider, modello, lingua, tempi, input mode e TTS.

Comando tipico inglese:

```bash
ros2 launch empower_hri hri_voice_llm_english.launch.py
```

Comando tipico italiano:

```bash
ros2 launch empower_hri hri_voice_llm_cpu.launch.py \
  input_mode:=voice \
  stt_provider:=faster_whisper \
  stt_model:=tiny \
  language:=it \
  llm_provider:=ollama \
  llm_model:=qwen2.5:1.5b \
  tts_provider:=system
```

### Test presenti

Sono presenti test solo per `empower_hri`:

```text
src/empower_hri/test/test_ollama_client.py
src/empower_hri/test/test_text_to_speech.py
```

Coprono:

- comportamento del mock LLM;
- selezione fallback modello Ollama;
- risposta mock in inglese;
- TTS mock;
- uso di `espeak-ng` nel provider `system`.

Non risultano test automatici per `empower_ros2`.

## Stato del collegamento fra HRI e EMPOWER

Attualmente non esiste integrazione funzionale fra i due sottosistemi.

`empower_hri`:

- conversa con l'utente;
- pubblica utterance e stato su topic ROS;
- non interpreta l'intento in formato task robotico;
- non scrive `task_description`;
- non invia trigger a `models_cacher_node`;
- non chiama `plan_generator_node`;
- non invia goal Nav2;
- non legge output EMPOWER.

`empower_ros2`:

- usa `task_description` da YAML o parametro launch;
- esegue planning tramite OpenAI `gpt-4o`, non tramite Qwen/Ollama;
- non ascolta topic `/hri/*`;
- non espone un servizio/action ROS per "esegui questo task naturale".

Punto naturale di integrazione futuro:

```text
/hri/user_utterance
  -> nodo intent/task bridge
  -> aggiorna o passa task_description a EMPOWER
  -> trigger planning
  -> opzionalmente trigger execution
  -> pubblica feedback verso /hri/robot_utterance o altro canale
```

Un'altra integrazione possibile e' sostituire/parzialmente affiancare OpenAI `gpt-4o` in `agents.py` con un provider locale Ollama, ma al momento cio' non e' implementato.

## Limiti attuali noti

### Manipolazione non implementata

La pipeline puo' produrre azioni simboliche:

```text
NAVIGATE
GRAB
DROP
PULL
PUSH
```

ma l'esecuzione ROS 2 implementa solo:

```text
NAVIGATE
```

Le altre azioni sono riconosciute e saltate con log. Non ci sono MoveIt, braccio o gripper.

### EMPOWER dipende da OpenAI

`agents.py` usa:

```python
from openai import OpenAI
client = OpenAI()
```

e chiama `gpt-4o`.

Serve quindi una chiave OpenAI valida in env o in:

```text
/root/exchange/openai_api_key.txt
```

Il file `openai_api_key.txt` e' referenziato dalla config. Non va condiviso.

### Modelli EMPOWER pesanti non versionati

Il loader richiede:

```text
yolow.pth
l2_encoder.onnx
l2_decoder.onnx
```

ma nella working tree attuale non risultano presenti nei path default sotto `src/empower_ros2/config`.

### HRI e' CPU-only

Il nodo HRI valida che:

```text
stt_device == cpu
```

Non supporta STT GPU nella configurazione attuale.

### Conversazione HRI non e' agentica

Il nodo HRI mantiene history e risponde, ma non ha tool/function calling, non chiama servizi ROS e non ha un planner. Il prompt gli vieta esplicitamente di dichiarare capacita' fisiche.

### Documentazione/package HRI

`src/empower_hri/setup.py` include nei `data_files`:

```text
README.md
README_QWEN_ENGLISH.md
```

relativi al package `src/empower_hri`. Nella working tree attuale quei file non risultano presenti sotto `src/empower_hri/`; i README corrispondenti esistono invece in root. Questo puo' causare problemi di build/install del package se `setuptools` cerca quei file.

## Comandi operativi principali

Build `empower_ros2`:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
rm -rf build/empower_ros2 install/empower_ros2
colcon build --symlink-install --packages-select empower_ros2
source /root/exchange/install/setup.bash
```

Build `empower_hri`:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
colcon build --symlink-install --packages-select empower_hri
source /root/exchange/install/setup.bash
```

Gazebo:

```bash
ros2 launch tiago_gazebo tiago_gazebo.launch.py \
  is_public_sim:=True \
  gazebo_version:=classic \
  world_name:=/root/exchange/exchange/person_table_child.world
```

Nav2 ground truth:

```bash
ros2 launch empower_ros2 tiago_nav2_ground_truth.launch.py \
  map:=/root/exchange/exchange/maps/person_table_child_static.yaml
```

Capture:

```bash
ros2 launch empower_ros2 empower_capture.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
```

Model cacher:

```bash
ros2 launch empower_ros2 empower_models_cacher.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
```

Planning:

```bash
ros2 launch empower_ros2 empower_plan.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
```

Visualizzazione:

```bash
ros2 launch empower_ros2 empower_visualization.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
```

Esecuzione low-level:

```bash
ros2 launch empower_ros2 empower_execute.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
```

HRI inglese:

```bash
ros2 launch empower_hri hri_voice_llm_english.launch.py
```

HRI italiano/locale:

```bash
ros2 launch empower_hri hri_voice_llm_cpu.launch.py
```

## Contratto mentale per sviluppo futuro

Se si vuole collegare LLM vocale ed EMPOWER, il codice attuale suggerisce questi passi tecnici:

1. Creare un nodo ponte HRI -> task, che sottoscriva `/hri/user_utterance`.
2. Trasformare l'utterance in un `task_description` controllato e sicuro.
3. Esporre EMPOWER come servizio/action ROS invece del solo trigger socket/file-based, oppure creare un wrapper che lanci il trigger socket esistente.
4. Rendere esplicito lo stato della pipeline: planning started, perception done, plan ready, execution started, navigation succeeded/failed.
5. Pubblicare feedback verso HRI, ad esempio su un topic che `voice_llm_node` o un futuro nodo dialog manager possa leggere.
6. Mantenere il vincolo che, finche' manipolazione non esiste, il robot non deve promettere `GRAB`, `DROP`, `PUSH`, `PULL`.

Questa repo, allo stato attuale, e' quindi una base con:

- pipeline EMPOWER quasi end-to-end fino a navigazione mobile;
- HRI vocale/testuale funzionante come canale conversazionale separato;
- integrazione fra le due componenti ancora da progettare e implementare.
