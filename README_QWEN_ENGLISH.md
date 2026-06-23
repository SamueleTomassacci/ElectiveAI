# Qwen voice dialogue

Questo README contiene la sequenza operativa per avviare il dialogo vocale con
Qwen dentro il container Docker della repo.

Flusso inglese:

```text
microfono -> Whisper base -> Qwen -> espeak-ng -> speaker
```

Il modulo ROS2 usato e' `llm_interaction`. Il modello LLM e' servito da Ollama
sull'host, mentre ROS2, microfono, trascrizione e TTS girano dentro il
container `hrai_sim`.

## 1. Host: prepara Ollama

```bash
ollama serve
```

In un secondo terminale host:

```bash
ollama pull qwen2.5:1.5b
```

## 2. Host: avvia il container con audio

```bash
cd /home/samuele/ElectiveAI

docker rm -f hrai_sim 2>/dev/null || true

HOST_UID=$(id -u)
PULSE_SOCKET="/run/user/${HOST_UID}/pulse/native"
PULSE_COOKIE="${HOME}/.config/pulse/cookie"

docker run \
  --name hrai_sim \
  --rm \
  -it \
  --net host \
  --privileged \
  --device /dev/snd \
  -e PULSE_SERVER="unix:${PULSE_SOCKET}" \
  -v "${PULSE_SOCKET}:${PULSE_SOCKET}" \
  -v "${PULSE_COOKIE}:/root/.config/pulse/cookie:ro" \
  -v /home/samuele/ElectiveAI:/root/exchange \
  -w /root/exchange \
  hrai-2025:v1 \
  /root/exchange/docker/entrypoint_tiago.sh
```

## 3. Container: installa le dipendenze

```bash
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

## 4. Container: configura microfono e speaker

Il microfono funzionante e' `hw:0,7`.

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

## 5. Container: scarica Whisper

Profilo inglese:

```bash
python3 -c \
  "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')"
```

Profilo italiano leggero:

```bash
python3 -c \
  "from faster_whisper import WhisperModel; WhisperModel('tiny', device='cpu', compute_type='int8')"
```

## 6. Container: build ROS2

```bash
cd /root/exchange

source /opt/ros/humble/setup.bash
source /root/tiago_public_ws/install/setup.bash

colcon build --symlink-install --packages-select llm_interaction

source /root/exchange/install/setup.bash
```

## 7. Container: pipeline completa inglese

```bash
ros2 run llm_interaction voice_llm_node --ros-args \
  --params-file /root/exchange/src/llm_interaction/config/llm_config.yaml
```

Quando compare il prompt, premi INVIO e parla in inglese durante i cinque
secondi di registrazione.

Per terminare:

```text
/quit
```

## 8. Container: pipeline italiana

```bash
ros2 run llm_interaction voice_llm_node --ros-args \
  --params-file /root/exchange/src/llm_interaction/config/llm_config.yaml \
  -p input_mode:=voice \
  -p stt_provider:=faster_whisper \
  -p stt_model:=tiny \
  -p language:=it \
  -p llm_provider:=ollama \
  -p llm_model:=qwen2.5:1.5b \
  -p tts_provider:=system
```

Per terminare:

```text
/quit
```
