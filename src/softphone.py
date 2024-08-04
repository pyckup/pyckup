import json
import os
from pathlib import Path
import time
import pjsua2 as pj
import pyttsx3

HERE = Path(os.path.abspath(__file__)).parent

class softphone_call(pj.Call):
    tts_engine = None
    media_player = None
    media_recorder = None
    softphone = None
    
    def __init__(self, acc, softphone):
        self.media_player = None
        self.media_recorder = None    
        self.softphone = softphone
        super(softphone_call, self).__init__(acc)
        # self.tts_engine = pyttsx3.init()

        
    
    def onCallState(self, prm):
        call_info = self.getInfo()
        if call_info.state == pj.PJSIP_INV_STATE_DISCONNECTED:
            self.softphone.hangup()

        
          
                
    # def say(self, message):
    #     call_info = self.getInfo()
    #     for i in range(len(call_info.media)):
    #         if call_info.media[i].type == pj.PJMEDIA_TYPE_AUDIO and call_info.media[i].status == pj.PJSUA_CALL_MEDIA_ACTIVE:
    #             call_media = self.getAudioMedia(i)

    #             # Create WAV from message
    #             self.tts_engine.save_to_file(message, str(HERE / '../artifacts/outgoing.wav'))
    #             self.tts_engine.runAndWait()
                
    #             self.media_player = pj.AudioMediaPlayer()
    #             self.media_player.createPlayer(str(HERE / "../artifacts/outgoing.wav"), pj.PJMEDIA_FILE_NO_LOOP)
    #             self.media_player.startTransmit(call_media)
                
    #             return
    #     print("No available audio media")
                
    # def recordAudio(self):
    #     ci = self.getInfo()
    #     for i in range(len(ci.media)):
    #         if ci.media[i].type == pj.PJMEDIA_TYPE_AUDIO and self.getMedia(i) and ci.media[i].status == pj.PJSUA_CALL_MEDIA_ACTIVE:
    #             self.media_recorder = pj.AudioMediaRecorder()
    #             self.media_recorder.createRecorder(str(HERE / "../artifacts/recording.wav"))
    #             print('recording audio')
    #             self.getAudioMedia(i).startTransmit(self.media_recorder)
    #             time.sleep(5)
    #             self.getAudioMedia(i).stopTransmit(self.media_recorder)
    #             self.media_recorder = None
    #             print('saved recording to artifacts/recording.wav')

class softphone:
    pjsua_endpoint = None
    pjsua_account = None
    active_call = None
    
    def __init__(self):
        # Load SIP Credentials
        credentials_path = os.environ['SIP_CREDENTIALS_PATH']
        with open(credentials_path, 'r') as f:
            sip_credentials = json.load(f)
            
        # Initialize PJSUA2 endpoint
        ep_cfg = pj.EpConfig()
        ep_cfg.uaConfig.threadCnt = 2
        ep_cfg.logConfig.level = 1
        ep_cfg.logConfig.consoleLevel = 1
        self.pjsua_endpoint = pj.Endpoint()
        self.pjsua_endpoint.libCreate()
        self.pjsua_endpoint.libInit(ep_cfg)

        sipTpConfig = pj.TransportConfig()
        sipTpConfig.port = 0#5060;
        self.pjsua_endpoint.transportCreate(pj.PJSIP_TRANSPORT_UDP, sipTpConfig)
        self.pjsua_endpoint.libStart()
        
        # WSL has no audio device, therefore use null device
        self.pjsua_endpoint.audDevManager().setNullDev()

        # Create SIP Account
        acfg = pj.AccountConfig()
        acfg.idUri = sip_credentials['idUri']
        acfg.regConfig.registrarUri = sip_credentials['registrarUri']
        cred = pj.AuthCredInfo("digest", "*", sip_credentials['username'], 0, sip_credentials['password'])
        acfg.sipConfig.authCreds.append(cred)

        self.pjsua_account = pj.Account()
        self.pjsua_account.create(acfg)
    
    def has_picked_up_call(self):
        if self.active_call:
            call_info = self.active_call.getInfo()
            return call_info.state == pj.PJSIP_INV_STATE_CONFIRMED
        return False
    
    def call(self, sip_number): 
        if self.active_call:
            print("Can't call: There is a call already in progress.")
        
        self.active_call = softphone_call(self.pjsua_account, self)
        call_op_param = pj.CallOpParam(True)
        self.active_call.makeCall(sip_number, call_op_param)
    
    def wait_for_stop_calling(self):
        call_info = self.active_call.getInfo()
        while(self.active_call and call_info.state == pj.PJSIP_INV_STATE_CALLING):
            time.sleep(0.2)
            call_info = self.active_call.getInfo()
    
    def hangup(self):
        if not self.active_call:
            print("Can't hangup: No call in progress.")
            return

        self.active_call.hangup(pj.CallOpParam(True))
        del self.active_call
        print('hung up')