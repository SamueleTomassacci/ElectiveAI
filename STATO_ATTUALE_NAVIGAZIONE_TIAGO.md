# Stato attuale della navigazione TIAGo

Questo documento riassume lo stato attuale della repo ElectiveAI con focus
sulla navigazione del robot TIAGo nella simulazione Gazebo. Il sistema completo
e' composto da tre blocchi principali:

- `empower`: planning simbolico, detection/grounding, capture RGB-D, navigazione
  Nav2 ed esecuzione low-level del piano.
- `gaze_estimation`: stima dell'attenzione del bambino durante la fase finale
  dell'interazione.
- `llm_interaction`: interazione vocale/testuale con terapista e bambino,
  usando STT, LLM locale via Ollama e TTS.

La navigazione vera e propria vive quasi tutta in `empower`: gli altri blocchi
servono a raccogliere istruzioni, monitorare attenzione e guidare il dialogo,
ma non decidono direttamente le pose Nav2.

## Scena, mappa e frame

La simulazione usa una sola scena principale:

- world Gazebo: `exchange/scene.world`
- mappa Nav2: `exchange/maps/scene.yaml`
- configurazione EMPOWER: `src/empower/config/empower_config.yaml`
- configurazione missione HRI: `src/empower/config/task_config.yaml`
- scene graph statico: `src/empower/config/scenes/therapy_room_scene_graph.json`

La mappa statica e' caricata nel frame `map`. Il robot viene comandato nel
frame `base_footprint`, mentre Nav2 riceve goal `NavigateToPose` nel frame
`map`.

Il bringup di Nav2 e':

```bash
ros2 launch empower tiago_nav2_ground_truth.launch.py \
  map:=/root/exchange/exchange/maps/scene.yaml
```

Questo launch avvia:

- `nav2_map_server`, che carica la mappa statica;
- `nav2_lifecycle_manager`, che porta online il map server;
- `ground_truth_localizer_node`, che pubblica localizzazione da ground truth;
- `nav2_bringup/launch/navigation_launch.py`, che avvia planner, controller,
  behavior server, BT navigator e costmap.

In questo MVP non viene usato AMCL: la posa del robot arriva da
`/ground_truth_odom` tramite `ground_truth_localizer_node`. Questo rende la
navigazione piu' stabile in Gazebo, perche' Nav2 lavora con una localizzazione
perfetta o quasi perfetta.

## Static Scene Graph

Tutte le pose di navigazione (missione HRI, target non visuali, waypoint,
drop surfaces) sono definite in un unico file JSON, il **scene graph statico**:

```text
src/empower/config/scenes/therapy_room_scene_graph.json
```

La classe che lo gestisce e':

```text
src/empower/python/empower/static_scene_graph.py
```

### Struttura del scene graph

Il file JSON contiene tre sezioni:

- `scene`: metadati (`id`, `frame_id`, `map`, `description`). Il `frame_id`
  e' `map` e viene usato da tutti i nodi come frame di riferimento.
- `nodes`: dizionario di nodi, ognuno con un ID univoco.
- `edges`: lista di triple semantiche `(subject, predicate, object)`.

### Tipi di nodo

| Tipo | Descrizione | Campi caratteristici |
| --- | --- | --- |
| `room` | La stanza. Solo label e alias. | `label`, `aliases` |
| `zone` | Zona con una posa raggiungibile. | `pose`, `roles`, `aliases` |
| `person` | Persona nella scena (non navigabile direttamente). | `aliases` |
| `nav_pose` | Posa di navigazione associata a un target. | `pose`, `target`, `roles`, `via` |
| `waypoint` | Punto intermedio lungo un percorso. | `pose`, `roles` |
| `surface` | Superficie su cui fare drop. | `drop_pose`, `roles` |

Ogni nodo puo' avere:

- `type`: stringa obbligatoria.
- `label`: etichetta leggibile.
- `aliases`: lista di nomi alternativi per la risoluzione.
- `pose`: dizionario `{x, y, yaw}` per pose di navigazione.
- `drop_pose`: dizionario `{x, y, z, yaw}` per pose di rilascio oggetti.
- `roles`: lista di ruoli semantici (usati per filtrare nella risoluzione).
- `target`: ID del nodo persona/entita' a cui la nav_pose si riferisce.
- `via`: lista di ID di waypoint da percorrere prima di raggiungere la posa.

### Nodi attuali

