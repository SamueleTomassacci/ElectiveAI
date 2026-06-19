# Stato attuale del progetto ElectiveAI

Data del report: 2026-06-19

Questo file serve come contesto da incollare in ChatGPT o in un altro assistente
quando si devono fare modifiche al progetto. Il focus principale e' EMPOWER,
cioe' il package `empower_ros2`, perche' e' la parte su cui sono previsti i
cambiamenti. In questa versione e' documentata in dettaglio l'interazione
robot-bambino per facilitare la progettazione della gaze estimation durante
l'attivita' child.

## Sintesi breve

La repository e' una workspace ROS 2 Humble per una simulazione TIAGo in Gazebo.
Il progetto e' organizzato intorno a tre componenti principali:

1. `empower_ros2`: adattamento ROS 2 della pipeline EMPOWER. Gestisce capture
   RGB-D, perception, grounding, planning simbolico con LLM, generazione report,
   visualizzazione 3D dei rilevamenti, navigazione Nav2, manipolazione simulata
   (GRAB/DROP via CLI Gazebo) e missione orchestrata con HRI.
2. `empower_hri`: interazione vocale/testuale locale. Usa STT con
   `faster-whisper`, LLM locale tramite Ollama/Qwen e TTS locale. Pubblica e
   riceve messaggi ROS 2 su topic `/hri/*` e viene coordinato
   dall'orchestratore missione di `empower_ros2`. Supporta tre attivita'
   specifiche per bambini piu' un fallback generico.
3. `gaze_estimation`: package ROS 2 standalone per stimare se una persona sta
   guardando un oggetto target usando webcam RGB, Gazelle/Gaze-LLE,
   face detector YOLO e YOLO-World. Al momento e' indipendente da EMPOWER e HRI.

La scena supportata e':

```text
exchange/person_table_child.world
exchange/maps/person_table_child_static.yaml
src/empower_ros2/config/empower_person_table_child.yaml
```

La scena contiene TIAGo, una persona/terapista sul lato `-Y`, un bambino sul
lato `+Y`, e un tavolo con oggetti sul lato `+X`. La pipeline EMPOWER assume che
TIAGo possa vedere il tavolo con la camera frontale quando parte dall'origine
guardando verso `+X`, cioe' yaw `0`.

## Struttura generale della repo

File e directory principali:

```text
README.md
README_QWEN_ENGLISH.md
README_GAZE_ESTIMATION.md
start_tiago.sh
exchange/
  person_table_child.world
  maps/person_table_child_static.yaml
  models/person_standing/
empower_data/
  images/person_table_child_task/
  output/person_table_child_task/
src/
  empower_ros2/
  empower_hri/
  gaze_estimation/
models/
  gaze_estimation/
  piper/
```

`empower_data/` contiene gli output generati dalla pipeline: immagine RGB,
depth, point cloud, detection, planning, report e file intermedi.

Attenzione: il file `openai_api_key.txt` e' usato per la chiave OpenAI ma non
deve essere incollato in report o prompt esterni.

## Componente 1: EMPOWER / `empower_ros2`

### Obiettivo di `empower_ros2`

`empower_ros2` porta nel mondo ROS 2 una pipeline EMPOWER che parte da una
snapshot RGB-D della scena e produce:

- relazioni simboliche tra oggetti;
- descrizione testuale della scena;
- piano simbolico con azioni primitive;
- bounding box e maschere degli oggetti;
- point cloud colorata e marker 3D degli oggetti;
- esecuzione low-level tramite Nav2 e simulazione Gazebo (GRAB/DROP).

La pipeline e' centrata su questo task configurato:

```yaml
use_case: person_table_child_task
task_description: "Take the book, go to the child and drop the book"
output_root: /root/exchange/empower_data
```

Il file di configurazione principale e':

```text
src/empower_ros2/config/empower_person_table_child.yaml
```

### File principali di EMPOWER

Codice "upstream style" EMPOWER, installato come moduli Python top-level:

```text
src/empower_ros2/agents.py
src/empower_ros2/detection.py
src/empower_ros2/loader.py
src/empower_ros2/models.py
src/empower_ros2/paths.py
```

Gli stessi file sono duplicati in:

```text
src/empower_ros2/python/agents.py
src/empower_ros2/python/detection.py
src/empower_ros2/python/loader.py
src/empower_ros2/python/models.py
src/empower_ros2/python/paths.py
```

Al momento risultano identici alle versioni in `src/empower_ros2/`. Il
`setup.py` installa i moduli top-level tramite `py_modules`, quindi questi file
sono effettivamente importati come `agents`, `detection`, `loader`, `models`,
`paths`.

Nodi ROS 2 e adapter:

```text
src/empower_ros2/python/empower_ros2/scene_capture_node.py
src/empower_ros2/python/empower_ros2/models_cacher_node.py
src/empower_ros2/python/empower_ros2/plan_generator_node.py
src/empower_ros2/python/empower_ros2/loader_adapter.py
src/empower_ros2/python/empower_ros2/report_writer.py
src/empower_ros2/python/empower_ros2/plan_parser.py
src/empower_ros2/python/empower_ros2/color_pcl_node.py
src/empower_ros2/python/empower_ros2/marker_centroids_node.py
src/empower_ros2/python/empower_ros2/low_level_execution_node.py
src/empower_ros2/python/empower_ros2/primitive_actions.py
src/empower_ros2/python/empower_ros2/mission_orchestrator_node.py
src/empower_ros2/python/empower_ros2/ground_truth_localizer_node.py
src/empower_ros2/python/empower_ros2/pointcloud_utils.py
```

Launch file principali:

```text
src/empower_ros2/launch/empower_capture.launch.py
src/empower_ros2/launch/empower_models_cacher.launch.py
src/empower_ros2/launch/empower_plan.launch.py
src/empower_ros2/launch/empower_visualization.launch.py
src/empower_ros2/launch/empower_execute.launch.py
src/empower_ros2/launch/hri_empower_mission.launch.py
src/empower_ros2/launch/tiago_nav2_ground_truth.launch.py
```

### Dipendenze concettuali di EMPOWER

La pipeline EMPOWER usa:

- ROS 2 Humble;
- Gazebo classic con TIAGo;
- Nav2 per la navigazione mobile;
- topic RGB-D della camera frontale di TIAGo;
- OpenAI GPT-4o via `openai` Python SDK per scene understanding e planning;
- YOLO-World tramite MMEngine/MMYOLO per detection open-vocabulary;
- EfficientViT-SAM via encoder/decoder ONNX per segmentazione;
- spaCy `en_core_web_sm`;
- gensim `word2vec-google-news-300`;
- Open3D per point cloud;
- TF2 per trasformazioni fra camera, map e base.

