# empower_hri

`empower_hri` è un MVP ROS2 Humble per verificare localmente il ciclo:

```text
microfono o terminale -> STT -> LLM -> TTS -> speaker del computer
```

Il package è isolato dalla pipeline EMPOWER/Nav2: non usa navigazione, memoria
persistente, riconoscimento utenti, braccio o altre azioni robotiche. Tutta
l'elaborazione è locale e CPU-only; non usa servizi cloud, API key, GPU o CUDA.

## Interfacce ROS2

Il nodo `voice_llm_node` pubblica:

- `/hri/user_utterance` (`std_msgs/String`)
- `/hri/robot_utterance` (`std_msgs/String`)
- `/hri/dialog_state` (`std_msgs/String`)

Gli stati principali sono `ready`, `waiting_for_text`,
`waiting_for_push_to_talk`, `recording`, `transcribing`, `thinking`,
`speaking`, `error` e `stopped`.

Digitare `/quit` per terminare il nodo.

## Build

Da workspace ROS2:

```bash
source /opt/ros/humble/setup.bash
cd /home/samuele/ElectiveAI
colcon build --symlink-install --packages-select empower_hri
source install/setup.bash
```

## Dipendenze locali

Il test mock richiede solo ROS2, `rclpy` e `std_msgs`. Installare le altre
dipendenze in base ai provider che si vogliono provare.

### Ollama

Installare Ollama seguendo le istruzioni per il proprio sistema operativo,
quindi avviare il server e scaricare un modello piccolo:

```bash
ollama serve
ollama pull qwen2.5:1.5b
```

Modello fallback opzionale:

```bash
ollama pull llama3.2:1b
```

Il nodo usa soltanto l'endpoint locale predefinito
`http://localhost:11434`.

### Speech-to-text e registrazione

```bash
python3 -m pip install faster-whisper sounddevice soundfile
```

Su Ubuntu possono servire anche le librerie audio di sistema:

```bash
sudo apt update
sudo apt install libportaudio2 libsndfile1
```

`faster-whisper` scarica il modello quando viene caricato per la prima volta
se non è già nella cache. Per rendere esplicito il download e farlo prima del
test:

```bash
python3 -c "from faster_whisper import WhisperModel; WhisperModel('tiny', device='cpu', compute_type='int8')"
```

Per precaricare il modello alternativo:

```bash
python3 -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')"
```

Il provider STT rifiuta configurazioni diverse da `stt_device:=cpu`.

### Text-to-speech

Provider Python predefinito:

```bash
python3 -m pip install pyttsx3
sudo apt install espeak-ng
```

Il provider `system` usa `espeak-ng`, `espeak` oppure `spd-say`, in
quest'ordine. Ogni comando ha un timeout configurabile con `tts_timeout`
per evitare che un backend audio blocchi il ciclo. Per
installarli su Ubuntu:

```bash
sudo apt install espeak speech-dispatcher
```

Piper è opzionale. Installare il comando `piper`, scaricare separatamente una
voce italiana `.onnx` e il relativo `.onnx.json`, poi impostare:

```bash
tts_provider:=piper \
piper_model_path:=/percorso/voce-it.onnx \
piper_config_path:=/percorso/voce-it.onnx.json
```

Se Piper o la voce non sono disponibili, il nodo prova `pyttsx3`, poi i
comandi di sistema e infine stampa la risposta.

## Test progressivi

### 1. Tutto mock, senza audio e senza Ollama

```bash
ros2 launch empower_hri hri_voice_llm_cpu.launch.py \
  input_mode:=text \
  llm_provider:=mock \
  tts_provider:=mock
```

Scrivendo `ciao robot`, il nodo pubblica la frase utente, genera una risposta
mock, pubblica la risposta e la mostra nel terminale.

### 2. Ollama con input da tastiera e output stampato

In un terminale:

```bash
ollama serve
```

In un altro:

```bash
ros2 launch empower_hri hri_voice_llm_cpu.launch.py \
  input_mode:=text \
  llm_provider:=ollama \
  llm_model:=qwen2.5:1.5b \
  tts_provider:=mock
```

### 3. Ollama e voce in uscita