```text
room_therapy        type=room     label="therapy room"

zone_center         type=zone     pose={x:4.8, y:0.0, yaw:1.72}
                    roles=[capture_origin, mission_reset]
                    aliases=[center, room center, spawn, capture origin]

therapist           type=person
                    aliases=[therapist, person, the person, adult,
                             the adult, standing person, adult person]

therapist_interaction_pose
                    type=nav_pose target=therapist
                    pose={x:0.0, y:-1.3, yaw:-1.5708}
                    roles=[receive_instruction, ask_activity]

child               type=person
                    aliases=[child, the child, kid]

child_delivery_pose type=nav_pose target=child
                    pose={x:0.0, y:0.95, yaw:1.5708}
                    roles=[deliver_object, start_child_interaction]
                    via=[child_approach_waypoint]

child_approach_waypoint
                    type=waypoint
                    pose={x:3.0, y:0.0, yaw:3.14159}
                    roles=[child_approach]

child_delivery_table
                    type=surface
                    drop_pose={x:0.0, y:1.65, z:0.65, yaw:1.5708}
                    roles=[child_delivery_drop]
```

### Edges attuali

```text
therapist                located_in      room_therapy
child                    located_in      room_therapy
zone_center              located_in      room_therapy
therapist_interaction_pose  approaches   therapist
child_delivery_pose      approaches      child
child_delivery_pose      reachable_via   child_approach_waypoint
child_delivery_table     used_for        child_delivery
```

### API di risoluzione

La classe `StaticSceneGraph` offre questi metodi principali:

- `resolve_nav_pose(target, role)` -> `{id, x, y, yaw}` o `None`.
  Cerca prima un nodo diretto con posa, poi i nodi `nav_pose` il cui `target`
  corrisponde. Se `role` e' specificato, filtra anche per ruolo.
- `resolve_waypoints_for_nav_pose(target, role)` -> lista di `{id, x, y, yaw}`.
  Restituisce i waypoint intermedi (`via`) della nav_pose trovata.
- `resolve_drop_pose(role)` -> `{id, x, y, z, yaw}` o `None`.
  Cerca nodi con `drop_pose` e ruolo corrispondente.
- `resolve_node(name, expected_type)` -> nodo completo o `None`.
- `is_alias_of(alias, canonical)` -> `bool`.
  Controlla se `alias` si riferisce allo stesso nodo di `canonical`.
- `aliases_for(name)` -> lista di stringhe.

La normalizzazione degli alias e' case-insensitive, rimuove articoli
(`the`, `a`, `an`), e sostituisce caratteri non alfanumerici con spazi.

### Funzione default_scene_graph_path

La funzione `default_scene_graph_path()` in `static_scene_graph.py` cerca
il file del scene graph in due modi:

1. Prima prova `ament_index_python` per trovare il path nella share directory
   del pacchetto `empower`.
2. Se fallisce, usa un path relativo dalla posizione del modulo Python.

## Flow di missione

Il flow completo HRI + EMPOWER e' orchestrato da:

```text
src/empower/python/empower/mission_orchestrator_node.py
```

Il launch principale e':

```bash
ros2 launch empower hri_empower_mission.launch.py
```

La sequenza attuale degli stati e' "child-first": il nome del bambino e'
il PRIMO input richiesto. Se esiste uno storico di punteggi sessione per
quel bambino (in `session_scores.json`), il robot propone automaticamente
l'attivita' con il miglior punteggio medio; altrimenti chiede task e
attivita' manualmente.

Flusso con storico:

1. **GO_TO_THERAPIST** - TIAGo naviga verso la terapeuta.
2. **WAIT_CHILD_NAME** - aspetta il nome del bambino dalla terapeuta su
   `/hri/user_utterance`.
3. **ACK_CHILD_NAME** - conferma il nome ricevuto.
4. **PROPOSE_ACTIVITY** - chiama `best_activity_for_child()` dal modulo
   `session_scores` e propone l'attivita' migliore. Il task EMPOWER
   viene derivato dal mapping `ACTIVITY_TO_TASK`.
5. **WAIT_THERAPIST_OVERRIDE** - attende
   `proposal_acceptance_timeout_sec` (default 15s). Se la terapeuta parla,
   l'input sovrascrive la proposta; se il timeout scade, la proposta viene
   accettata automaticamente.
