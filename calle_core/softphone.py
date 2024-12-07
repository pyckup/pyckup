import hashlib
import json
import os
from pathlib import Path
import threading
import time
import wave
from openai import OpenAI
import pjsua2 as pj
from pydub import AudioSegment
import numpy as np
import yaml
import uuid
import glob
import traceback

HERE = Path(os.path.abspath(__file__)).parent


class SoftphoneCall(pj.Call):

    softphone = None
    __is_paired = False

    def __init__(self, acc, softphone, call_id=pj.PJSUA_INVALID_ID, paired=False):
        """
        Initialize a SoftphoneCall instance, inheriting PJSUA2's Call class.

        Args:
            acc (Account): The SIP account associated with the call.
            softphone (Softphone): The softphone instance managing the call.
            call_id (int, optional): The ID of the call. Defaults to pj.PJSUA_INVALID_ID.
            paired (bool, optional): Whether the call is paired. Defaults to False.
        """
        super(SoftphoneCall, self).__init__(acc, call_id)
        self.softphone = softphone
        self.__is_paired = paired

    def onCallState(self, prm):
        if not self.softphone:
            return

        # hang up the softphone after the call is no longer active
        call_info = self.getInfo()
        if (
            call_info.state == pj.PJSIP_INV_STATE_DISCONNECTED
            or call_info.state == pj.PJSIP_INV_STATE_NULL
        ):
            self.softphone.hangup(paired_only=self.__is_paired)

        super(SoftphoneCall, self).onCallState(prm)


class GroupAccount(pj.Account):
    """
    Initialize a GroupAccount instance, inheriting PJSUA2's Account class. All softphones associated
    with the same group are going to share this SIP account.
    Args:
        group (SoftphoneGroup): The softphone group associated with this account.
    """

    def __init__(self, group):
        self.__group = group
        super(GroupAccount, self).__init__()

    def onIncomingCall(self, prm):
        # try to answer call using one of the available group softphones
        for phone in self.__group.softphones:
            if phone.active_call:
                continue

            call = SoftphoneCall(self, phone, prm.callId)

            call_op_param = pj.CallOpParam()
            call_op_param.statusCode = pj.PJSIP_SC_OK
            call.answer(call_op_param)
            phone.active_call = call
            return

        # no available phone found, hangup
        call = SoftphoneCall(self, None, prm.callId)
        call_op_param = pj.CallOpParam(True)
        call.hangup(call_op_param)


