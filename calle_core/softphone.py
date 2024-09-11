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
import uuid
import glob

HERE = Path(os.path.abspath(__file__)).parent

class softphone_call(pj.Call):

    softphone = None
    
    def __init__(self, acc, softphone, call_id = pj.PJSUA_INVALID_ID):   
        super(softphone_call, self).__init__(acc, call_id)
        self.softphone = softphone


    def onCallState(self, prm):
        if not self.softphone:
            return
        
        call_info = self.getInfo()
        if call_info.state == pj.PJSIP_INV_STATE_DISCONNECTED:
            self.softphone.hangup()
        
        super(softphone_call, self).onCallState(prm)        
        
class group_account(pj.Account):
    def __init__(self, group):
        self.__group = group
        super(group_account, self).__init__()

        
    def onIncomingCall(self, prm):
        for phone in self.__group.softphones:
            if phone.active_call:
                continue
            
            call = softphone_call(self, phone, prm.callId)
            
            call_op_param = pj.CallOpParam()
            call_op_param.statusCode = pj.PJSIP_SC_OK
            call.answer(call_op_param)
            phone.active_call = call
            return
                    
        # no available phone found
        call = softphone_call(self, None, prm.callId)
        call_op_param = pj.CallOpParam(True)
        call.hangup(call_op_param)
          

