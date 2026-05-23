import os

import gensim.downloader as gensim_api
import spacy

from models import VitSam, YOLOW


class EmpowerRos2Loader:
    """ROS2-side loader exposing the same attributes used by empower Detection."""

    def __init__(
        self,
        *,
        use_case,
        images_dir,
        output_dir,
        config_dir,
        yolow_path=None,
        encoder_path=None,
        decoder_path=None,
        spacy_model="en_core_web_sm",
        word2vec_model="word2vec-google-news-300",
    ):
        self._use_case = use_case
        self._CONFIG = os.path.abspath(config_dir) + "/"
        self._IMAGES = os.path.abspath(images_dir) + "/"
        self._OUTPUT = os.path.abspath(output_dir) + "/"
        self._YOLOW_PATH = os.path.abspath(yolow_path or os.path.join(self._CONFIG, "yolow")) + "/"
        self._ENCODER_PATH = os.path.abspath(
            encoder_path or os.path.join(self._CONFIG, "efficientvitsam", "l2_encoder.onnx")
        )
        self._DECODER_PATH = os.path.abspath(
            decoder_path or os.path.join(self._CONFIG, "efficientvitsam", "l2_decoder.onnx")
        )
        self._SCAN_DIR = os.path.join(self._IMAGES, use_case) + "/"
        self._DUMP_DIR = os.path.join(self._OUTPUT, use_case) + "/"

        os.makedirs(self._SCAN_DIR, exist_ok=True)
        os.makedirs(self._DUMP_DIR, exist_ok=True)

        self._check_file(os.path.join(self._YOLOW_PATH, "yolow.pth"), "YOLO-World checkpoint")
        self._check_file(self._ENCODER_PATH, "EfficientViT-SAM encoder ONNX")
        self._check_file(self._DECODER_PATH, "EfficientViT-SAM decoder ONNX")

        self._yolow_model = YOLOW(self._YOLOW_PATH)
        self._vit_sam_model = VitSam(self._ENCODER_PATH, self._DECODER_PATH)
        self._nlp = spacy.load(spacy_model)
        self._wv = gensim_api.load(word2vec_model)

    @staticmethod
    def _check_file(path, label):
        if not os.path.exists(path):
            raise FileNotFoundError(f"{label} not found: {path}")

    @property
    def nlp(self):
        return self._nlp

    @property
    def wv(self):
        return self._wv

    @property
    def yolow_model(self):
        return self._yolow_model

    @property
    def vit_sam_model(self):
        return self._vit_sam_model

    @property
    def CONFIG(self):
        return self._CONFIG

    @property
    def IMAGES(self):
        return self._IMAGES

    @property
    def OUTPUT(self):
        return self._OUTPUT

    @property
    def YOLOW_PATH(self):
        return self._YOLOW_PATH

    @property
    def ENCODER_PATH(self):
        return self._ENCODER_PATH

    @property
    def DECODER_PATH(self):
        return self._DECODER_PATH

    @property
    def SCAN_DIR(self):
        return self._SCAN_DIR

    @property
    def DUMP_DIR(self):
        return self._DUMP_DIR

    @property
    def use_case(self):
        return self._use_case
