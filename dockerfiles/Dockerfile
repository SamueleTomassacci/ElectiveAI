FROM ros:humble

ENV DEBIAN_FRONTEND=noninteractive
ENV WS=/root/tiago_public_ws

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    gnupg2 \
    lsb-release \
    unzip \
    build-essential \
    python3-pip \
    python3-colcon-common-extensions \
    python3-rosdep \
    python3-vcstool \
    ros-humble-cv-bridge \
    ros-humble-gazebo-ros-pkgs \
    ros-humble-image-view \
    ros-humble-joint-state-publisher-gui \
    ros-humble-navigation2 \
    ros-humble-nav2-bringup \
    ros-humble-robot-state-publisher \
    ros-humble-rmw-cyclonedds-cpp \
    ros-humble-sensor-msgs-py \
    ros-humble-slam-toolbox \
    ros-humble-teleop-twist-keyboard \
    ros-humble-vision-opencv \
    ros-humble-visualization-msgs \
    ros-humble-xacro \
    && rm -rf /var/lib/apt/lists/*

ENV EMPOWER_UPSTREAM_DIR=/opt/empower_upstream
ENV EMPOWER_CONFIG_DIR=/opt/empower_models/config
ENV GENSIM_DATA_DIR=/opt/gensim-data
ENV HF_HOME=/opt/huggingface

ARG EMPOWER_DOWNLOAD_MODELS=true
ARG EMPOWER_PRECACHE_WORD2VEC=true
ARG EMPOWER_PRECACHE_HF=true

RUN git clone --depth 1 https://github.com/Lab-RoCoCo-Sapienza/empower ${EMPOWER_UPSTREAM_DIR} && \
    mkdir -p ${EMPOWER_CONFIG_DIR} && \
    cp -a ${EMPOWER_UPSTREAM_DIR}/config/yolow ${EMPOWER_CONFIG_DIR}/yolow && \
    mkdir -p ${EMPOWER_CONFIG_DIR}/efficientvitsam ${GENSIM_DATA_DIR} ${HF_HOME}

RUN python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel && \
    python3 -m pip install --no-cache-dir "numpy<2" && \
    python3 -m pip install --no-cache-dir -r ${EMPOWER_UPSTREAM_DIR}/requirements.txt && \
    python3 -m pip install --no-cache-dir openmim && \
    mim install "mmcv==2.0.0" mmyolo mmdet && \
    python3 -m spacy download en_core_web_sm

RUN if [ "${EMPOWER_DOWNLOAD_MODELS}" = "true" ]; then \
      curl -L --retry 5 --retry-delay 5 \
        -o ${EMPOWER_CONFIG_DIR}/yolow/yolow.pth \
        https://huggingface.co/spaces/stevengrove/YOLO-World/resolve/main/yolow-v8_l_clipv2_frozen_t2iv2_bn_o365_goldg_pretrain.pth && \
      curl -L --retry 5 --retry-delay 5 \
        -o ${EMPOWER_CONFIG_DIR}/efficientvitsam/l2_encoder.onnx \
        https://huggingface.co/mit-han-lab/efficientvit-sam/resolve/main/onnx/l2_encoder.onnx && \
      curl -L --retry 5 --retry-delay 5 \
        -o ${EMPOWER_CONFIG_DIR}/efficientvitsam/l2_decoder.onnx \
        https://huggingface.co/mit-han-lab/efficientvit-sam/resolve/main/onnx/l2_decoder.onnx ; \
    fi

RUN if [ "${EMPOWER_PRECACHE_WORD2VEC}" = "true" ]; then \
      python3 -c 'import gensim.downloader as api; api.load("word2vec-google-news-300")' ; \
    fi

RUN python3 -m pip install --no-cache-dir "transformers==4.37.2"

RUN python3 -m pip install --no-cache-dir "tomli>=2,<3"

RUN if [ "${EMPOWER_PRECACHE_HF}" = "true" ]; then \
      python3 -c 'from transformers import CLIPModel, CLIPProcessor; CLIPModel.from_pretrained("openai/clip-vit-base-patch32"); CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")' ; \
    fi

RUN python3 -m pip install --no-cache-dir "setuptools==58.2.0"

RUN rosdep update

RUN mkdir -p ${WS}/src
WORKDIR ${WS}

RUN vcs import --input https://raw.githubusercontent.com/pal-robotics/tiago_tutorials/humble-devel/tiago_public.repos src

RUN apt-get update && \
    rosdep install --from-paths src -y --ignore-src && \
    rm -rf /var/lib/apt/lists/*

RUN . /opt/ros/humble/setup.sh && \
    colcon build --symlink-install --cmake-args -DCMAKE_POLICY_VERSION_MINIMUM=3.5

# HRI audio/voice dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libportaudio2 \
    portaudio19-dev \
    libsndfile1 \
    libasound2-plugins \
    alsa-utils \
    espeak-ng \
    pulseaudio-utils \
    python3-rdflib \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir \
    sounddevice \
    soundfile \
    faster-whisper \
    pyttsx3 \
    piper-tts

# Gaze estimation dependencies 
RUN python3 -m pip install --no-cache-dir \
    torch==2.0.1 torchvision==0.15.2 \
    --index-url https://download.pytorch.org/whl/cu117

RUN python3 -m pip install --no-cache-dir \
    timm==0.9.16 \
    ultralytics==8.4.71 \
    ftfy

# Ultralytics CLIP (pinned) — prevents 31s runtime auto-install on every container start
RUN python3 -m pip install --no-cache-dir \
    git+https://github.com/ultralytics/CLIP.git@16be45c7062240d445cce764f2afd9454a91ef7e
ENV YOLO_AUTOINSTALL=0

# Pre-download DINOv2 ViT-L/14 backbone
RUN python3 -c "\
import torch; \
torch.hub.load('facebookresearch/dinov2', 'dinov2_vitl14', pretrained=True); \
print('DINOv2 ViT-L/14 cached')"

# EmotiEffLib for facial emotion recognition
RUN python3 -m pip install --no-cache-dir --no-deps emotiefflib

# Pre-cache EmotiEffLib emotion model weights
RUN python3 -c "\
from emotiefflib.facial_analysis import EmotiEffLibRecognizer; \
EmotiEffLibRecognizer(engine='torch', model_name='enet_b0_8_best_vgaf', device='cpu'); \
print('EmotiEffLib enet_b0_8_best_vgaf cached')"

# Pre-download gaze estimation model weights into the image.
# At runtime /root/exchange is a volume mount, so the entrypoint
# symlinks these into /root/exchange/models/ if they are not already there.
ENV GAZE_MODELS_DIR=/opt/models/gaze_estimation
RUN mkdir -p ${GAZE_MODELS_DIR} && \
    curl -L --retry 5 --retry-delay 5 --progress-bar \
      -o ${GAZE_MODELS_DIR}/gazelle_dinov2_vitl14_inout.pt \
      https://github.com/fkryan/gazelle/releases/download/v1.0.0/gazelle_dinov2_vitl14_inout.pt && \
    curl -L --retry 5 --retry-delay 5 --progress-bar \
      -o ${GAZE_MODELS_DIR}/yolov8x-worldv2.pt \
      https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8x-worldv2.pt && \
    curl -L --retry 5 --retry-delay 5 --progress-bar \
      -o ${GAZE_MODELS_DIR}/yolov8n-face.pt \
      https://github.com/lindevs/yolov8-face/releases/latest/download/yolov8n-face-lindevs.pt

# Pre-download Piper TTS voice model
ENV PIPER_MODELS_DIR=/opt/models/piper
RUN mkdir -p ${PIPER_MODELS_DIR} && \
    curl -L --retry 5 --retry-delay 5 --progress-bar \
      -o ${PIPER_MODELS_DIR}/en_GB-jenny_dioco-medium.onnx \
      https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx && \
    curl -L --retry 5 --retry-delay 5 --progress-bar \
      -o ${PIPER_MODELS_DIR}/en_GB-jenny_dioco-medium.onnx.json \
      https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx.json

RUN mkdir -p /app
COPY dockerfiles/entrypoint_tiago.sh /app/entrypoint_tiago.sh
RUN chmod +x /app/entrypoint_tiago.sh

WORKDIR /root/exchange
CMD ["/app/entrypoint_tiago.sh"]