class softphone:
    __config = None
    __id = None
    
    __group = None
    active_call = None
    __paired_call = None
    
    __tts_engine = None
    __media_player_1 = None
    __media_player_2 = None
    __media_recorder = None
    
    __openai_client = None
    
    
    def __init__(self, credentials_path, group = None):
        # Load config
        with open(HERE / '../conf/softphone_config.yaml', 'r') as config_file:
            self.__config = yaml.safe_load(config_file)
        
        if group:
            self.__group = group
        else:
            self.__group = softphone_group(credentials_path)
        self.__group.add_phone(self)
        
        self.__id = uuid.uuid4()
        self.__paired_call = None
        
        # self.__tts_engine = pyttsx3.init()
        self.__media_player_1 = None
        self.__media_player_2 = None
        self.__media_recorder = None
        
        # Initialize OpenAI
        self.__openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        
    def __del__(self):
        self.__media_player_1 = None
        self.__media_player_2 = None
        self.__media_recorder = None
        self.__group.remove_phone(self)
        
    def __remove_artifacts(self):
        artifacts = glob.glob(os.path.join(HERE / "../artifacts/", f'{self.__id}*'))
        for artifact in artifacts:
            if os.path.isfile(artifact):
                os.remove(artifact)
    

    
    def call(self, phone_number): 
        if self.active_call:
            print("Can't call: There is a call already in progress.")
        
        # construct SIP adress
        registrar = self.__group.sip_credentials['registrarUri'].split(':')[1]
        sip_adress = "sip:" + phone_number + "@" + registrar
        
        # make call
        self.active_call = softphone_call(self.__group.pjsua_account, self)
        call_op_param = pj.CallOpParam(True)
        self.active_call.makeCall(sip_adress, call_op_param)
    
    def forward_call(self, phone_number):
        if not self.active_call:
            print("Can't forward call: No call in progress.")
            return False
            
        if self.__paired_call:
            print("Can't forward call: Already in forwarding session.")
            return False
            
        print("Forwarding call...")
        
        # construct SIP adress
        registrar = self.__group.sip_credentials['registrarUri'].split(':')[1]
        sip_adress = "sip:" + phone_number + "@" + registrar
        
        # make call to forwarded number
        self.__paired_call = softphone_call(self.__group.pjsua_account, self)
        call_op_param = pj.CallOpParam(True)
        self.__paired_call.makeCall(sip_adress, call_op_param)
        
        # wait for pick up
        self.__wait_for_stop_calling("paired")
        
        if not self.__has_picked_up_call("paired"):
            print("Call not picked up.")
            return False
        
        # connect audio medias of both calls
        active_call_media = None
        paired_call_media = None
        
        active_call_info = self.active_call.getInfo()
        for i in range(len(active_call_info.media)):
            if active_call_info.media[i].type == pj.PJMEDIA_TYPE_AUDIO: #and active_call_info.media[i].status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                active_call_media = self.active_call.getAudioMedia(i)
        
        paired_call_info = self.__paired_call.getInfo()
        for i in range(len(paired_call_info.media)):
            if paired_call_info.media[i].type == pj.PJMEDIA_TYPE_AUDIO: #and paired_call_info.media[i].status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                paired_call_media = self.__paired_call.getAudioMedia(i)
                
        if not active_call_media or not paired_call_media:
            print("No audio media available.")
            return False
        
        if self.__media_player_1:
            self.__media_player_1.stopTransmit(active_call_media)
        if self.__media_player_2:
            self.__media_player_2.stopTransmit(active_call_media)
        active_call_media.startTransmit(paired_call_media)
        paired_call_media.startTransmit(active_call_media)
        
        return True
    
    def is_forwarded(self):
        return self.__paired_call is not None
    
    def __has_picked_up_call(self, call_type="active"):
        if call_type == "active":
            call = self.active_call
        elif call_type == "paired":
            call = self.__paired_call
        else:
            return False
        
        if call:
            call_info = call.getInfo()
            for i in range(call_info.media.size()):
                if (call_info.media[i].type==pj.PJMEDIA_TYPE_AUDIO and call.getMedia(i)):
                    return True
        return False
        
        
    def has_picked_up_call(self):
        return self.__has_picked_up_call("active")
        
    def __wait_for_stop_calling(self, call_type="active"):
        if call_type == "active":
            call = self.active_call
        elif call_type == "paired":
            call = self.__paired_call
        else:
            return
            
        if not call:
            return
        
        call_info = call.getInfo()
        while(call_info.state == pj.PJSIP_INV_STATE_CALLING or call_info.state == pj.PJSIP_INV_STATE_EARLY):
            time.sleep(0.2)
            if not call:
                return
            call_info = call.getInfo()
    
    def wait_for_stop_calling(self):
        self.__wait_for_stop_calling("active")
        
    
    def hangup(self):
        if self.active_call:
            self.active_call.hangup(pj.CallOpParam(True))
            self.active_call = None
        
        if self.__paired_call:
            self.__paired_call.hangup(pj.CallOpParam(True))
            self.__paired_call = None

        self.__remove_artifacts()
                
    def say(self, message):        
        if not self.active_call:
            print("Can't say: No call in progress.")
            return
        if self.__paired_call:
            print("Can't say: Call is in forwarding session.")
            return
                
        call_info = self.active_call.getInfo()
        for i in range(len(call_info.media)):
            if call_info.media[i].type == pj.PJMEDIA_TYPE_AUDIO and call_info.media[i].status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                call_media = self.active_call.getAudioMedia(i)
                
                # -- Recieve TTS audio from OpenAI and stream it using double buffering --
                # Setup buffer files
                try:
                    silence = np.zeros(1024, dtype=np.int16).tobytes()
                    with wave.open(str(HERE / f"../artifacts/{self.__id}_outgoing_buffer_0.wav"), 'wb') as buffer_0:
                        buffer_0.setnchannels(self.__config['tts_channels']) 
                        buffer_0.setsampwidth(self.__config['tts_sample_width'])  
                        buffer_0.setframerate(self.__config['tts_sample_rate'])
                        buffer_0.writeframes(silence)
                    
                    with wave.open(str(HERE / f"../artifacts/{self.__id}_outgoing_buffer_1.wav"), 'wb') as buffer_1:
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
                                    self.__media_player_1.createPlayer(str(HERE / f"../artifacts/{self.__id}_outgoing_buffer_0.wav"), pj.PJMEDIA_FILE_NO_LOOP)
                                    self.__media_player_1.startTransmit(call_media)

                                    with wave.open(str(HERE / f"../artifacts/{self.__id}_outgoing_buffer_1.wav"), 'wb') as buffer_1: 
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
                                    self.__media_player_2.createPlayer(str(HERE / f"../artifacts/{self.__id}_outgoing_buffer_1.wav"), pj.PJMEDIA_FILE_NO_LOOP)
                                    self.__media_player_2.startTransmit(call_media)
                                    with wave.open(str(HERE / f"../artifacts/{self.__id}_outgoing_buffer_0.wav"), 'wb') as buffer_0:
                                        buffer_0.setnchannels(self.__config['tts_channels']) 
                                        buffer_0.setsampwidth(self.__config['tts_sample_width'])  
                                        buffer_0.setframerate(self.__config['tts_sample_rate'])
                                        buffer_0.writeframes(chunk)
                                        time.sleep(delay)
                                        
                        # play residue audio from last buffer                
                        if buffer_switch:
                            self.__media_player_2.stopTransmit(call_media)
                            self.__media_player_1 = pj.AudioMediaPlayer()  
                            self.__media_player_1.createPlayer(str(HERE / f"../artifacts/{self.__id}_outgoing_buffer_0.wav"), pj.PJMEDIA_FILE_NO_LOOP)
                            self.__media_player_1.startTransmit(call_media)
                        else:
                            self.__media_player_1.stopTransmit(call_media)
                            self.__media_player_2 = pj.AudioMediaPlayer()  
                            self.__media_player_2.createPlayer(str(HERE / f"../artifacts/{self.__id}_outgoing_buffer_1.wav"), pj.PJMEDIA_FILE_NO_LOOP)
                            self.__media_player_2.startTransmit(call_media)  
                except:    
                    print('Error occured while speaking (probably because user hung up)')
                                   
                return
        print("No available audio media")

    def play_audio(self, audio_file_path, do_loop = False):
        if not self.active_call:
            print("Can't play audio: No call in progress.")
            return
        if self.__paired_call:
            print("Can't play audio: Call is in forwarding session.")
            return
        
        call_info = self.active_call.getInfo()
        for i in range(len(call_info.media)):
            if call_info.media[i].type == pj.PJMEDIA_TYPE_AUDIO and call_info.media[i].status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                call_media = self.active_call.getAudioMedia(i)
                
            if self.__media_player_1:
                self.__media_player_1.stopTransmit(call_media)
            if self.__media_player_2:
                self.__media_player_2.stopTransmit(call_media)
                
            self.__media_player_1 = pj.AudioMediaPlayer()  
            loop_mode = pj.PJMEDIA_FILE_LOOP if do_loop else pj.PJMEDIA_FILE_NO_LOOP
            self.__media_player_1.createPlayer(audio_file_path, loop_mode)
            self.__media_player_1.startTransmit(call_media)
        
    def listen(self):
        # skip silence
        if not self.__record_incoming_audio(self.__config['silence_sample_interval']):
            return ""
        
        last_segment = AudioSegment.from_wav(str(HERE / f"../artifacts/{self.__id}_incoming.wav"))
        while last_segment.dBFS < self.__config['silence_threshold']:
            
            if not self.active_call or self.__paired_call:
                return ""
            
            if not self.__record_incoming_audio(self.__config['silence_sample_interval']):
                return ""
            last_segment = AudioSegment.from_wav(str(HERE / f"../artifacts/{self.__id}_incoming.wav"))
            
        # record audio while over silence threshold
        combined_segments = last_segment
        while last_segment.dBFS > self.__config['silence_threshold']:
            
            if not self.active_call or self.__paired_call:
                return ""
            
            if not self.__record_incoming_audio(self.__config['speaking_sample_interval']):
                return ""
            last_segment = AudioSegment.from_wav(str(HERE / f"../artifacts/{self.__id}_incoming.wav"))
            combined_segments += last_segment
        
        # output combined audio to file
        combined_segments.export(str(HERE / f"../artifacts/{self.__id}_incoming_combined.wav"), format="wav")
        
        # transcribe audio
        audio_file = open(str(HERE / f"../artifacts/{self.__id}_incoming_combined.wav"), "rb")
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
                self.__media_recorder.createRecorder(str(HERE / f"../artifacts/{self.__id}_incoming.wav"))
                call_media.startTransmit(self.__media_recorder)
                time.sleep(duration)
                
                if not self.__media_recorder or not self.active_call:
                    return False
                
                call_media.stopTransmit(self.__media_recorder)
                del self.__media_recorder
                
        return True
    