6. **ACK_PROPOSAL** - conferma l'attivita' scelta.
7. **GO_TO_SPAWN** - torna al centro stanza per la capture EMPOWER.
8. **RUN_EMPOWER** - esegue capture, planning ed esecuzione del piano EMPOWER.
9. **GO_TO_CHILD** - naviga verso il bambino (solo se
    `child_delivery_handled_by_empower` e' false).
10. **CHILD_READY** - pubblica `child_ready` e avvia il dialogo con il bambino.
11. Avvia `gaze_estimation` per monitorare l'attenzione.
12. **CHILD_ACTIVITY** - gestisce l'attivita' con il bambino (storytelling, ecc.).
13. **GO_BACK_TO_THERAPIST** - il robot torna dalla terapeuta e pronuncia
    un riepilogo dell'attivita' con i punteggi sessione.
14. **MISSION_COMPLETE** - missione completata.

Se non c'e' storico per il bambino, dopo ACK_CHILD_NAME il flusso passa
per gli stati WAIT_TASK -> ACK_TASK -> WAIT_ACTIVITY -> ACK_ACTIVITY prima
di GO_TO_SPAWN (fallback al flusso manuale).

Nel file corrente `task_config.yaml` il parametro:

```yaml
child_delivery_handled_by_empower: true
```

significa che l'orchestratore non esegue il proprio `GO_TO_CHILD` hardcoded
dopo EMPOWER. Ci si aspetta invece che il piano generato da EMPOWER includa
una navigazione verso il bambino, per esempio `NAVIGATE to child`.

## Come l'orchestratore risolve le pose

L'orchestratore non legge piu' `therapist_pose`, `spawn_pose`, `child_pose`
da `task_config.yaml`. Al loro posto usa il `StaticSceneGraph`:

```python
self.static_scene_graph = StaticSceneGraph.from_json(scene_graph_path)
```

La funzione `static_nav_pose(target, role)` wrappa la risoluzione e solleva
`RuntimeError` se nessuna posa viene trovata:

| Destinazione | Chiamata | Posa risultante | Usata in |
| --- | --- | --- | --- |
| Terapeuta | `resolve_nav_pose("therapist", role="receive_instruction")` | `[0.0, -1.3, -1.5708]` | GO_TO_THERAPIST, GO_BACK_TO_THERAPIST |
| Centro stanza | `resolve_nav_pose("center", role="capture_origin")` | `[4.8, 0.0, 1.72]` | GO_TO_SPAWN |
| Bambino | `resolve_nav_pose("child", role="deliver_object")` | `[0.0, 0.95, 1.5708]` | GO_TO_CHILD |

Il path del scene graph e' configurato in `task_config.yaml`:

```yaml
static_scene_graph_path: "/root/exchange/src/empower/config/scenes/therapy_room_scene_graph.json"
```

## task_config.yaml attuale

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

Da notare che **non ci sono piu'** i campi `therapist_pose`, `spawn_pose`,
`child_pose`. Tutte le pose sono nel scene graph JSON.

Parametri nuovi rispetto alla versione precedente:

- `session_scores_path`: path del file JSON dei punteggi sessione.
- `proposal_acceptance_timeout_sec`: timeout per l'accettazione automatica
  della proposta di attivita'.
- `child_name_ask_text`: testo per chiedere il nome del bambino.
- `no_history_text`: testo se non c'e' storico sessione.
- `proposal_text`: template per la proposta (`{activity}` viene sostituito).

## Come il low_level_execution_node risolve i target

Il file:

```text
src/empower/python/empower/low_level_execution_node.py
```

usa anch'esso `StaticSceneGraph` per risolvere target e pose.

### Risoluzione alias

Per determinare se un target nel piano EMPOWER si riferisce al bambino o alla
terapeuta, il nodo usa:

```python
self.static_scene_graph.is_alias_of(label, "child")
self.static_scene_graph.is_alias_of(label, "therapist")
```

Questo significa che qualsiasi alias configurato nel scene graph (per esempio
`"kid"`, `"the child"`, `"person"`, `"the adult"`) viene riconosciuto
automaticamente. Non serve piu' mantenere liste di alias separate in
`empower_config.yaml`.

### Risoluzione pose di navigazione

Quando il piano contiene `NAVIGATE to child`, il nodo chiede al scene graph:

```python
self.static_scene_graph.resolve_nav_pose("child", role="deliver_object")
# -> {id: "child_delivery_pose", x: 0.0, y: 0.95, yaw: 1.5708}
```

### Risoluzione waypoint

Se la nav_pose ha waypoint intermedi (`via`), vengono risolti con:

```python
self.static_scene_graph.resolve_waypoints_for_nav_pose("child", role="deliver_object")
# -> [{id: "child_approach_waypoint", x: 3.0, y: 0.0, yaw: 3.14159}]
```

Il robot naviga prima ai waypoint in ordine, poi alla posa finale. Per il
bambino, il percorso e':

```text
waypoint: (3.0, 0.0, 3.14159)   ->   goal: (0.0, 0.95, 1.5708)
```

### Risoluzione posa di drop

Per la consegna al bambino, la posa di drop sul tavolo viene da:

```python
self.static_scene_graph.resolve_drop_pose(role="child_delivery_drop")
# -> {id: "child_delivery_table", x: 0.0, y: 1.65, z: 0.65, yaw: 1.5708}
```

### Navigazione verso la terapeuta

Se il piano contiene `NAVIGATE to person` o `NAVIGATE to therapist`:

```python
self.static_scene_graph.resolve_nav_pose("therapist", role=None)
# -> {id: "therapist_interaction_pose", x: 0.0, y: -1.3, yaw: -1.5708}
```

### Frame ID

Il frame di riferimento per tutte le pose viene dal scene graph:

```python
self.static_scene_graph.frame_id  # -> "map"
```

### Parametro scene graph

Il parametro `static_scene_graph_path` nel low_level_execution_node viene
dichiarato con `default_scene_graph_path()`, che prova prima ament_index
e poi un path relativo come fallback.

## Navigazione verso oggetti visuali

Per target visuali come tavolo, libro, laptop o peluche, il robot non usa una
posa fissa. La posa viene ricavata dalla detection EMPOWER.

Il caso multi-view e' quello attualmente abilitato:

```yaml
multi_view:
  enabled: true
```

Il nodo `multi_view_capture_node` ruota TIAGo sul posto usando Nav2. Per ogni
vista salva RGB, depth, camera info e posa della camera in `map`. Il manifest
principale e':

```text
output/empower_output/therapy_task/views_manifest.json
```

Poi `multi_view_aggregator` unisce le detection delle varie viste e produce:

```text
output/empower_output/therapy_task/global_detections.json
output/empower_output/therapy_task/global_scene_graph.json
output/empower_output/therapy_task/global_scene_description.txt
```

Dentro `global_detections.json`, ogni oggetto ha un `centroid_map`, cioe' un
centroide 3D espresso in frame `map`. Durante l'esecuzione del piano, il
low-level executor cerca il target richiesto nel file `global_detections.json`.
Se lo trova, costruisce un goal Nav2 non esattamente sul centroide, ma a una
distanza di sicurezza.

La funzione chiave e':

```text
src/empower/python/empower/primitive_actions.py
make_nav_goal_pose()
```

La logica e':

1. prendi il centroide dell'oggetto in `map`;
2. leggi la posa corrente del robot da TF;
3. calcola la direzione oggetto -> robot;
4. posiziona il goal a `standoff_distance` dal centroide, lungo quella
   direzione;
5. orienta il robot verso l'oggetto.

Il parametro corrente e':

```yaml
standoff_distance: 1.2
```

Quindi, se il target e' un oggetto sopra un tavolo, TIAGo non prova ad andare
nel centro del tavolo: si ferma circa 1.2 m prima e guarda verso il target.

Se `multi_view.enabled` fosse false, la navigazione verso oggetti visuali
passerebbe dai marker `/pcl_centroids` e `/pcl_names`, pubblicati a partire
dalla point cloud single-view. La logica finale resta la stessa: centroide
target piu' `standoff_distance`.

## Manipolazione simulata e drop vicino al bambino

La manipolazione fisica reale non e' implementata: non ci sono MoveIt, gripper
o controllo braccio. Pero' `low_level_execution_node.py` simula `GRAB` e `DROP`
spostando modelli Gazebo.

Oggetti supportati (definiti in `empower_config.yaml` sotto `object_simulation.objects`):

- `book` -> `clear_red_book_on_right_table`
- `computer` / `laptop` -> `laptop_computer_on_right_table`
- `peluche` / `teddy bear` -> `teddy_bear_on_right_table`

Quando il robot fa `GRAB`, il modello viene rimosso da Gazebo (spostato in una
posizione nascosta). Quando fa `DROP`, viene respawnato. Se l'ultima navigazione
riuscita era verso il bambino, il drop usa la posa dal scene graph:

```python
self.static_scene_graph.resolve_drop_pose(role="child_delivery_drop")
# -> {x: 0.0, y: 1.65, z: 0.65, yaw: 1.5708}
```

Questa posa corrisponde al nodo `child_delivery_table` nel scene graph.
Non ci sono piu' campi `child_delivery_table_drop_*` in `empower_config.yaml`:
le pose di drop sono interamente nel scene graph.

La simulazione oggetti usa anche i seguenti parametri da `empower_config.yaml`:

```yaml
object_simulation:
  world_frame: world
  robot_base_frame: base_footprint
  gz_command: gz
  gz_world_name: scene
  gz_cli_timeout_sec: 10.0
  world_sdf_path: /root/exchange/exchange/scene.world
```

## empower_config.yaml attuale

Il file di configurazione EMPOWER non contiene piu' le sezioni
`child_navigation` e `person_navigation`. Le pose dei target non visuali sono
migrate nel scene graph. Il path del scene graph e' indicato nel file stesso:

```yaml
static_scene_graph_path: /root/exchange/src/empower/config/scenes/therapy_room_scene_graph.json
```

Contenuto completo:

```yaml
/**:
  ros__parameters:
    use_case: therapy_task
    task_description: "Take the teddy bear, go to the child and drop the teddy bear"
    output_root: /root/exchange/output
    rgb_topic: /head_front_camera/rgb/image_raw
    depth_topic: /head_front_camera/depth/image_raw
    camera_info_topic: /head_front_camera/depth/camera_info
    pointcloud_topic: /empower/colored_pointcloud
    fixed_frame: map
    camera_frame: head_front_camera_color_optical_frame
    depth_unit_scale_to_mm: -1.0
    multi_view:
      enabled: true
      overlap_ratio: 0.15
      yaw_coverage: 6.283185307179586
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
        min_detection_confidence: 0.15
    server_host: 127.0.0.1
    server_port: 10000
    openai_api_key_file: /root/exchange/openai_api_key.txt
    centroids_topic: /pcl_centroids
    names_topic: /pcl_names
    map_frame: map
    static_scene_graph_path: /root/exchange/src/empower/config/scenes/therapy_room_scene_graph.json
    base_frame: base_footprint
    nav_action_name: navigate_to_pose
    marker_timeout_sec: 30.0
    tf_timeout_sec: 10.0
    action_server_timeout_sec: 20.0
    nav_result_timeout_sec: 300.0
    standoff_distance: 1.2
    continue_on_nav_failure: false
    child_context:
      enabled: true
      planner_description: >
        The child is present in the room but is not visible in the RGB image.
        ...
      aliases: ["child", "the child", "kid"]
    object_simulation:
      enabled: true
      drop_distance: 0.75
      hidden_x: 0.0
      hidden_y: 0.0
      hidden_z: -10.0
      world_frame: world
      robot_base_frame: base_footprint
      gz_command: gz
      gz_world_name: scene
      gz_cli_timeout_sec: 10.0
      world_sdf_path: /root/exchange/exchange/scene.world
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

## Dove modificare le pose

Tutte le pose di navigazione sono ora centralizzate nel scene graph:

```text
src/empower/config/scenes/therapy_room_scene_graph.json
```

### Per cambiare la posa davanti alla terapeuta

Modifica il nodo `therapist_interaction_pose`:

```json
"therapist_interaction_pose": {
  "type": "nav_pose",
  "target": "therapist",
  "pose": { "x": 0.0, "y": -1.3, "yaw": -1.5708 },
  "roles": ["receive_instruction", "ask_activity"]
}
```

### Per cambiare la posa centrale / spawn

Modifica il nodo `zone_center`:

```json
"zone_center": {
  "type": "zone",
  "pose": { "x": 4.8, "y": 0.0, "yaw": 1.72 },
  "roles": ["capture_origin", "mission_reset"]
}
```

### Per cambiare la posa vicino al bambino

Modifica il nodo `child_delivery_pose`:

```json
"child_delivery_pose": {
  "type": "nav_pose",
  "target": "child",
  "pose": { "x": 0.0, "y": 0.95, "yaw": 1.5708 },
  "roles": ["deliver_object", "start_child_interaction"],
  "via": ["child_approach_waypoint"]
}
```

### Per cambiare il waypoint intermedio verso il bambino

Modifica il nodo `child_approach_waypoint`:

```json
"child_approach_waypoint": {
  "type": "waypoint",
  "pose": { "x": 3.0, "y": 0.0, "yaw": 3.14159 }
}
```

Per rimuovere il waypoint, togliere `"via": ["child_approach_waypoint"]` dal
nodo `child_delivery_pose`.

### Per cambiare la posa di drop al tavolo del bambino

Modifica il nodo `child_delivery_table`:

```json
"child_delivery_table": {
  "type": "surface",
  "drop_pose": { "x": 0.0, "y": 1.65, "z": 0.65, "yaw": 1.5708 },
  "roles": ["child_delivery_drop"]
}
```

### Per cambiare gli alias di una persona

Aggiungi o modifica la lista `aliases` nel nodo corrispondente. Per esempio,
per aggiungere `"dottore"` come alias della terapeuta:

```json
"therapist": {
  "type": "person",
  "aliases": ["therapist", "person", "the person", "adult", ..., "dottore"]
}
```

### Per cambiare la standoff distance degli oggetti visuali

Modifica in `empower_config.yaml`:

```yaml
standoff_distance: 1.2
```

### Formato delle coordinate

Il formato pose e' sempre:

```text
{x, y, yaw}        per nav_pose, zone, waypoint
{x, y, z, yaw}     per drop_pose (superfici)
```

dove `x` e `y` sono coordinate in `map`, `z` e' l'altezza in metri, e `yaw`
e' l'orientamento del robot in radianti:

- `yaw = 0.0`: robot orientato verso `+X`;
- `yaw = 1.5708`: robot orientato circa verso `+Y`;
- `yaw = -1.5708`: robot orientato circa verso `-Y`;
- `yaw = 3.14159`: robot orientato circa verso `-X`.

## Launch files

I launch file EMPOWER sono attualmente tre:

| File | Funzione |
| --- | --- |
| `empower_plan.launch.py` | Avvia il planning EMPOWER standalone. |
| `hri_empower_mission.launch.py` | Avvia la missione HRI completa con orchestratore. |
| `tiago_nav2_ground_truth.launch.py` | Bringup Nav2 con localizzazione ground truth. |

I nodi di capture, models_cacher, visualization ed execute **non hanno piu'
launch file separati**. Vengono lanciati come sottoprocessi dall'orchestratore
durante la fase `RUN_EMPOWER`.

## Limiti attuali

- La navigazione e' basata su ground truth, non su localizzazione realistica con
  AMCL.
- I target non visuali (`child`, `therapist`) sono pose statiche nel scene
  graph, non vengono stimati dinamicamente dalla percezione.
- Gli oggetti visuali dipendono dalla qualita' di detection, depth e fusione
  multi-view.
- La manipolazione e' simulata via Gazebo CLI: `GRAB` e `DROP` non muovono il
  braccio reale del robot.

## File principali da guardare

- `src/empower/config/scenes/therapy_room_scene_graph.json`: scene graph con
  tutte le pose di navigazione, waypoint, drop surfaces e alias.
- `src/empower/python/empower/static_scene_graph.py`: classe `StaticSceneGraph`
  che carica e risolve il scene graph.
- `src/empower/config/task_config.yaml`: parametri missione HRI (timeout, testi
  di conferma, path scene graph).
- `src/empower/config/empower_config.yaml`: Nav2, multi-view, object simulation
  e parametri EMPOWER generali.
- `src/empower/python/empower/mission_orchestrator_node.py`: sequenza missione e
  navigazione verso terapeuta/spawn/bambino via scene graph.
- `src/empower/python/empower/low_level_execution_node.py`: esecuzione del piano
  EMPOWER, risoluzione target, waypoint e drop pose via scene graph.
- `src/empower/python/empower/primitive_actions.py`: invio goal a Nav2 e calcolo
  della posa di avvicinamento agli oggetti.
- `src/empower/python/empower/multi_view_capture_node.py`: rotazione sul posto e
  salvataggio delle viste RGB-D.
- `src/empower/python/empower/multi_view_aggregator.py`: costruzione dei
  centroidi globali in `map`.
- `src/empower/launch/tiago_nav2_ground_truth.launch.py`: bringup Nav2 con
  localizzazione ground truth.
- `src/empower/config/nav2_tiago_ground_truth.yaml`: parametri planner,
  controller, costmap e goal tolerance.
