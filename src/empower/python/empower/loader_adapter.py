import os

import gensim.downloader as gensim_api
import spacy

from models import VitSam, YOLOW


class EmpowerLoader:
    

    def __init__(
        self,
        *,
        use_case,
        output_dir,
        config_dir,
        images_dir=None,
        yolow_path=None,
        encoder_path=None,
        decoder_path=None,
        spacy_model="en_core_web_sm",
        word2vec_model="word2vec-google-news-300",
        child_planner_context="",
        child_aliases=None,
        multi_view_enabled=False,
        multi_view_config=None,
        object_aliases=None,
    ):
        self.__dict__["use_case"] = use_case
        self.__dict__["CONFIG"] = os.path.abspath(config_dir) + "/"
        self.__dict__["OUTPUT"] = os.path.abspath(output_dir) + "/"
        self.__dict__["IMAGES"] = self.OUTPUT
        self.__dict__["YOLOW_PATH"] = os.path.abspath(yolow_path or os.path.join(self.CONFIG, "yolow"))+"/"
        self.__dict__["ENCODER_PATH"] = os.path.abspath(
            encoder_path or os.path.join(self.CONFIG, "efficientvitsam","l2_encoder.onnx")
        )
        self.__dict__["DECODER_PATH"] = os.path.abspath(
            decoder_path or os.path.join(self.CONFIG,"efficientvitsam","l2_decoder.onnx")
        )
        self.__dict__["DUMP_DIR"] = os.path.join(self.OUTPUT,use_case) + "/"

        self.__dict__["SCAN_DIR"] = self.DUMP_DIR
        self.__dict__["child_planner_context"] = child_planner_context or ""

        self.__dict__["child_aliases"] = list(child_aliases or ["child","the child","kid"])
        self.__dict__["multi_view_enabled"] = bool(multi_view_enabled)
        self.__dict__["multi_view_config"] = dict(multi_view_config or {})
        self.__dict__["object_aliases"] = dict(object_aliases or {})

        os.makedirs(self.DUMP_DIR, exist_ok=True)

        self.check_file(os.path.join(self.YOLOW_PATH, "yolow.pth"), "YOLO-World checkpoint")
        self.check_file(self.ENCODER_PATH, "EfficientViT-SAM encoder ONNX")
        self.check_file(self.DECODER_PATH, "EfficientViT-SAM decoder ONNX")

        self.__dict__["yolow_model"] = YOLOW(self.YOLOW_PATH)
        self.__dict__["vit_sam_model"] = VitSam(self.ENCODER_PATH,self.DECODER_PATH)
        self.__dict__["nlp"] = spacy.load(spacy_model)
        self.__dict__["wv"] = gensim_api.load(word2vec_model)

    @staticmethod
    def check_file(path,label):
        if not os.path.exists(path):
            raise FileNotFoundError(f"{label} not found: {path}")

    @property
    def nlp(self):
        return self.__dict__["nlp"]

    @property
    def wv(self):
        return self.__dict__["wv"]

    @property
    def yolow_model(self):
        return self.__dict__["yolow_model"]

    @property
    def vit_sam_model(self):
        return self.__dict__["vit_sam_model"]

    @property
    def CONFIG(self):

        return self.__dict__["CONFIG"]

    @property
    def IMAGES(self):

        return self.__dict__["IMAGES"]

    @property
    def OUTPUT(self):
        return self.__dict__["OUTPUT"]

    @property
    def YOLOW_PATH(self):
        return self.__dict__["YOLOW_PATH"]

    @property
    def ENCODER_PATH(self):
        return self.__dict__["ENCODER_PATH"]

    @property
    def DECODER_PATH(self):
        return self.__dict__["DECODER_PATH"]

    @property
    def SCAN_DIR(self):

        return self.__dict__["SCAN_DIR"]

    @property
    def DUMP_DIR(self):
        return self.__dict__["DUMP_DIR"]

    @property
    def use_case(self):
        return self.__dict__["use_case"]

    @property
    def child_planner_context(self):
        return self.__dict__["child_planner_context"]

    @property
    def child_aliases(self):
        return self.__dict__["child_aliases"]

    @property
    def multi_view_enabled(self):
        return self.__dict__["multi_view_enabled"]

    @property
    def multi_view_config(self):
        return self.__dict__["multi_view_config"]

    @property
    def object_aliases(self):
        return self.__dict__["object_aliases"]