# share one library instance and account for multiple sofpthones
class softphone_group:
    pjsua_endpoint = None
    pjsua_account = None
    sip_credentials = None
    softphones = []
    
    is_listening = False
    
    def __init__(self, credentials_path):
        self.softphones = []

        # Load SIP Credentials
        with open(credentials_path, 'r') as f:
            self.sip_credentials = json.load(f)
            
        # Initialize PJSUA2 endpoint
        ep_cfg = pj.EpConfig()
        ep_cfg.uaConfig.threadCnt = 1
        ep_cfg.logConfig.level = 1
        ep_cfg.logConfig.consoleLevel = 1
        self.pjsua_endpoint = pj.Endpoint()
        self.pjsua_endpoint.libCreate()
        self.pjsua_endpoint.libInit(ep_cfg)

        sipTpConfig = pj.TransportConfig()
        sipTpConfig.port = 5060
        self.pjsua_endpoint.transportCreate(pj.PJSIP_TRANSPORT_UDP, sipTpConfig)
        self.pjsua_endpoint.libStart()
        
        # Create SIP Account
        acfg = pj.AccountConfig()
        acfg.idUri = self.sip_credentials['idUri']
        acfg.regConfig.registrarUri = self.sip_credentials['registrarUri']
        cred = pj.AuthCredInfo("digest", "*", self.sip_credentials['username'], 0, self.sip_credentials['password'])
        acfg.sipConfig.authCreds.append(cred)

        self.pjsua_account = group_account(self)
        self.pjsua_account.create(acfg)
        
        # initialize media devices
        self.pjsua_endpoint.audDevManager().setNullDev()
        
        
        self.is_listening = True
        
    def add_phone(self, phone):
        self.softphones.append(phone)
        
    def remove_phone(self, phone):
        self.softphones.remove(phone)
        if len(self.softphones) == 0:
            self.pjsua_account.shutdown()
            self.pjsua_endpoint.libDestroy()