I pesi attesi da EMPOWER sono sotto la config installata del package:

```text
config/yolow/yolow.pth
config/efficientvitsam/l2_encoder.onnx
config/efficientvitsam/l2_decoder.onnx
```

`models_cacher_node.py` controlla che questi file esistano tramite
`EmpowerRos2Loader`.

### Flusso operativo EMPOWER dettagliato

Il flusso completo, senza HRI, e':

1. Avvio Gazebo con `exchange/person_table_child.world`.
2. Avvio Nav2 con localizzazione ground truth.
3. Capture RGB-D con `empower_capture.launch.py`.
4. Avvio model cacher con `empower_models_cacher.launch.py`.
5. Planning con `empower_plan.launch.py`.
6. Visualizzazione opzionale con `empower_visualization.launch.py`.
7. Execution low-level con `empower_execute.launch.py`.

#### 1. Capture scena

Nodo:

```text
empower_ros2.scene_capture_node
```

File:

```text
src/empower_ros2/python/empower_ros2/scene_capture_node.py
```

Input ROS:

```text
/head_front_camera/rgb/image_raw
/head_front_camera/depth/image_raw
/head_front_camera/depth/camera_info
```

Output salvati:

```text
empower_data/images/person_table_child_task/scan.jpg
empower_data/output/person_table_child_task/depth.npy
empower_data/output/person_table_child_task/depth_pointcloud.pcd
empower_data/output/person_table_child_task/camera_info.pkl
empower_data/output/person_table_child_task/camera_info.json
```

Il nodo aspetta un messaggio RGB, uno depth e uno camera info. Poi:

- converte RGB in immagine OpenCV BGR e salva `scan.jpg`;
- normalizza depth in millimetri e salva `depth.npy`;
- usa gli intrinseci camera per generare una point cloud da depth;
- applica una trasformazione assi compatibile con la convenzione usata dal
  resto della pipeline;
- salva camera info in pickle e JSON;
- se `capture_once` e' true, termina il nodo.

#### 2. Model cacher

Nodo:

```text
empower_ros2.models_cacher_node
```

File:

```text
src/empower_ros2/python/empower_ros2/models_cacher_node.py
```

Il model cacher carica una sola volta i modelli pesanti:

- YOLO-World;
- EfficientViT-SAM encoder/decoder;
- spaCy;
- word2vec;
- chiave OpenAI da ambiente o da `openai_api_key_file`.

Poi apre un server TCP locale:

```text
127.0.0.1:10000
```

Quando riceve un payload con comando `"detection"`, importa/reloada il modulo
top-level `detection`, crea `Detection()` e chiama:

```python
detection_instance.set_loader(self.loader)
```

Se viene passato un `task_description`, il model cacher sovrascrive la voce
`task_dict[use_case]` dentro `Detection`, cosi' il planning usa il task arrivato
dal launch/orchestratore invece dei task hardcoded.

Questa architettura evita di ricaricare i modelli a ogni run del planning.

#### 3. Planning e detection EMPOWER

Nodo:

```text
empower_ros2.plan_generator_node
```

File:

```text
src/empower_ros2/python/empower_ros2/plan_generator_node.py
```

Il plan generator:

- rimuove il vecchio `planning.txt`;
- invia al model cacher un JSON con:

```json
{
  "command": "detection",
  "task_description": "..."
}
```

- aspetta che venga generato `planning.txt`;
- parsea il piano in `planning.json`;
- rigenera report leggibili con `report_writer.py`;
- termina.

La logica EMPOWER vera e propria sta in:

```text
src/empower_ros2/detection.py
src/empower_ros2/agents.py
src/empower_ros2/models.py
```

##### `Agents`

`agents.py` usa OpenAI GPT-4o con immagine base64. Ci sono due modalita':

- `single_agent()`: produce relazioni e piano in un'unica chiamata.
- `multi_agent_vision_planning()`: modalita' usata attualmente.

La modalita' multi-agent fa tre passaggi:

1. `enviroment_agent()` produce triple `(subject, relation, object)` rilevanti
   per il task.
2. `description_agent()` produce una descrizione testuale della scena usando le
   relazioni.
3. planning agent produce una sequenza di azioni simboliche usando solo:

```text
NAVIGATE
GRAB
DROP
PULL
PUSH
```

Il modello e' hardcoded a:

```text
gpt-4o
```

Non c'e' ancora un parametro YAML per cambiare modello OpenAI.

##### `Detection.run_experiment()`

Questa funzione:

- legge `scan.jpg`;
- codifica l'immagine in base64;
- istanzia `Agents(encoded_image, task_description)`;
- chiama `multi_agent_vision_planning()`;
- salva:

```text
planning.pkl
planning.txt
```

`planning.pkl` contiene:

```python
{
  "environment_agent_info": "...",
  "description_agent_info": "...",
  "planning_agent_info": "..."
}
```

##### `Detection.run_image()`

Dopo il planning testuale, `run_image()` fa grounding visivo:

1. prende le relazioni prodotte dall'LLM;
2. estrae classi oggetto con spaCy e word2vec;
3. costruisce una stringa di classi per YOLO-World;
4. esegue YOLO-World su `scan.jpg`;
5. per ogni bounding box con score `> 0.1`, esegue EfficientViT-SAM;
6. salva maschere e immagini debug;
7. prova a riordinare/associare le detection alle relazioni testuali;
8. salva `detection.pkl`.

Output principali:

```text
detection.pkl
scan_with_bb.jpg
mask.jpg
yolo.jpg
rgb_0.jpg
rgb_1.jpg
...
```

Il grounding relazionale e' fragile: il codice parsea le relazioni facendo split
su `")"` e `","`, quindi si aspetta un formato molto preciso tipo:

```text
1) (book, on, table)
```

Se l'LLM cambia formato, aggiunge testo extra, usa virgole nei nomi, o produce
relazioni non previste, questa parte puo' rompersi o associare male le label.

#### 4. Report leggibili

File:

```text
src/empower_ros2/python/empower_ros2/report_writer.py
src/empower_ros2/python/empower_ros2/plan_parser.py
src/empower_ros2/python/empower_ros2/export_reports.py
```

Report generati:

```text
relations.txt
relations.json
scene_description.txt
symbolic_plan.txt
planning.json
detections.json
empower_report.txt
```

