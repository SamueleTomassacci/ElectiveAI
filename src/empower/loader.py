import os
import time
from models import YOLOW,VitSam
import spacy
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
import gensim.downloader as api

from paths import *

class Loader:

    instance = None

    def __new__(cls, *args):
        if not cls.instance:
            cls.instance = super(Loader, cls).__new__(cls)
            cls.instance.initialize(*args)
        return cls.instance

    def initialize(self, *args):
        use_case = args[0]
        self.use_case = use_case

        self.CONFIG = CONFIG_DIR
        self.IMAGES = IMAGES_DIR
        self.OUTPUT = OUTPUT_DIR

        self.YOLOW_PATH = self.CONFIG + "yolow/"
        self.ENCODER_PATH = self.CONFIG + "efficientvitsam/l2_encoder.onnx"
        self.DECODER_PATH = self.CONFIG + "efficientvitsam/l2_decoder.onnx"

        self.SCAN_DIR = self.IMAGES + use_case+'/'
        os.makedirs(self.SCAN_DIR, exist_ok=True)
        self.DUMP_DIR = self.OUTPUT + use_case + '/'
        os.makedirs(self.DUMP_DIR, exist_ok=True)
        self.MASKED_SCANS_DIR = self

        self.yolow_model = YOLOW(self.YOLOW_PATH)
        self.vit_sam_model = VitSam(self.ENCODER_PATH, self.DECODER_PATH)

        self.nlp = spacy.load("en_core_web_sm")

        self.wv = api.load('word2vec-google-news-300')

    @property
    def nlp(self):
        return self.__dict__["nlp"]
    
    @nlp.setter
    def nlp(self, value):
        self.__dict__["nlp"] = value

    @property
    def wv(self):
        return self.__dict__["wv"]
    
    @wv.setter
    def wv(self, value):
        self.__dict__["wv"] = value

    @property
    def yolow_model(self):

        return self.__dict__["yolow_model"]
    
    @yolow_model.setter
    def yolow_model(self,value):
        self.__dict__["yolow_model"] = value

    @property
    def vit_sam_model(self):
        return self.__dict__["vit_sam_model"]
    
    @vit_sam_model.setter
    def vit_sam_model(self,value):
        self.__dict__["vit_sam_model"] = value
        
    @property
    def CONFIG(self):
        return self.__dict__["CONFIG"]
    
    @CONFIG.setter
    def CONFIG(self,value):

        self.__dict__["CONFIG"] = value
    
    @property
    def IMAGES(self):
        return self.__dict__["IMAGES"]
    
    @IMAGES.setter
    def IMAGES(self,value):
        self.__dict__["IMAGES"] = value
    
    @property
    def YOLOW_PATH(self):
        return self.__dict__["YOLOW_PATH"]
    
    @YOLOW_PATH.setter
    def YOLOW_PATH(self,value):
        self.__dict__["YOLOW_PATH"] = value

    @property
    def ENCODER_PATH(self):
        return self.__dict__["ENCODER_PATH"]
    
    @ENCODER_PATH.setter
    def ENCODER_PATH(self, value):
        self.__dict__["ENCODER_PATH"] = value
    
    @property
    def DECODER_PATH(self):
        return self.__dict__["DECODER_PATH"]
    
    @DECODER_PATH.setter
    def DECODER_PATH(self, value):
        self.__dict__["DECODER_PATH"] = value
    
    @property
    def SCAN_DIR(self):
        return self.__dict__["SCAN_DIR"]
    
    @SCAN_DIR.setter
    def SCAN_DIR(self,value):
        self.__dict__["SCAN_DIR"] = value
    
    @property
    def DUMP_DIR(self):
        return self.__dict__["DUMP_DIR"]
    
    @DUMP_DIR.setter
    def DUMP_DIR(self,value):
        self.__dict__["DUMP_DIR"] = value
    
    @property
    def OUTPUT(self):
        return self.__dict__["OUTPUT"]
    
    @OUTPUT.setter
    def OUTPUT(self, value):
        self.__dict__["OUTPUT"] = value
    
    @property
    def use_case(self):
        return self.__dict__["use_case"]
    
    @use_case.setter
    def use_case(self, value):
        self.__dict__["use_case"] = value