class Softphone:
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

    def __init__(self, credentials_path, group=None):
        """
        Initialize a Softphone instance with the provided SIP credentials and softphone group. Used to make
        and answer calls and perform various call actions (e.g. hangup, forward, say, play_audio, listen).

        Args:
            credentials_path (str): The file path to the SIP credentials.
            group (SoftphoneGroup, optional): The group to which the softphone belongs. If None, a new group is created, containing just this softphone.

        Returns:
            None
        """
        # Load config
        with open(HERE / "../conf/softphone_config.yaml", "r") as config_file:
            self.__config = yaml.safe_load(config_file)

        if group:
            self.__group = group
        else:
            self.__group = SoftphoneGroup(credentials_path)
        self.__group.add_phone(self)

        self.__id = uuid.uuid4()
        self.__paired_call = None

        self.__media_player_1 = None
        self.__media_player_2 = None
        self.__media_recorder = None

        # Initialize OpenAI
        self.__openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        
        # Ensure cache directory exists
        if not os.path.exists(HERE / "../cache"):
            os.makedirs(HERE / "../cache")

    def __del__(self):
        self.__media_player_1 = None
        self.__media_player_2 = None
        self.__media_recorder = None
        self.__group.remove_phone(self)
        
    def get_id(self):
        """
        Get the unique ID of the softphone instance.
        
        Returns:
            str: The unique ID of the softphone instance.
        """
        return self.__id

    def __remove_artifacts(self):
        """
        Remove artifacts (mostly incoming and outgoing audio files) associated with the current softphone instance.

        Returns:
            None
        """
        artifacts = glob.glob(os.path.join(HERE / "../artifacts/", f"{self.__id}*"))
        for artifact in artifacts:
            if os.path.isfile(artifact):
                try:
                    os.remove(artifact)
                except FileNotFoundError:
                    print(
                        f"File {artifact} not found. It might have been deleted already."
                    )
                except Exception as e:
                    print(
                        f"An error occurred while trying to delete the file {artifact}: {e}"
                    )

    def call(self, phone_number):
        """
        Initiate a call to the specified phone number.

        Args:
            phone_number (str): The phone number to call in E.164 format.

        Returns:
            None
        """
        if self.active_call:
            print("Can't call: There is a call already in progress.")

        # construct SIP adress
        registrar = self.__group.sip_credentials["registrarUri"].split(":")[1]
        sip_adress = "sip:" + phone_number + "@" + registrar

        # make call
        self.active_call = SoftphoneCall(self.__group.pjsua_account, self)
        call_op_param = pj.CallOpParam(True)
        self.active_call.makeCall(sip_adress, call_op_param)

    def forward_call(self, phone_number, timeout=None):
        """
        Attempt to forward the current call to a specified phone number. A seperate call will be made and the
        two calls will be connected.

        Args:
            phone_number (str): The phone number to forward the call to in E.164 format.
            timeout (float, optional): The maximum time to wait for the forwarded call to be picked up in seconds. If None, waits indefinitely. Defaults to None.

        Returns:
            bool: True if the call was successfully forwarded, False otherwise.
        """
        if not self.active_call:
            print("Can't forward call: No call in progress.")
            return False

        if self.__paired_call:
            print("Can't forward call: Already in forwarding session.")
            return False

        print("Forwarding call...")

        # construct SIP adress
        registrar = self.__group.sip_credentials["registrarUri"].split(":")[1]
        sip_adress = "sip:" + phone_number + "@" + registrar

        # make call to forwarded number
        self.__paired_call = SoftphoneCall(
            self.__group.pjsua_account, self, paired=True
        )
        call_op_param = pj.CallOpParam(True)
        self.__paired_call.makeCall(sip_adress, call_op_param)

        # wait for pick up
        self.__wait_for_stop_calling("paired", timeout=timeout)

        if not self.__has_picked_up_call("paired"):
            print("Call not picked up.")
            if self.__paired_call:
                self.__paired_call.hangup(pj.CallOpParam(True))
                self.__paired_call = None
            return False

        # connect audio medias of both calls
        active_call_media = None
        paired_call_media = None

        active_call_info = self.active_call.getInfo()
        for i in range(len(active_call_info.media)):
            if (
                active_call_info.media[i].type == pj.PJMEDIA_TYPE_AUDIO
            ):  # and active_call_info.media[i].status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                active_call_media = self.active_call.getAudioMedia(i)

        paired_call_info = self.__paired_call.getInfo()
        for i in range(len(paired_call_info.media)):
            if (
                paired_call_info.media[i].type == pj.PJMEDIA_TYPE_AUDIO
            ):  # and paired_call_info.media[i].status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                paired_call_media = self.__paired_call.getAudioMedia(i)

        if not active_call_media or not paired_call_media:
            print("No audio media available.")
            self.__paired_call = None
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
        """
        Check if the specified call (active call or paired call) has been picked up.

        Args:
            call_type (str, optional): The type of call to check. Can be "active" or "paired". Defaults to "active".

        Returns:
            bool: True if the specified call has been successfully picked up, otherwise False.
        """
        if call_type == "active":
            call = self.active_call
        elif call_type == "paired":
            call = self.__paired_call
        else:
            return False

        if call:
            call_info = call.getInfo()
            for i in range(call_info.media.size()):
                if call_info.media[i].type == pj.PJMEDIA_TYPE_AUDIO and call.getMedia(
                    i
                ):
                    return True
        return False

    def has_picked_up_call(self):
        """
        Check if the active call has been picked up.

        Returns:
            bool: True if the active call has been picked up, otherwise False.
        """
        return self.__has_picked_up_call("active")
    
    def has_paired_call(self):
        """
        Check if the paired call has been picked up.

        Returns:
            bool: True if the paired call has been picked up, otherwise False.
        """
        return self.__has_picked_up_call("paired")

    def __wait_for_stop_calling(self, call_type="active", timeout=None):
        """
        Wait for the specified call (active call or paired call) to stop ringing. Holds program execution.

        Args:
            call_type (str, optional): The type of call to check. Can be "active" or "paired". Defaults to "active".
            timeout (float, optional): The maximum time to wait in seconds. If None, waits indefinitely. Defaults to None.

        Returns:
            None
        """
        if call_type == "active":
            call = self.active_call
        elif call_type == "paired":
            call = self.__paired_call
        else:
            return

        if not call:
            return

        waited_time = 0
        call_info = call.getInfo()
        while (
            (call_info.state == pj.PJSIP_INV_STATE_CALLING
            or call_info.state == pj.PJSIP_INV_STATE_EARLY)
            and (not timeout or waited_time < timeout)
        ):
            try:
                time.sleep(0.2)
                waited_time += 0.2
                if not call:
                    return
                call_info = call.getInfo()
            except Exception as e:
                return

    def wait_for_stop_calling(self, timeout=None):
        """
        Wait for the active call to stop ringing. Holds program execution.
        
        Args:
            timeout (float, optional): The maximum time to wait in seconds. If None, waits indefinitely. Defaults to None.

        Returns:
            None
        """
        self.__wait_for_stop_calling("active", timeout)

    def hangup(self, paired_only=False):
        """
        Hang up the current call(s) and clean up artifacts.

        Args:
            paired_only (bool, optional): If True, only the paired call is hung up. If False,
            both active and paired call are hung up. Defaults to False.

        Returns:
            None
        """
        if self.__paired_call:
            self.__paired_call.hangup(pj.CallOpParam(True))
            self.__paired_call = None
            
        if paired_only:
            return
        
        if self.active_call:
            self.active_call.hangup(pj.CallOpParam(True))
            self.active_call = None

        self.__remove_artifacts()
        
    def __get_message_hash(self, message):
        """
        Calculate the hash of a given string message using SHA-256.

        Args:
            message (str): The input string to hash.

        Returns:
            str: The hexadecimal representation of the hash.
        """
        sha256_generator = hashlib.sha256()
        sha256_generator.update(message.encode('utf-8'))
        return sha256_generator.hexdigest()

    def say(self, message, cache_audio=False):
        """
        Read out a message as audio to the active call.

        Args:
            message (str): The message to be converted to speech and streamed to the call.

        Returns:
            None
        """
        if not self.active_call:
            print("Can't say: No call in progress.")
            return
        if self.__paired_call:
            print("Can't say: Call is in forwarding session.")
            return
        
        # Setup audio media
        call_info = self.active_call.getInfo()
        for i in range(len(call_info.media)):
            if (
                call_info.media[i].type == pj.PJMEDIA_TYPE_AUDIO
                and call_info.media[i].status == pj.PJSUA_CALL_MEDIA_ACTIVE
            ):
                call_media = self.active_call.getAudioMedia(i)
                
                # -- Scan for cached audio file --
                message_hash = self.__get_message_hash(message)
                cached_audio_path = os.path.join(HERE / "../cache", f"{message_hash}.wav")
                if os.path.isfile(cached_audio_path):
                    if self.__media_player_1:
                        self.__media_player_1.stopTransmit(call_media)
                    if self.__media_player_2:
                        self.__media_player_2.stopTransmit(call_media)

                    cached_audio = AudioSegment.from_wav(str(cached_audio_path))
                    self.__media_player_1 = pj.AudioMediaPlayer()
                    self.__media_player_1.createPlayer(str(cached_audio_path), pj.PJMEDIA_FILE_NO_LOOP)
                    self.__media_player_1.startTransmit(call_media)
                    
                    time.sleep(cached_audio.duration_seconds)
                    
                    self.__media_player_1.stopTransmit(call_media)
                    return
                
                # -- Recieve TTS audio from OpenAI and stream it using double buffering --
                # Setup buffer files
                try:
                    silence = np.zeros(1024, dtype=np.int16).tobytes()
                    with wave.open(
                        str(HERE / f"../artifacts/{self.__id}_outgoing_buffer_0.wav"),
                        "wb",
                    ) as buffer_0:
                        buffer_0.setnchannels(self.__config["tts_channels"])
                        buffer_0.setsampwidth(self.__config["tts_sample_width"])
                        buffer_0.setframerate(self.__config["tts_sample_rate"])
                        buffer_0.writeframes(silence)

                    with wave.open(
                        str(HERE / f"../artifacts/{self.__id}_outgoing_buffer_1.wav"),
                        "wb",
                    ) as buffer_1:
                        buffer_1.setnchannels(self.__config["tts_channels"])
                        buffer_1.setsampwidth(self.__config["tts_sample_width"])
                        buffer_1.setframerate(self.__config["tts_sample_rate"])
                        buffer_1.writeframes(silence)

                    # stream and play response to/from alternating buffer
                    delay = self.__config["tts_chunk_size"] / (
                        self.__config["tts_sample_rate"]
                        * self.__config["tts_sample_width"]
                        * self.__config["tts_channels"]
                    )  # length of each chunk in seconds
                    
                    combined_audio = AudioSegment.empty()

                    with self.__openai_client.audio.speech.with_streaming_response.create(
                        model="tts-1",
                        voice="alloy",
                        input=message,
                        response_format="pcm",
                    ) as response:
                        buffer_switch = True
                        for chunk in response.iter_bytes(
                            chunk_size=self.__config["tts_chunk_size"]
                        ):
                            if chunk and len(chunk) >= 512:
                                if buffer_switch:
                                    buffer_switch = False
                                    # play audio from buffer 0
                                    if self.__media_player_2:
                                        self.__media_player_2.stopTransmit(call_media)
                                    self.__media_player_1 = pj.AudioMediaPlayer()
                                    self.__media_player_1.createPlayer(
                                        str(
                                            HERE
                                            / f"../artifacts/{self.__id}_outgoing_buffer_0.wav"
                                        ),
                                        pj.PJMEDIA_FILE_NO_LOOP,
                                    )
                                    self.__media_player_1.startTransmit(call_media)
                                    
                                    # append buffer audio to combined audio
                                    buffered_audio = AudioSegment.from_wav(
                                        str(
                                            HERE
                                            / f"../artifacts/{self.__id}_outgoing_buffer_0.wav"
                                        )
                                    )
                                    combined_audio += buffered_audio
                                         
                                    # write audio to buffer 1
                                    with wave.open(
                                        str(
                                            HERE
                                            / f"../artifacts/{self.__id}_outgoing_buffer_1.wav"
                                        ),
                                        "wb",
                                    ) as buffer_1:
                                        buffer_1.setnchannels(
                                            self.__config["tts_channels"]
                                        )
                                        buffer_1.setsampwidth(
                                            self.__config["tts_sample_width"]
                                        )
                                        buffer_1.setframerate(
                                            self.__config["tts_sample_rate"]
                                        )
                                        buffer_1.writeframes(chunk)
                                        time.sleep(delay)
                                else:
                                    buffer_switch = True
                                    # play audio from buffer 1
                                    if self.__media_player_1:
                                        self.__media_player_1.stopTransmit(call_media)
                                    self.__media_player_2 = pj.AudioMediaPlayer()
                                    self.__media_player_2.createPlayer(
                                        str(
                                            HERE
                                            / f"../artifacts/{self.__id}_outgoing_buffer_1.wav"
                                        ),
                                        pj.PJMEDIA_FILE_NO_LOOP,
                                    )
                                    self.__media_player_2.startTransmit(call_media)
                                    
                                    # append buffer audio to combined audio
                                    buffered_audio = AudioSegment.from_wav(
                                        str(
                                            HERE
                                            / f"../artifacts/{self.__id}_outgoing_buffer_1.wav"
                                        )
                                    )
                                    combined_audio += buffered_audio
                                    
                                    # write audio to buffer 0
                                    with wave.open(
                                        str(
                                            HERE
                                            / f"../artifacts/{self.__id}_outgoing_buffer_0.wav"
                                        ),
                                        "wb",
                                    ) as buffer_0:
                                        buffer_0.setnchannels(
                                            self.__config["tts_channels"]
                                        )
                                        buffer_0.setsampwidth(
                                            self.__config["tts_sample_width"]
                                        )
                                        buffer_0.setframerate(
                                            self.__config["tts_sample_rate"]
                                        )
                                        buffer_0.writeframes(chunk)
                                        time.sleep(delay)

                        # save cache file
                        if cache_audio:
                            combined_audio.export(str(cached_audio_path), format="wav")
                            
                        time.sleep(delay)
                        # play residue audio from last buffer
                        # try:
                        #     if buffer_switch:
                        #         self.__media_player_2.stopTransmit(call_media)
                        #         if self.__media_player_1:
                        #                     self.__media_player_1.stopTransmit(call_media)
                        #         self.__media_player_1 = pj.AudioMediaPlayer()
                        #         self.__media_player_1.createPlayer(str(HERE / f"../artifacts/{self.__id}_outgoing_buffer_0.wav"), pj.PJMEDIA_FILE_NO_LOOP)
                        #         self.__media_player_1.startTransmit(call_media)
                        #         time.sleep(delay)
                        #     else:
                        #         self.__media_player_1.stopTransmit(call_media)
                        #         if self.__media_player_2:
                        #                     self.__media_player_2.stopTransmit(call_media)
                        #         self.__media_player_2 = pj.AudioMediaPlayer()
                        #         self.__media_player_2.createPlayer(str(HERE / f"../artifacts/{self.__id}_outgoing_buffer_1.wav"), pj.PJMEDIA_FILE_NO_LOOP)
                        #         self.__media_player_2.startTransmit(call_media)
                        #         time.sleep(delay)
                        # except Exception as e:
                        #     print('Error when playing residue audio buffer', e)
                        #     traceback.print_exc()
                except Exception as e:
                    print(
                        "Error occured while speaking (probably because user hung up):",
                        e,
                    )
                    traceback.print_exc()
                return
        print("No available audio media")

    def play_audio(self, audio_file_path, do_loop=False):
        """
        Play an audio file to the active call.

        Args:
            audio_file_path (str): The file path to the audio file to be played.
            do_loop (bool, optional): Whether to loop the audio file. Defaults to False.

        Returns:
            None
        """
        if not self.active_call:
            print("Can't play audio: No call in progress.")
            return
        if self.__paired_call:
            print("Can't play audio: Call is in forwarding session.")
            return

        call_info = self.active_call.getInfo()
        for i in range(len(call_info.media)):
            if (
                call_info.media[i].type == pj.PJMEDIA_TYPE_AUDIO
                and call_info.media[i].status == pj.PJSUA_CALL_MEDIA_ACTIVE
            ):
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
        """
        Listen for incoming audio on the incoming call and transcribe it to text. Listens as long
        as a certain decibel level is maintained.

        Returns:
            str: The transcribed text from the recorded audio.
        """
        # skip silence
        if not self.__record_incoming_audio(self.__config["silence_sample_interval"]):
            return "##INTERRUPTED##"

        last_segment = AudioSegment.from_wav(
            str(HERE / f"../artifacts/{self.__id}_incoming.wav")
        )
        while last_segment.dBFS < self.__config["silence_threshold"]:

            if not self.active_call or self.__paired_call:
                return ""

            if not self.__record_incoming_audio(
                self.__config["silence_sample_interval"]
            ):
                return "##INTERRUPTED##"
            last_segment = AudioSegment.from_wav(
                str(HERE / f"../artifacts/{self.__id}_incoming.wav")
            )

        # record audio while over silence threshold
        combined_segments = last_segment
        active_threshold = self.__config["silence_threshold"]
        
        while last_segment.dBFS > active_threshold:
            
            # adapt thrshold to current noise level
            active_threshold = last_segment.dBFS - 5

            if not self.active_call or self.__paired_call:
                return ""

            if not self.__record_incoming_audio(
                self.__config["speaking_sample_interval"]
            ):
                return "##INTERRUPTED##"
            last_segment = AudioSegment.from_wav(
                str(HERE / f"../artifacts/{self.__id}_incoming.wav")
            )
            combined_segments += last_segment

        # output combined audio to file
        combined_segments.export(
            str(HERE / f"../artifacts/{self.__id}_incoming_combined.wav"), format="wav"
        )

        # transcribe audio
        audio_file = open(
            str(HERE / f"../artifacts/{self.__id}_incoming_combined.wav"), "rb"
        )
        transcription = self.__openai_client.audio.transcriptions.create(
            model="whisper-1", file=audio_file
        )
        return transcription.text

    def __record_incoming_audio(self, duration=1.0, unavailable_media_timeout=60):
        """
        Record incoming audio from the active call for a specified duration and save it as an artifact WAVE file.

        Args:
            duration (float, optional): The duration in seconds to record the audio. Defaults to 1.0.
            unavailable_media_timeout (int, optional): The timeout in seconds to wait if call media becomes unavailable (eg. due to holding the call). Defaults to 60.

        Returns:
            bool: True if the recording was successful, False otherwise.
        """
        waited_on_media = 0
        while waited_on_media < unavailable_media_timeout:
            call_info = self.active_call.getInfo()
            for i in range(len(call_info.media)):
                if (
                    call_info.media[i].type == pj.PJMEDIA_TYPE_AUDIO
                    and call_info.media[i].status == pj.PJSUA_CALL_MEDIA_ACTIVE
                ):
                    call_media = self.active_call.getAudioMedia(i)

                    self.__media_recorder = pj.AudioMediaRecorder()
                    self.__media_recorder.createRecorder(
                        str(HERE / f"../artifacts/{self.__id}_incoming.wav")
                    )
                    call_media.startTransmit(self.__media_recorder)
                    time.sleep(duration)

                    # call was terminated while recording.
                    if not self.__media_recorder or not self.active_call:
                        return False
                    
                    # call media no longer active. probably holding. Wait for media.
                    call_info = self.active_call.getInfo()
                    if not call_info.media[i].status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                        call_media.stopTransmit(self.__media_recorder)
                        time.sleep(1)
                        waited_on_media += 1
                        continue

                    # recorded successfully
                    call_media.stopTransmit(self.__media_recorder)
                    del self.__media_recorder
                    return True
              
            # no available call media. probably holding. Wait for media. 
            time.sleep(1)
            waited_on_media += 1
            continue
        
        return False