`plan_parser.py` riconosce solo azioni con primo token:

```text
NAVIGATE, GRAB, DROP, PULL, PUSH
```

Per `NAVIGATE` estrae `target`.
Per `GRAB`, `PULL`, `PUSH` estrae `target`.
Per `DROP` prova a identificare una relazione spaziale, per esempio:

```text
DROP book right to laptop
```

diventa:

```json
{
  "action": "DROP",
  "target": "book",
  "spatial_relation": "right to",
  "reference": "laptop"
}
```

#### 5. Visualizzazione 3D

Nodi:

```text
empower_ros2.color_pcl_node
empower_ros2.marker_centroids_node
```

`color_pcl_node.py`:

- legge `depth_pointcloud.pcd`;
- legge `detection.pkl` e `camera_info.pkl`;
- proietta le maschere 2D sulla point cloud;
- colora i punti appartenenti agli oggetti;
- salva:

```text
colored_image.png
colored_pcl.pcd
colors_dict.pkl
```

- pubblica:

```text
/empower/colored_pointcloud
```

`marker_centroids_node.py`:

- legge `detection.pkl` e `colors_dict.pkl`;
- calcola centroidi e punti massimi dei cluster 3D;
- prova a trasformare i punti in `map` usando TF;
- se TF fallisce, pubblica nel frame camera;
- pubblica:

```text
/pcl_centroids
/pcl_names
/pcl_maximum
```

Questi marker sono usati sia per RViz sia per la navigation low-level.

#### 6. Execution low-level

Nodi:

```text
empower_ros2.low_level_execution_node
empower_ros2.primitive_actions
```

`low_level_execution_node.py` legge `planning.json` o `planning.txt`, poi esegue
gli step uno alla volta.

Azioni supportate:

