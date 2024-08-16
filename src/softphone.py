import json
import os
from pathlib import Path
import threading
import time
import wave
from openai import OpenAI
import pjsua2 as pj
# import pyttsx3
from pydub import AudioSegment
import numpy as np
import yaml

HERE = Path(os.path.abspath(__file__)).parent

class softphone_call(pj.Call):

    softphone = None
    
    def __init__(self, acc, softphone):   
        self.softphone = softphone
        super(softphone_call, self).__init__(acc)

    def onCallState(self, prm):
        call_info = self.getInfo()
        if call_info.state == pj.PJSIP_INV_STATE_DISCONNECTED:
            self.softphone.hangup()
        
        super(softphone_call, self).onCallState(prm)
          

class softphone:
    __config = None
    
    __pjsua_endpoint = None
    __pjsua_account = None
    __sip_credentials = None
    active_call = None
    
    __tts_engine = None
    __media_player_1 = None
    __media_player_2 = None
    __media_recorder = None
    
    __openai_client = None
    
    
    def __init__(self):
        # Load config
        with open(HERE / '../conf/softphone_config.yaml', 'r') as config_file:
            self.__config = yaml.safe_load(config_file)
        
        # Load SIP Credentials
        credentials_path = os.environ['SIP_CREDENTIALS_PATH']
        with open(credentials_path, 'r') as f:
            self.__sip_credentials = json.load(f)
            
        # Initialize PJSUA2 endpoint
        ep_cfg = pj.EpConfig()
        ep_cfg.uaConfig.threadCnt = 1
        ep_cfg.logConfig.level = 1
        ep_cfg.logConfig.consoleLevel = 1
        self.__pjsua_endpoint = pj.Endpoint()
        self.__pjsua_endpoint.libCreate()
        self.__pjsua_endpoint.libInit(ep_cfg)

        sipTpConfig = pj.TransportConfig()
        sipTpConfig.port = 5061;
        self.__pjsua_endpoint.transportCreate(pj.PJSIP_TRANSPORT_UDP, sipTpConfig)
        self.__pjsua_endpoint.libStart()
        
        # initialize media devices
        # WSL has no audio device, therefore use null device
        self.__pjsua_endpoint.audDevManager().setNullDev()
        # self.__tts_engine = pyttsx3.init()
        self.__media_player_1 = None
        self.__media_player_2 = None
        self.__media_recorder = None 

        # Create SIP Account
        acfg = pj.AccountConfig()
        acfg.idUri = self.__sip_credentials['idUri']
        acfg.regConfig.registrarUri = self.__sip_credentials['registrarUri']
        cred = pj.AuthCredInfo("digest", "*", self.__sip_credentials['username'], 0, self.__sip_credentials['password'])
        acfg.sipConfig.authCreds.append(cred)

        self.__pjsua_account = pj.Account()
        self.__pjsua_account.create(acfg)
        
        # Initialize OpenAI
        self.__openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        
    def __del__(self):
        self.__pjsua_endpoint.libDestroy()
    
    def has_picked_up_call(self):
        if self.active_call:
            call_info = self.active_call.getInfo()
            for i in range(call_info.media.size()):
                if (call_info.media[i].type==pj.PJMEDIA_TYPE_AUDIO and self.active_call.getMedia(i)):
                    return True
        return False
    
    def call(self, phone_number): 
        if self.active_call:
            print("Can't call: There is a call already in progress.")
        
        # construct SIP adress
        registrar = self.__sip_credentials['registrarUri'].split(':')[1]
        sip_adress = "sip:" + phone_number + "@" + registrar
        
        # make call
        self.active_call = softphone_call(self.__pjsua_account, self)
        call_op_param = pj.CallOpParam(True)
        self.active_call.makeCall(sip_adress, call_op_param)
    
    def wait_for_stop_calling(self):
        if not self.active_call:
            return
        
        call_info = self.active_call.getInfo()
        while(call_info.state == pj.PJSIP_INV_STATE_CALLING or call_info.state == pj.PJSIP_INV_STATE_EARLY):
            time.sleep(0.2)
            if not self.active_call:
                return
            call_info = self.active_call.getInfo()
    
    def hangup(self):
        if not self.active_call:
            print("Can't hangup: No call in progress.")
            return

        self.active_call.hangup(pj.CallOpParam(True))
        self.active_call = None
                
    def say(self, message):        
        if not self.active_call:
            print("Can't say: No call in progress.")
            return
                
        call_info = self.active_call.getInfo()
        for i in range(len(call_info.media)):
            if call_info.media[i].type == pj.PJMEDIA_TYPE_AUDIO and call_info.media[i].status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                call_media = self.active_call.getAudioMedia(i)
                
                # -- Recieve TTS audio from OpenAI and stream it using double buffering --
                # Setup buffer files
                try:
                    silence = np.zeros(1024, dtype=np.int16).tobytes()
                    with wave.open(str(HERE / "../artifacts/outgoing_buffer_0.wav"), 'wb') as buffer_0:
                        buffer_0.setnchannels(self.__config['tts_channels']) 
                        buffer_0.setsampwidth(self.__config['tts_sample_width'])  
                        buffer_0.setframerate(self.__config['tts_sample_rate'])
                        buffer_0.writeframes(silence)
                    
                    with wave.open(str(HERE / "../artifacts/outgoing_buffer_1.wav"), 'wb') as buffer_1:
                        buffer_1.setnchannels(self.__config['tts_channels']) 
                        buffer_1.setsampwidth(self.__config['tts_sample_width'])  
                        buffer_1.setframerate(self.__config['tts_sample_rate'])
                        buffer_1.writeframes(silence)
                    
                    # stream and play response to/from alternating buffer
                    delay = self.__config['tts_chunk_size'] / (self.__config['tts_sample_rate'] * self.__config['tts_sample_width'] * self.__config['tts_channels']) # length of each chunk in seconds

                    with self.__openai_client.audio.speech.with_streaming_response.create(
                    model="tts-1",
                    voice="alloy",
                    input=message,
                    response_format="pcm",
                    ) as response:   
                        buffer_switch = True
                        for chunk in response.iter_bytes(chunk_size=self.__config['tts_chunk_size']):
                            if chunk and len(chunk) >=512: 
                                if buffer_switch:
                                    buffer_switch = False
                                    if self.__media_player_2:
                                        self.__media_player_2.stopTransmit(call_media)
                                    self.__media_player_1 = pj.AudioMediaPlayer()  
                                    self.__media_player_1.createPlayer(str(HERE / "../artifacts/outgoing_buffer_0.wav"), pj.PJMEDIA_FILE_NO_LOOP)
                                    self.__media_player_1.startTransmit(call_media)

                                    with wave.open(str(HERE / "../artifacts/outgoing_buffer_1.wav"), 'wb') as buffer_1: 
                                        buffer_1.setnchannels(self.__config['tts_channels']) 
                                        buffer_1.setsampwidth(self.__config['tts_sample_width'])  
                                        buffer_1.setframerate(self.__config['tts_sample_rate'])
                                        buffer_1.writeframes(chunk)
                                        time.sleep(delay)
                                else:
                                    buffer_switch = True
                                    if self.__media_player_1:
                                        self.__media_player_1.stopTransmit(call_media)
                                    self.__media_player_2 = pj.AudioMediaPlayer()  
                                    self.__media_player_2.createPlayer(str(HERE / "../artifacts/outgoing_buffer_1.wav"), pj.PJMEDIA_FILE_NO_LOOP)
                                    self.__media_player_2.startTransmit(call_media)
                                    with wave.open(str(HERE / "../artifacts/outgoing_buffer_0.wav"), 'wb') as buffer_0:
                                        buffer_0.setnchannels(self.__config['tts_channels']) 
                                        buffer_0.setsampwidth(self.__config['tts_sample_width'])  
                                        buffer_0.setframerate(self.__config['tts_sample_rate'])
                                        buffer_0.writeframes(chunk)
                                        time.sleep(delay)
                                        
                        # play residue audio from last buffer                
                        if buffer_switch:
                            self.__media_player_2.stopTransmit(call_media)
                            self.__media_player_1 = pj.AudioMediaPlayer()  
                            self.__media_player_1.createPlayer(str(HERE / "../artifacts/outgoing_buffer_0.wav"), pj.PJMEDIA_FILE_NO_LOOP)
                            self.__media_player_1.startTransmit(call_media)
                        else:
                            self.__media_player_1.stopTransmit(call_media)
                            self.__media_player_2 = pj.AudioMediaPlayer()  
                            self.__media_player_2.createPlayer(str(HERE / "../artifacts/outgoing_buffer_1.wav"), pj.PJMEDIA_FILE_NO_LOOP)
                            self.__media_player_2.startTransmit(call_media)  
                except:    
                    print('Error occured while speaking (probably because user hung up)')
                                   
                return
        print("No available audio media")
        
    def listen(self):
        # skip silence
        self.__record_incoming_audio(self.__config['silence_sample_interval'])
        last_segment = AudioSegment.from_wav(str(HERE / "../artifacts/incoming.wav"))
        while last_segment.dBFS < self.__config['silence_threshold']:
            
            if not self.active_call:
                return ""
            
            self.__record_incoming_audio(self.__config['silence_sample_interval'])
            last_segment = AudioSegment.from_wav(str(HERE / "../artifacts/incoming.wav"))
            
        # record audio while over silence threshold
        combined_segments = last_segment
        while last_segment.dBFS > self.__config['silence_threshold']:
            
            if not self.active_call:
                return ""
            
            self.__record_incoming_audio(self.__config['speaking_sample_interval'])
            last_segment = AudioSegment.from_wav(str(HERE / "../artifacts/incoming.wav"))
            combined_segments += last_segment
        
        # output combined audio to file
        combined_segments.export(str(HERE / "../artifacts/incoming_combined.wav"), format="wav")
        
        # transcribe audio
        audio_file = open(str(HERE / "../artifacts/incoming_combined.wav"), "rb")
        transcription = self.__openai_client.audio.transcriptions.create(
        model="whisper-1", 
        file=audio_file
        )
        return transcription.text
                
    def __record_incoming_audio(self, duration = 1.0):
        call_info = self.active_call.getInfo()
        for i in range(len(call_info.media)):
            if call_info.media[i].type == pj.PJMEDIA_TYPE_AUDIO and call_info.media[i].status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                call_media = self.active_call.getAudioMedia(i)
                
                self.__media_recorder = pj.AudioMediaRecorder()
                self.__media_recorder.createRecorder(str(HERE / "../artifacts/incoming.wav"))
                call_media.startTransmit(self.__media_recorder)
                time.sleep(duration)
                call_media.stopTransmit(self.__media_recorder)
                del self.__media_recorder