```bash
ros2 launch empower_hri hri_voice_llm_cpu.launch.py \
  input_mode:=text \
  llm_provider:=ollama \
  llm_model:=qwen2.5:1.5b \
  tts_provider:=pyttsx3
```

### 4. STT simulato, Ollama e TTS

La modalità `voice` mantiene il flusso push-to-talk, ma il provider STT mock
chiede di digitare manualmente la trascrizione:

```bash
ros2 launch empower_hri hri_voice_llm_cpu.launch.py \
  input_mode:=voice \
  stt_provider:=mock \
  llm_provider:=ollama \
  tts_provider:=pyttsx3
```

### 5. Ciclo completo locale CPU

```bash
ros2 launch empower_hri hri_voice_llm_cpu.launch.py \
  input_mode:=voice \
  stt_provider:=faster_whisper \
  stt_model:=tiny \
  stt_device:=cpu \
  stt_compute_type:=int8 \
  llm_provider:=ollama \
  llm_model:=qwen2.5:1.5b \
  tts_provider:=pyttsx3
```

Premere INVIO quando richiesto. Il nodo registra per 4 secondi, trascrive in
italiano, interroga Ollama e riproduce la risposta dagli speaker dell'host.

### 6. Profilo completo in inglese con Qwen

Il profilo `hri_cpu_english.yaml` mantiene Qwen `qwen2.5:1.5b`, usa Whisper
`base` per la trascrizione inglese e seleziona la voce inglese del TTS di
sistema:

```bash
ros2 launch empower_hri hri_voice_llm_english.launch.py
```

Per verificare prima il prompt inglese senza microfono né speaker:

```bash
ros2 launch empower_hri hri_voice_llm_english.launch.py \
  input_mode:=text \
  llm_provider:=ollama \
  tts_provider:=mock
```

Il profilo italiano rimane disponibile tramite
`hri_voice_llm_cpu.launch.py`.

Per osservare i topic:

```bash
ros2 topic echo /hri/user_utterance
ros2 topic echo /hri/robot_utterance
ros2 topic echo /hri/dialog_state
```

## Parametri principali

La configurazione predefinita è in `config/hri_cpu_local.yaml`. I parametri
più comuni possono essere sovrascritti dal launch:

- `input_mode`: `text` o `voice`
- `llm_provider`: `ollama` o `mock`
- `llm_base_url`, `llm_model`, `llm_fallback_model`, `llm_timeout`
- `temperature`, `max_response_tokens`, `max_history_turns`
- `stt_provider`: `faster_whisper` o `mock`
- `stt_model`: `tiny` o, opzionalmente, `base`
- `stt_device:=cpu`, `stt_compute_type:=int8`, `language`
- `record_seconds`, `sample_rate`
- `tts_provider`: `piper`, `pyttsx3`, `system` o `mock`
- `tts_timeout`: attesa massima del backend TTS, predefinita a 30 secondi
- `piper_model_path`, `piper_config_path`

Per cambiare `system_prompt` o altri valori si può creare un file YAML
derivato e passarlo con `params_file:=/percorso/config.yaml`.

La history è mantenuta solo in RAM e limitata a `max_history_turns`.

## Docker e audio

La prima prova con microfono e speaker è consigliata direttamente sull'host.
PulseAudio/ALSA e i device audio richiedono configurazioni aggiuntive dentro
Docker. Gazebo ed EMPOWER possono restare nel container mentre
`voice_llm_node` gira sull'host.

Host e container devono usare lo stesso dominio ROS2, per esempio:

```bash
export ROS_DOMAIN_ID=0
```

Impostare lo stesso valore in entrambi gli ambienti.

## Risoluzione problemi

- `Impossibile contattare Ollama`: eseguire `ollama serve` sull'host.
- `modello non sembra disponibile`: eseguire
  `ollama pull qwen2.5:1.5b`.
- `faster-whisper non è installato`: installarlo con `pip` oppure usare
  `stt_provider:=mock`.
- `Microfono non disponibile`: controllare il device audio e provare
  `input_mode:=text`.
- Nessuna voce udibile: provare `tts_provider:=system` oppure
  `tts_provider:=mock` per verificare il resto della pipeline.