- `NAVIGATE`: eseguita realmente via Nav2 `NavigateToPose`;
- `GRAB`: simulata in Gazebo via CLI `gz model` (nasconde l'oggetto);
- `DROP`: simulata in Gazebo via CLI `gz model` (riposiziona l'oggetto);
- `PUSH` e `PULL`: riconosciute ma saltate come non implementate.

Per `NAVIGATE`:

- il nodo aspetta marker su `/pcl_centroids` e `/pcl_names`;
- normalizza le label degli oggetti;
- cerca un marker compatibile col target del piano;
- converte il marker in `map`;
- calcola un goal a distanza `standoff_distance` dal centroide;
- orienta TIAGo verso l'oggetto;
- invia il goal a Nav2.

Navigazione speciale non basata su marker EMPOWER:

- `child_navigation`: se il target contiene un alias del bambino (default:
  `child`, `the child`, `kid`), naviga verso una posa fissa configurata in YAML
  (`child_navigation.x/y/yaw`). Attualmente: `(0.0, 0.95, 1.5708)`.
- `person_navigation`: se il target contiene un alias della persona/terapista
  (default: `person`, `adult`, `standing person`, ecc.), naviga verso una posa
  fissa configurata.

Per `GRAB`:

- se `object_simulation.enabled` e' false, viene saltata;
- se e' true, il nodo nasconde il modello Gazebo spostando l'entita' a una
  posizione nascosta (attualmente `0, 0, -10` sotto il pavimento);
- traccia l'oggetto come `held_object`.

Oggetti configurati per la simulazione:

```text
book -> clear_red_book_on_right_table (aliases: book)
computer -> laptop_computer_on_right_table (aliases: computer, laptop, laptop computer)
peluche -> teddy_bear_on_right_table (aliases: peluche, teddy bear, plush toy, stuffed animal)
```

Per `DROP`:

- se il robot e' stato navigato verso il bambino
  (`last_navigation_target_is_child`), usa la posizione fissa
  `child_delivery_table_drop` (`0, 1.65, 1.30`, cioe' sul tavolo lato bambino);
- se `object_simulation.fixed_drop.enabled` e' true, usa una posizione fissa
  configurata;
- altrimenti, calcola una posa davanti al robot usando TF e `drop_distance`;
- sposta il modello Gazebo alla posizione calcolata via CLI `gz model`.

Questa e' manipolazione simulata a livello mondo Gazebo, non manipolazione reale
con MoveIt, braccio o gripper.

Parametro `child_delivery_handled_by_empower`:

```yaml
child_delivery_handled_by_empower: true
```

Attualmente e' `true` nel YAML della missione. Significa che e' il piano EMPOWER
(che contiene `NAVIGATE to the child`) a gestire la navigazione verso il bambino,
e il mission_orchestrator salta il suo `GO_TO_CHILD` hardcoded.

### Configurazione scena attuale (YAML)

Parametri chiave da `empower_person_table_child.yaml`:

```yaml
task_description: "Take the book, go to the child and drop the book"
child_context:
  enabled: true
  planner_description: >
    The child is present in the room but is not visible in the RGB image.
    The child is a known non-visual target. ...
child_navigation:
  enabled: true
  x: 0.0
  y: 0.95
  yaw: 1.5708
object_simulation:
  enabled: true
  child_delivery_table_drop_x: 0.0
  child_delivery_table_drop_y: 1.65
  child_delivery_table_drop_z: 1.30
```

#### 7. Missione HRI + EMPOWER

Nodo:

```text
empower_ros2.mission_orchestrator_node
```

Launch:

```text
src/empower_ros2/launch/hri_empower_mission.launch.py
```

Config:

```text
src/empower_ros2/config/hri_empower_mission.yaml
```

La missione orchestrata collega Nav2, HRI e EMPOWER.

Stati principali:

```text
GO_TO_THERAPIST
WAIT_TASK
ACK_TASK
WAIT_ACTIVITY
ACK_ACTIVITY
GO_TO_SPAWN
RUN_EMPOWER
GO_TO_CHILD      (saltato se child_delivery_handled_by_empower=true)
CHILD_READY
CHILD_ACTIVITY
MISSION_COMPLETE
```

Flusso:

1. TIAGo naviga verso la terapista (posa: `0, -1.3, -pi/2`).
2. Aspetta un task fisico su `/hri/user_utterance` (timeout 300s).
3. Pubblica un ack vocale (`"Okay, I understood the task."`) su
   `/hri/dialog_command`.
4. Aspetta una descrizione di attivita' per il bambino (timeout 300s).
5. Normalizza il tipo di attivita' tramite keyword matching (vedi sotto).
6. Pubblica un ack vocale (`"Okay, I understood the activity."`).
7. Naviga allo spawn (posa: `0, 0, 0`).
8. Esegue capture EMPOWER (lancia `scene_capture_node`).
9. Lancia planning EMPOWER (`empower_plan.launch.py`).
10. Lancia execution EMPOWER (color_pcl_node + marker_centroids_node +
    low_level_execution_node).
11. Se `child_delivery_handled_by_empower=false`, naviga verso il bambino;
    altrimenti salta (attualmente e' `true`).
12. Pubblica `/mission/child_ready`.
13. Pubblica `start_child_activity` su `/hri/dialog_command` con context.
14. Aspetta `/hri/dialog_complete` (timeout 300s).

Il nodo lancia capture e EMPOWER come processi figli (`subprocess.Popen`),
monitorandone l'output.

Topic missione:

```text
/mission/state
/mission/feedback
/mission/task_description
/mission/child_ready
```

Topic HRI usati:

```text
/hri/user_utterance
/hri/dialog_command
/hri/dialog_complete
```

##### Normalizzazione del tipo di attivita'

`_normalize_activity_type()` in `mission_orchestrator_node.py` (riga 275)
usa keyword matching sull'utterance del terapista. Le regole vengono
controllate nell'ordine sotto; vince la prima che matcha:

**1. `computer_emotion_recognition`** — keyword:

```text
computer emotion recognition, emotion recognition with the computer,
use the computer for emotion recognition, show emotion pictures,
recognize emotions from photos, recognize emotions from pictures,
look at emotion photos, look at pictures of emotions,
emotion recognition, recognize emotions, emotion pictures,
emotion photos, emotions
```

**2. `mood_check_teddy`** — keyword:

```text
how they feel, how he feels, how she feels, feelings, mood,
state of mind, teddy, teddy bear, plush
```

**3. `storytelling`** — keyword:

```text
story, storytelling, read the story, tell a story
```

Se nessuna keyword matcha, il tipo e' `generic_child_activity`.

### Output EMPOWER attualmente presenti

Nella repo sono presenti output generati per:

```text
empower_data/output/person_table_child_task/
```

Esempio di report corrente:

```text
Relations:
1) (teddy bear, on, table)
2) (laptop, on, table)
3) (book, on, table)
4) (laptop, right to, teddy bear)
5) (book, right to, laptop)

Symbolic plan:
1. NAVIGATE to the table
2. NAVIGATE to the right side of the table
3. GRAB book
4. DROP book right to laptop

Detections:
- laptop
- table
- book
```

Il `planning.json` corrente contiene quattro step:

```text
NAVIGATE -> target "the table"
NAVIGATE -> target "the right side of the table"
GRAB -> target "book"
DROP -> target "book", reference "laptop", relation "right to"
```

### Punti critici e fragilita' di EMPOWER

Questi sono i punti piu' importanti da considerare prima di modificare
`empower_ros2`.

1. Parsing LLM fragile.
   `detection.py` assume che le relazioni abbiano formato preciso con parentesi
   e virgole. `plan_parser.py` e' piu' robusto, ma anche lui dipende da action
   words all'inizio della riga.

2. Prompt e modello OpenAI sono hardcoded.
   `agents.py` usa `gpt-4o` direttamente. Non c'e' parametro YAML per modello,
   temperatura o prompt esterni.

3. `Detection.task_dict` contiene task storici hardcoded.
   Il task reale arriva da YAML/orchestratore solo perche' `models_cacher_node`
   sovrascrive `task_dict[use_case]`. Se si usa `Detection` fuori dal model
   cacher, `person_table_child_task` non e' nel dizionario originale.

4. Label mismatch.
   La scena/config usa `computer`, ma l'LLM/detection puo' produrre `laptop`.
   La simulazione oggetti conosce `computer`, mentre i marker possono chiamarsi
   `laptop`. Il low_level_execution_node gestisce alias
   (`"computer", "laptop", "laptop computer"`), ma il matching dipende dalla
   catena completa.

5. Navigation target mismatch.
   EMPOWER puo' generare target non-oggetto, come "right side of the table".
   L'esecuzione low-level naviga verso marker oggetto, non verso regioni
   semantiche astratte. Il codice prova sotto-stringhe, quindi spesso
   trova `table`, ma non e' garantito.

6. Manipolazione reale non implementata.
   `GRAB` e `DROP` sono simulati via CLI `gz model`. Non esiste controllo
   reale del braccio, planning MoveIt, grasp pose o gripper.

7. Object simulation limitata.
   Solo `book`, `computer`, `peluche` sono configurati. Nuovi oggetti richiedono
   mapping YAML e probabilmente modifiche se servono alias.

8. Heavy dependencies.
   YOLO-World, EfficientViT-SAM, spaCy e word2vec sono pesanti. Il model cacher
   riduce i tempi caricando una volta sola, ma il primo avvio puo' essere lento.

9. Frame e TF delicati.
   Capture, point cloud e marker dipendono da frame camera, `map`, `base_footprint`
   e transform disponibili. Se TF non e' disponibile, i marker possono essere
   pubblicati nel frame camera, ma execution verso Nav2 richiede conversione
   coerente in `map`.

10. Duplicazione file top-level e `python/`.
    I file `agents.py`, `detection.py`, `loader.py`, `models.py`, `paths.py`
    sono duplicati. Se si modifica uno, bisogna verificare quale copia viene
    installata/importata e mantenere coerenza.

### Dove intervenire per modifiche EMPOWER

Per cambiare il task di default:

```text
src/empower_ros2/config/empower_person_table_child.yaml
```

Parametro:

```yaml
task_description: "..."
```

Per cambiare prompt, modello o logica LLM:

```text
src/empower_ros2/agents.py
src/empower_ros2/python/agents.py
```

Per rendere piu' robusto parsing relazioni / classi / grounding:

```text
src/empower_ros2/detection.py
src/empower_ros2/python/detection.py
```

Per cambiare parser piano simbolico:

```text
src/empower_ros2/python/empower_ros2/plan_parser.py
```

Per cambiare report generati:

```text
src/empower_ros2/python/empower_ros2/report_writer.py
```

Per cambiare logica di navigazione verso oggetti:

```text
src/empower_ros2/python/empower_ros2/low_level_execution_node.py
src/empower_ros2/python/empower_ros2/primitive_actions.py
```

Per aggiungere oggetti simulati `GRAB`/`DROP`:

```text
src/empower_ros2/config/empower_person_table_child.yaml
src/empower_ros2/python/empower_ros2/low_level_execution_node.py
```

Per cambiare il flusso missione HRI + EMPOWER:

```text
src/empower_ros2/python/empower_ros2/mission_orchestrator_node.py
src/empower_ros2/config/hri_empower_mission.yaml
src/empower_ros2/launch/hri_empower_mission.launch.py
```

## Componente 2: Interazione / `empower_hri`

### Obiettivo

`empower_hri` implementa un nodo ROS 2 per dialogo locale:

```text
microfono o terminale -> STT -> LLM locale -> TTS -> speaker
```

Il package non controlla direttamente il robot. Pubblica utterance e riceve
comandi da altri nodi. Nella missione completa, il controllo del robot resta in
`mission_orchestrator_node.py`.

### File principali

```text
src/empower_hri/empower_hri/voice_llm_node.py
src/empower_hri/empower_hri/speech_to_text.py
src/empower_hri/empower_hri/text_to_speech.py
src/empower_hri/empower_hri/ollama_client.py
src/empower_hri/empower_hri/terminal_io.py
src/empower_hri/config/hri_cpu_english.yaml
src/empower_hri/config/hri_cpu_local.yaml
src/empower_hri/launch/hri_voice_llm_english.launch.py
src/empower_hri/launch/hri_voice_llm_cpu.launch.py
```

### Topic ROS 2

Pubblica:

```text
/hri/user_utterance
/hri/robot_utterance
/hri/dialog_state
/hri/dialog_complete
```

Sottoscrive:

```text
/hri/dialog_command
/mission/state
```

### Modalita' del nodo HRI

`voice_llm_node.py` ha due modalita' principali che vengono gestite dal thread
`_interaction_loop`:

1. `therapist_command`
   - modalita' iniziale;
   - usata per ricevere task e descrizione attivita' dal terapista;
   - nel launch missione, `therapist_llm_replies` e' impostato a `false`,
     quindi il nodo non risponde con l'LLM ma si limita a pubblicare l'utterance
     del terapista su `/hri/user_utterance`;
   - `follow_mission_state_for_therapist_input` e' `true`: il nodo legge input
     dal terapista solo quando `/mission/state` e' `WAIT_TASK` o `WAIT_ACTIVITY`,
     e solo una volta per stato (evita input multipli nello stesso stato).

2. `child_activity`
   - attivata quando il nodo riceve un comando `start_child_activity` su
     `/hri/dialog_command`;
   - il nodo genera un'apertura dell'attivita';
   - poi fa turni interattivi con STT/LLM/TTS;
   - al termine pubblica il risultato su `/hri/dialog_complete` e torna a
     `therapist_command`.

### Attivita' bambino (child activity) — DETTAGLIO

Questa sezione descrive in dettaglio il flusso dell'interazione robot-bambino,
che e' il momento in cui potrebbe intervenire la gaze estimation.

#### Attivazione

Il mission orchestrator, dopo aver completato EMPOWER ed essere arrivato
(o meno) davanti al bambino, pubblica su `/hri/dialog_command`:

```json
{
  "command": "start_child_activity",
  "context": {
    "task": "Take the book, go to the child and drop the book",
    "activity_description": "<testo inserito dal terapista>",
    "activity_type": "storytelling"
  }
}
```

Il campo `activity_type` e' gia' normalizzato dall'orchestratore.

#### Tipi di attivita' supportati

Definiti in `voice_llm_node.py` riga 35:

```python
SUPPORTED_CHILD_ACTIVITY_TYPES = {
    "computer_emotion_recognition",
    "mood_check_teddy",
    "storytelling",
    "generic_child_activity",
}
```

Se `activity_type` non e' nell'insieme, viene forzato a
`generic_child_activity`.

#### System prompt per attivita'

Ogni tipo ha un system prompt specifico (funzione `build_child_activity_system_prompt`,
riga 43):

**`computer_emotion_recognition`**:

TIAGo aiuta il bambino a riconoscere emozioni da foto mostrate al computer.
Offre scelte semplici (happy, sad, angry, scared, surprised). Chiede una
domanda alla volta, incoraggia il bambino, da' suggerimenti se serve.

**`mood_check_teddy`**:

TIAGo usa un orsacchiotto come intermediario per chiedere al bambino come si
sente. Domande semplici e non forzate sui sentimenti. L'orsacchiotto puo'
essere curioso, timido, felice, stanco o calmo.

**`storytelling`**:

TIAGo legge/inventa una storia da un libro. Racconta in piccole parti per
mantenere il bambino coinvolto. Fa domande occasionali sulla storia. Mantiene
continuita' della storia tra i turni.

**`generic_child_activity`**:

Usa la descrizione testuale dell'attivita' fornita dal terapista come istruzione
di alto livello. Fallback se il tipo non e' riconosciuto.

#### Frase di apertura

Ogni tipo ha una frase di apertura fissa (funzione `child_activity_opening`,
riga 107):

- `computer_emotion_recognition`: "Let's look at the pictures on the computer
  together. What emotion do you see in this picture: happy, sad, angry, or
  scared?"
- `mood_check_teddy`: "This teddy bear would like to know how you feel today.
  Are you happy, sad, tired, calm, or something else?"
- `storytelling`: "I found a book with many little stories inside. I will
  choose one and read it with you. Are you ready to begin?"
- `generic_child_activity`: "Let's do this activity together: <descrizione>.
  Would you like to start?"

#### Loop interattivo dell'attivita'

Metodo `_run_child_activity_session()` (riga 434):

1. Salva il context dell'attivita' e il tipo.
2. Pubblica lo stato `child_activity_active` su `/hri/dialog_state`.
3. Genera e pronuncia la frase di apertura (TTS).
4. Inserisce nella history LLM:
   - `user: [START_ACTIVITY]`
   - `assistant: <frase di apertura>`
5. Entra nel loop interattivo:
   - Legge input dal bambino (`_read_child_text()`);
   - In voice mode con `storytelling_auto_listen=true`, usa VAD automatica
     (non richiede push-to-talk): registra quando rileva voce, si ferma dopo
     `stt_vad_silence_sec` (1.5s) di silenzio, max `stt_vad_max_duration_sec`
     (30s);
   - In text mode, chiede input da terminale;
   - Se il bambino dice `/quit` o `exit`, esce;
   - Se nessun input (silenzio VAD), continua ad ascoltare;
   - Altrimenti: pubblica utterance bambino, chiama LLM con history + system
     prompt dell'attivita', pubblica risposta, la pronuncia con TTS;
   - Incrementa il contatore turni.
6. Il loop termina quando:
   - Raggiunge `storytelling_max_turns` (default: 10);
   - Riceve comando `stop` su `/hri/dialog_command`;
   - L'utente digita `/quit`;
   - Il nodo viene fermato.
7. Al termine, pubblica su `/hri/dialog_complete`:

```json
{
  "reason": "max_turns",
  "turns": 10,
  "activity_type": "storytelling"
}
```

8. Ritorna in modalita' `therapist_command`.

#### Topic e stati durante l'attivita' child

Durante l'interazione robot-bambino, i topic attivi sono:

```text
/hri/dialog_state      <- pubblica: child_activity_active, waiting_for_text,
                          waiting_for_push_to_talk, listening, recording,
                          transcribing, thinking, speaking, ready, error
/hri/robot_utterance   <- ogni risposta del robot
/hri/user_utterance    <- ogni utterance del bambino (solo come dato loggato,
                          non consumato dall'orchestratore in questo stato)
/hri/dialog_complete   <- quando l'attivita' finisce
```

Lo stato pubblicato su `/hri/dialog_state` segue questo ciclo per ogni turno:

```text
child_activity_active -> waiting_for_text / listening -> recording ->
transcribing -> thinking -> speaking -> ready -> (prossimo turno)
```

#### Parametri rilevanti per l'interazione child

```yaml
storytelling_max_turns: 10        # turni massimi dell'attivita'
storytelling_auto_listen: true    # VAD automatica (senza push-to-talk)
stt_vad_energy_threshold: 0.01   # soglia energia per rilevamento voce
stt_vad_silence_sec: 1.5         # secondi di silenzio per chiudere la registrazione
stt_vad_max_duration_sec: 30.0   # durata massima registrazione
max_history_turns: 4             # turni di history LLM (deque maxlen=8 messaggi)
max_response_tokens: 120         # token massimi per risposta LLM
temperature: 0.4                 # temperatura LLM
```

#### Punti di integrazione per gaze estimation

Analisi dei punti dove la gaze estimation potrebbe inserirsi durante
l'interazione robot-bambino:

1. **Quando sapere che l'attivita' child e' in corso**:
   - Topic `/mission/state` pubblica `CHILD_ACTIVITY`;
   - Topic `/hri/dialog_state` pubblica `child_activity_active` all'inizio;
   - Il tipo di attivita' e' nel context di `start_child_activity`.

2. **Quando il bambino dovrebbe guardare un target specifico**:
   - In `computer_emotion_recognition`, il bambino deve guardare lo schermo
     del computer (il laptop sul tavolo nella scena);
   - In `mood_check_teddy`, il bambino potrebbe guardare il peluche o il robot;
   - In `storytelling`, il bambino potrebbe guardare il robot o il libro.

3. **Timing dell'attenzione**:
   - Il ciclo di stato su `/hri/dialog_state` indica quando il robot sta
     parlando (`speaking`), quando aspetta risposta (`listening`,
     `waiting_for_text`), e quando sta pensando (`thinking`);
   - La gaze estimation potrebbe correlare lo sguardo del bambino con la fase
     del dialogo.

4. **Oggetti target gia' rilevati da EMPOWER**:
   - `detection.pkl` contiene bounding box e maschere degli oggetti nella scena;
   - `/pcl_centroids` e `/pcl_names` contengono posizioni 3D degli oggetti;
   - La gaze estimation potrebbe usare queste informazioni per sapere dove sono
     gli oggetti nella scena.

5. **Topic non ancora pubblicati (gap da colmare)**:
   - Lo status di gaze non e' pubblicato su nessun topic ROS;
   - Non c'e' un topic che indichi "il bambino sta guardando X";
   - Non c'e' integrazione tra `/hri/dialog_state` e gaze;
   - Non c'e' un nodo che combini gaze + dialog per adattare il comportamento
     del robot.

6. **Camera disponibile per gaze estimation**:
   - La camera frontale di TIAGo (`/head_front_camera/rgb/image_raw`) e' gia'
     usata da EMPOWER per la capture; potrebbe essere usata anche per gaze;
   - Il package `gaze_estimation` attuale usa una webcam esterna
     (`cv2.VideoCapture(camera_index)`), non un topic ROS;
   - Per integrazione in simulazione, bisognerebbe creare un nodo che
     sottoscriva al topic camera di TIAGo e faccia gaze estimation su quel
     feed, oppure usare una webcam esterna puntata sulla scena reale;
   - In Gazebo, la camera frontale vede la scena dal punto di vista del robot;
     per gaze estimation, servirebbero una camera che vede la faccia del bambino,
     che non esiste nella simulazione attuale.

### STT

File:

```text
src/empower_hri/empower_hri/speech_to_text.py
```

Provider:

- `faster_whisper`: registra WAV e trascrive su CPU;
- `mock`: input da terminale.

Il codice forza STT CPU-only. Se si configura `stt_device != cpu`, il nodo
solleva errore.

Sono disponibili:

- push-to-talk: premi invio, registra per `record_seconds` (default 5s);
- auto-listening con VAD per attivita' child: registra automaticamente quando
  rileva voce sopra `stt_vad_energy_threshold`, si ferma dopo
  `stt_vad_silence_sec` di silenzio. Ignora segmenti troppo corti
  (`min_speech_sec=0.3s`).

### LLM

File:

```text
src/empower_hri/empower_hri/ollama_client.py
```

Provider:

- `ollama`;
- `mock`.

Modello default nei README/config:

```text
qwen2.5:1.5b
```

Fallback:

```text
llama3.2:1b
```

Il client usa le API HTTP locali di Ollama:

```text
http://localhost:11434/api/chat
```

### TTS

File:

```text
src/empower_hri/empower_hri/text_to_speech.py
```

Provider supportati:

- `piper`: usa il binario `piper` + model file; genera WAV e lo riproduce
  con `aplay`/`paplay`/`ffplay`;
- `pyttsx3`: usa `pyttsx3.init()` con selezione voce per lingua;
- `system` (`espeak-ng`, `espeak`, `spd-say`);
- `mock`: stampa a terminale.

Il TTS usa fallback automatici. Per esempio, se `pyttsx3` non funziona, prova
`system`, poi `mock`. La config inglese usa `tts_provider: "system"`.

### Relazione con EMPOWER

`empower_hri` non lancia EMPOWER. La relazione avviene tramite:

- `/hri/user_utterance`: il nodo HRI pubblica task/attivita';
- `mission_orchestrator_node`: riceve utterance, decide quando avviare EMPOWER;
- `/hri/dialog_command`: l'orchestratore chiede al nodo HRI di parlare o avviare
  attivita' child;
- `/hri/dialog_complete`: il nodo HRI notifica l'orchestratore quando
  l'attivita' child e' terminata.

Quindi, se si vuole modificare il comportamento robotico, di solito bisogna
modificare `empower_ros2`, non `empower_hri`.

## Componente 3: Gaze estimation / `gaze_estimation`

### Obiettivo

`gaze_estimation` e' un package ROS 2 standalone che usa una webcam RGB per
stimare se la persona sta guardando uno degli oggetti target.

E' esplicitamente indipendente da:

```text
empower_ros2
empower_hri
EMPOWER
TIAGo
Gazebo
Nav2
/hri/*
/empower/*
socket TCP esistenti
```

Non usa RealSense, rosbag, depth, point cloud o immagini salvate come input
obbligatorio.

### File principali

```text
src/gaze_estimation/gaze_estimation/gaze_webcam_node.py
src/gaze_estimation/gaze_estimation/gazelle_wrapper.py
src/gaze_estimation/gaze_estimation/yolo_world_wrapper.py
src/gaze_estimation/gaze_estimation/geometry.py
src/gaze_estimation/gaze_estimation/visualization.py
src/gaze_estimation/gaze_estimation/config_loader.py
src/gaze_estimation/gaze_estimation/device.py
src/gaze_estimation/config/gaze_webcam.yaml
src/gaze_estimation/launch/gaze_webcam.launch.py
```

### Modelli usati

Pesi attesi in:

```text
models/gaze_estimation/
```

La config attuale (`gaze_webcam.yaml`) specifica il path locale:

```yaml
weights_dir: "/home/lorenzo/Scrivania/ElectiveAI/models/gaze_estimation"
```

Checkpoint:

```text
gazelle_dinov2_vitl14_inout.pt   (Gazelle per heatmap gaze e inout score)
yolov8x-worldv2.pt              (YOLO-World per detection oggetti target)
yolov8n-face.pt                 (face detector; nome nel YAML, era best.pt nei README)
```

### Flusso runtime

Nodo:

```text
gaze_estimation.gaze_webcam_node
```

Il nodo:

1. apre la webcam con OpenCV (`cv2.VideoCapture(camera_index)`);
2. ridimensiona il frame se `resize_width > 0` (default: 640);
3. ogni `process_every_n_frames` (default: 2) esegue inferenza;
4. Gazelle/face detector trova facce e heatmap gaze;
5. YOLO-World rileva oggetti target;
6. sceglie la faccia piu' grande;
7. controlla `inout_score` (> 0.50 = guarda dentro l'immagine);
8. verifica se gaze point cade nella bounding box oggetto, con margine (30px);
9. opzionalmente controlla quanta massa della heatmap cade nella bbox (> 15%);
10. disegna overlay OpenCV.

Status possibili:

```text
LOOKING_AT_OBJECT: <class>
LOOKING_ELSEWHERE
NO_FACE
NO_TARGET_OBJECT
GAZE_OUT_OF_FRAME
NO_FRAME
```

Al momento lo status viene loggato e mostrato nella finestra OpenCV. Non risulta
un topic ROS pubblicato con lo status, quindi non e' ancora integrato con
EMPOWER o con la missione HRI.

### Parametri importanti

```yaml
camera_index: 0
device: "auto"
target_classes: ["book", "bottle", "cube", "sunglasses", "smartphone"]
resize_width: 640
process_every_n_frames: 2
yolo_confidence: 0.10
gaze_inout_threshold: 0.50
bbox_margin_px: 30
use_heatmap_ratio_check: true
heatmap_inside_bbox_threshold: 0.15
```

`device` puo' essere:

```text
auto
cpu
cuda
```

Se si chiede CUDA ma non e' disponibile, il codice fa fallback su CPU.

### Limitazioni attuali per l'integrazione con HRI

1. Non pubblica lo status su nessun topic ROS.
2. Usa `cv2.VideoCapture`, non un topic camera ROS.
3. Non ha consapevolezza della fase dell'interazione (non sottoscrive
   `/hri/dialog_state` o `/mission/state`).
4. Le classi target sono statiche e configurate nel YAML, non dinamiche in
   base all'attivita'.
5. Non c'e' modo di avviare/fermare la gaze estimation da un altro nodo.
6. In simulazione Gazebo, non esiste una camera che veda la faccia del bambino.

## Relazione fra le tre componenti

Stato attuale:

- `empower_ros2` e' la componente centrale robotica.
- `empower_hri` e' integrato con `empower_ros2` tramite mission orchestrator
  e topic `/hri/*`.
- `gaze_estimation` e' separato e non alimenta ancora decisioni EMPOWER/HRI.

Flusso integrato attuale HRI + EMPOWER (per la missione completa):

```text
terapista parla / scrive nel terminale
  -> empower_hri pubblica /hri/user_utterance (primo input: task fisico)
  -> mission_orchestrator cattura task, dice ack
  -> empower_hri pubblica /hri/user_utterance (secondo input: attivita' child)
  -> mission_orchestrator normalizza attivita', dice ack
  -> mission_orchestrator naviga allo spawn
  -> mission_orchestrator lancia scene capture
  -> mission_orchestrator lancia planning EMPOWER (relazioni + piano + detection)
  -> mission_orchestrator lancia execution EMPOWER (navigate + grab + drop)
  -> (se child_delivery_handled_by_empower=true, il piano EMPOWER naviga al bambino)
  -> mission_orchestrator pubblica child_ready
  -> mission_orchestrator invia start_child_activity a empower_hri
  -> empower_hri pronuncia frase di apertura
  -> empower_hri entra nel loop interattivo bambino (max 10 turni):
       bambino parla -> STT -> LLM genera risposta -> TTS pronuncia
  -> empower_hri pubblica dialog_complete
  -> mission_orchestrator completa la missione
```

Gaze estimation oggi non compare in questo flusso.

## Architettura topic durante l'interazione robot-bambino

Questo schema mostra i topic attivi durante la fase CHILD_ACTIVITY, che e' il
momento rilevante per la gaze estimation:

```text
mission_orchestrator                   voice_llm_node
     |                                      |
     |-- /mission/state: CHILD_ACTIVITY --> |
     |                                      |
     |   /hri/dialog_command: ---------->   |
     |   {start_child_activity, context}    |
     |                                      |
     |                         /hri/dialog_state: child_activity_active
     |                         /hri/robot_utterance: <apertura>
     |                                      |
     |                         (loop turni) |
     |                                      |
     |                         /hri/dialog_state: listening
     |                         <bambino parla>
     |                         /hri/dialog_state: recording
     |                         /hri/dialog_state: transcribing
     |                         /hri/user_utterance: <testo bambino>
     |                         /hri/dialog_state: thinking
     |                         /hri/dialog_state: speaking
     |                         /hri/robot_utterance: <risposta robot>
     |                         /hri/dialog_state: ready
     |                                      |
     |                         (fine loop)  |
     |                                      |
     |   <-- /hri/dialog_complete: -----    |
     |   {reason, turns, activity_type}     |
     |                                      |
     |-- /mission/state: MISSION_COMPLETE   |
```

Un nodo di gaze estimation integrato potrebbe sottoscrivere:

- `/mission/state` per sapere quando inizia/finisce l'interazione;
- `/hri/dialog_state` per sapere la fase del turno corrente;
- `/hri/robot_utterance` per sapere cosa dice il robot;
- Un topic camera (frontale TIAGo o webcam esterna) per il frame video.

E pubblicare:

- Un topic dedicato (es. `/gaze/status`) con lo stato gaze;
- Opzionalmente, un topic con metriche aggregate (tempo di attenzione, ecc.).

## Comandi principali

Build dei package principali:

```bash
cd /root/exchange
source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash
colcon build --symlink-install --packages-select empower_ros2 empower_hri gaze_estimation
source /root/exchange/install/setup.bash
```

Avvio Gazebo:

```bash
ros2 launch tiago_gazebo tiago_gazebo.launch.py \
  is_public_sim:=True \
  gazebo_version:=classic \
  world_name:=/root/exchange/exchange/person_table_child.world
```

Avvio Nav2 ground truth:

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

Execution:

```bash
ros2 launch empower_ros2 empower_execute.launch.py \
  params_file:=/root/exchange/src/empower_ros2/config/empower_person_table_child.yaml
```

Missione completa HRI + EMPOWER:

```bash
ros2 launch empower_ros2 hri_empower_mission.launch.py \
  hri_params_file:=/root/exchange/src/empower_hri/config/hri_cpu_english.yaml \
  hri_input_mode:=text
```

Gaze estimation:

```bash
ros2 launch gaze_estimation gaze_webcam.launch.py \
  camera_index:=0 \
  device:=auto \
  target_classes:="book,bottle,cup"
```

## Indicazioni pratiche per prossime modifiche

### Per integrare gaze estimation nell'interazione robot-bambino

Le modifiche con piu' impatto per integrare la gaze estimation sarebbero:

1. Far pubblicare al nodo `gaze_estimation` lo status su un topic ROS
   (es. `/gaze/status`), cosi' altri nodi possono reagire.

2. Far sottoscrivere al nodo gaze `/hri/dialog_state` e/o `/mission/state`,
   cosi' la gaze estimation sa quando l'interazione e' attiva e in che fase si
   trova.

3. Decidere la sorgente video: webcam esterna puntata sulla faccia del bambino
   (realistico per setup reale) oppure un topic camera ROS (necessita di una
   camera aggiuntiva in simulazione o nella configurazione reale del robot).

4. Adattare le classi target del gaze estimator in base al tipo di attivita':
   - `computer_emotion_recognition`: target = computer/laptop;
   - `mood_check_teddy`: target = teddy bear, robot;
   - `storytelling`: target = book, robot.

5. Creare un nodo integratore che combini gaze status e dialog state per
   produrre metriche di attenzione del bambino durante l'attivita'.

6. Opzionalmente, far usare le metriche gaze al nodo HRI per adattare il
   comportamento del robot (es. richiamare l'attenzione se il bambino non
   guarda).

### Per rendere EMPOWER piu' affidabile

1. Rendere configurabili modello OpenAI e prompt in YAML.
2. Sostituire l'output LLM libero con JSON strutturato per relazioni e piano.
3. Rendere robusto il parsing in `detection.py`, evitando split manuali su
   stringhe fragili.
4. Aggiungere alias oggetti, per esempio `laptop` == `computer`.
5. Separare target di navigazione astratti da oggetti fisici.
6. Migliorare `_resolve_target_marker()` per gestire target come "right side of
   the table".
7. Se serve manipolazione reale, introdurre un layer dedicato per MoveIt/gripper
   invece della simulazione Gazebo attuale.

## Prompt rapido da dare a ChatGPT

Se devo chiedere modifiche a un altro ChatGPT, posso incollare questo blocco:

```text
Sto lavorando sulla repo ElectiveAI, una workspace ROS 2 Humble per TIAGo in
Gazebo. Le componenti principali sono:

- empower_ros2: pipeline EMPOWER per capture RGB-D, GPT-4o vision planning,
  YOLO-World detection, EfficientViT-SAM segmentation, grounding, report,
  marker 3D, Nav2 execution, simulazione GRAB/DROP via CLI Gazebo e mission
  orchestrator.
- empower_hri: dialogo locale STT/LLM/TTS con faster-whisper, Ollama/Qwen e TTS
  locale. Si integra con EMPOWER tramite topic /hri/* e mission_orchestrator.
  Supporta 3 attivita' bambino (storytelling, computer_emotion_recognition,
  mood_check_teddy) con system prompt dedicati e frase di apertura.
- gaze_estimation: package standalone webcam + Gazelle/Gaze-LLE + YOLO-World per
  stimare se una persona guarda un oggetto. Non e' ancora integrato con EMPOWER.

Il flusso missione e':
1. TIAGo va dal terapista, riceve task fisico e tipo di attivita' child
2. TIAGo va allo spawn, EMPOWER cattura scena, pianifica, esegue (navigate,
   grab, drop simulati)
3. TIAGo arriva dal bambino, HRI apre l'attivita' child con frase di apertura
4. Loop interattivo: bambino parla -> STT -> LLM -> TTS (max 10 turni)
5. Missione completa

Topic chiave durante l'interazione child:
  /mission/state (CHILD_ACTIVITY)
  /hri/dialog_state (child_activity_active, listening, recording, transcribing,
                     thinking, speaking, ready)
  /hri/robot_utterance (risposte robot)
  /hri/dialog_complete (fine attivita')

Obiettivo: integrare gaze estimation durante l'interazione robot-bambino.
La gaze estimation deve sapere quando l'attivita' e' in corso, in che fase del
turno si trova, e pubblicare lo status su un topic ROS.
```