class SoftphoneGroup:
    pjsua_endpoint = None
    pjsua_account = None
    sip_credentials = None
    softphones = []

    is_listening = False

    def __init__(self, credentials_path):
        """
        Initialize a SoftphoneGroup instance with the provided SIP credentials. Used to share a single
        PJSUA2 library instance and SIP account among multiple softphones.

        Args:
            credentials_path (str): The file path to the SIP credentials JSON file.

        Returns:
            None
        """
        self.softphones = []

        # Load SIP Credentials
        with open(credentials_path, "r") as f:
            self.sip_credentials = json.load(f)

        # Initialize PJSUA2 endpoint
        ep_cfg = pj.EpConfig()
        ep_cfg.uaConfig.threadCnt = 2
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
        acfg.idUri = self.sip_credentials["idUri"]
        acfg.regConfig.registrarUri = self.sip_credentials["registrarUri"]
        cred = pj.AuthCredInfo(
            "digest",
            "*",
            self.sip_credentials["username"],
            0,
            self.sip_credentials["password"],
        )
        acfg.sipConfig.authCreds.append(cred)

        self.pjsua_account = GroupAccount(self)
        self.pjsua_account.create(acfg)

        # initialize media devices
        self.pjsua_endpoint.audDevManager().setNullDev()

        self.is_listening = True

    def add_phone(self, phone):
        """
        Add a softphone instance to this softphone group.

        Args:
            phone (Softphone): The softphone instance to be added to the group.

        Returns:
            None
        """
        self.softphones.append(phone)

    def remove_phone(self, phone):
        """
        Remove a softphone instance from this softphone group.

        Args:
            phone (Softphone): The softphone instance to be removed from the group.

        Returns:
            None
        """
        self.softphones.remove(phone)
        if len(self.softphones) == 0:
            self.pjsua_account.shutdown()
            self.pjsua_endpoint.libDestroy()
