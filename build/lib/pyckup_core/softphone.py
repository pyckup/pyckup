import hashlib
import io
import json
import os
from pathlib import Path
import queue
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
from typing import Optional, Tuple, Callable

HERE = Path(os.path.abspath(__file__)).parent


class SoftphoneCall(pj.Call):

    softphone = None
    __is_paired = False

    def __init__(
        self,
        acc: pj.Account,
        softphone: "Softphone",
        call_id: int = pj.PJSUA_INVALID_ID,
        paired: bool = False,
    ) -> None:
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

    def onCallState(self, prm: pj.OnCallStateParam) -> None:
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

    def onDtmfDigit(self, prm: pj.OnDtmfDigitParam) -> None:
        for reciever in self.softphone.dtmf_recievers:
            reciever(prm.digit)


class GroupAccount(pj.Account):
    """
    Initialize a GroupAccount instance, inheriting PJSUA2's Account class. All softphones associated
    with the same group are going to share this SIP account.
    Args:
        group (SoftphoneGroup): The softphone group associated with this account.
    """

    def __init__(self, group: "SoftphoneGroup") -> None:
        self.__group = group
        super(GroupAccount, self).__init__()

    def onIncomingCall(self, prm: pj.OnIncomingCallParam) -> None:
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

    def __init__(
        self, credentials_path: str, group: Optional["SoftphoneGroup"] = None
    ) -> None:
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
        with open(HERE / "conf/softphone_config.yaml", "r") as config_file:
            self.__config = yaml.safe_load(config_file)

        if group:
            self.__group = group
        else:
            self.__group = SoftphoneGroup(credentials_path)
        self.__group.add_phone(self)

        self.__id = uuid.uuid4()
        self.active_call = None
        self.__paired_call = None

        self.__media_player_1 = None
        self.__media_player_2 = None
        self.__media_recorder = None

        self.dtmf_recievers = []

        self.__external_incoming_buffer = queue.Queue()
        self.__external_outgoing_buffer = queue.Queue()
        self.__external_incoming_buffer_thread = None
        self.__external_outgoing_buffer_thread = None
        self.__audio_output_lock = (
            threading.Lock()
        )  # determines, which thread uses softphone for output
        self.__prioritize_external_audio = False  # True if next the next external message should be played before internal (say)
        self.__interrupt_audio_output = False  # set to True to interrupt audio output (eg. when user starts speaking)

        self.__audio_input_lock = (
            threading.Lock()
        )  # determines, which thread currently records audio from this softphone
        self.__audio_input_priority_thread = (
            None  # thread that has priority to currently record audio
        )

        # Initialize OpenAI
        self.__openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

        # Ensure cache directory exists
        if not os.path.exists(HERE / "../cache"):
            os.makedirs(HERE / "../cache")

        # Ensure artifacts directory exists
        if not os.path.exists(HERE / "../artifacts"):
            os.makedirs(HERE / "../artifacts")

    def __del__(self) -> None:
        self.__media_player_1 = None
        self.__media_player_2 = None
        self.__media_recorder = None

        if self.__external_incoming_buffer_thread:
            self.__external_incoming_buffer_thread.join()
        if self.__external_outgoing_buffer_thread:
            self.__external_outgoing_buffer_thread.join()

        self.__group.remove_phone(self)

    def get_id(self) -> str:
        """
        Get the unique ID of the softphone instance.

        Returns:
            str: The unique ID of the softphone instance.
        """
        return self.__id

    def __remove_artifacts(self) -> None:
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

    def __get_call_medium(self) -> Optional[pj.AudioMedia]:
        """
        Get the audio media associated with the active call.

        Returns:
            AudioMedia: The audio media associated with the active call.
        """
        if not self.active_call:
            return None

        call_info = self.active_call.getInfo()
        for i in range(len(call_info.media)):
            if (
                call_info.media[i].type == pj.PJMEDIA_TYPE_AUDIO
                and call_info.media[i].status == pj.PJSUA_CALL_MEDIA_ACTIVE
            ):
                return self.active_call.getAudioMedia(i)
        return None
    
    def __get_incoming_audio_path(self) -> str:
        return str(HERE / f"../artifacts/{self.__id}_{threading.get_ident()}_incoming.wav")

    def __external_incoming_buffer_loop(self) -> None:
        """
        Playback audio incoming from external source (eg. OpenAI realtime API), stored in external incoming buffer.
        Assumes chunks of raw PCM audio with sample rate 24000, 16 bit, mono.

        Returns:
            None
        """
        try:
            self.__group.pjsua_endpoint.libRegisterThread(
                "external_incoming_buffer_loop"
            )

            while self.has_picked_up_call():

                # play back incoming audio buffer
                iteration_has_played_audio = False
                while (
                    not self.__external_incoming_buffer.empty()
                ):  # ensure that still incoming packages are caught as part of the same response
                    incoming_audio_chunks = []

                    # wait a bit to catch more packages -> reduces stuttering (but slightly increases response time)
                    if not iteration_has_played_audio:
                        time.sleep(0.3)
                        iteration_has_played_audio = True

                    # empty queue completely
                    while not self.__external_incoming_buffer.empty():
                        audio_chunk = self.__external_incoming_buffer.get()
                        incoming_audio_chunks.append(audio_chunk)

                    if incoming_audio_chunks:
                        if not self.__audio_output_lock.locked():
                            self.__audio_output_lock.acquire()
                        self.__interrupt_audio_output = False
                        self.__prioritize_external_audio = False

                        if not self.has_picked_up_call():
                            self.__audio_output_lock.release()
                            return

                        combined_incoming_audio = b"".join(incoming_audio_chunks)
                        incoming_audio_segment = AudioSegment.from_file(
                            io.BytesIO(combined_incoming_audio),
                            format="raw",
                            frame_rate=24000,
                            channels=1,
                            sample_width=2,
                        )
                        incoming_audio_path = str(
                            HERE / f"../artifacts/{self.__id}_openai_incoming.wav"
                        )
                        incoming_audio_segment.export(incoming_audio_path, format="wav")

                        call_medium = self.__get_call_medium()
                        if not call_medium:
                            self.__audio_output_lock.release()
                            return
                        # use media player 2 for realtime conversation audio
                        self.__media_player_2 = pj.AudioMediaPlayer()
                        self.__media_player_2.createPlayer(
                            incoming_audio_path, pj.PJMEDIA_FILE_NO_LOOP
                        )
                        self.__media_player_2.startTransmit(call_medium)

                        # wait until done speaking or external interruption
                        time_to_wait = incoming_audio_segment.duration_seconds
                        while time_to_wait > 0 and not self.__interrupt_audio_output:
                            time.sleep(0.2)
                            time_to_wait -= 0.2
                        self.__interrupt_audio_output = False

                        if self.__media_player_2:
                            self.__media_player_2.stopTransmit(call_medium)
                            del self.__media_player_2

                # no more incoming packages
                if self.__audio_output_lock.locked() and iteration_has_played_audio:
                    self.__audio_output_lock.release()
                time.sleep(0.2)
        except Exception as e:
            print("Error in external incoming buffer thread:", e)
            traceback.print_exc()
            return

    def __external_outgoing_buffer_loop(self) -> None:
        """
        Record audio to be sent to external source (eg. OpenAI realtime API) and store it in external outgoing buffer.
        Assumes chunks of raw PCM audio with sample rate 24000, 16 bit, mono.

        Returns:
            None
        """
        try:
            self.__group.pjsua_endpoint.libRegisterThread(
                "external_outgoing_buffer_loop"
            )

            while self.has_picked_up_call():

                recording_successful = self.__record_incoming_audio(output_path=self.__get_incoming_audio_path(), duration=0.3)

                if not recording_successful:
                    time.sleep(0.2)
                    continue
                
                outgoing_audio_segment = AudioSegment.from_wav(
                    self.__get_incoming_audio_path()
                )
                outgoing_audio_segment = (
                    outgoing_audio_segment.set_frame_rate(24000)
                    .set_channels(1)
                    .set_sample_width(2)
                )
                outgoing_audio_buffer = io.BytesIO()
                outgoing_audio_segment.export(outgoing_audio_buffer, format="wav")
                outgoing_audio_buffer.seek(0)

                self.__external_outgoing_buffer.put(outgoing_audio_buffer.read())
        except Exception as e:
            print("Error in external outgoing buffer thread:", e)
            traceback.print_exc()
            return

    def handle_external_buffers(self) -> Tuple[queue.Queue, queue.Queue]:
        """
        Start handling the external incoming and outgoing audio buffers in separate threads.

        Returns:
            (queue.Queue, queue.Queue): The external incoming and outgoing audio buffers.
        """
        self.__external_incoming_buffer_thread = threading.Thread(
            target=self.__external_incoming_buffer_loop
        )
        self.__external_outgoing_buffer_thread = threading.Thread(
            target=self.__external_outgoing_buffer_loop
        )

        self.__external_incoming_buffer_thread.start()
        self.__external_outgoing_buffer_thread.start()

        return self.__external_incoming_buffer, self.__external_outgoing_buffer

    def wait_for_external_output_finish(self) -> None:
        """
        Wait until no audio is being output to external sources.

        Returns:
            None
        """
        while self.__prioritize_external_audio:
            time.sleep(0.2)

        self.__audio_output_lock.acquire()
        self.__audio_output_lock.release()

    def call(self, phone_number: str) -> None:
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

    def forward_call(self, phone_number: str, timeout: Optional[float] = None) -> bool:
        """
        Attempt to forward the current call to a specified phone number. A seperate call will be made and the
        two calls will be paired.

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

    def is_forwarded(self) -> bool:
        """
        Check if the current call is forwarded.

        Returns:
            bool: True if the current call is forwarded, False otherwise.
        """
        return self.__paired_call is not None

    def __has_picked_up_call(self, call_type: str = "active") -> bool:
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

    def has_picked_up_call(self) -> bool:
        """
        Check if the active call has been picked up.

        Returns:
            bool: True if the active call has been picked up, otherwise False.
        """
        return self.__has_picked_up_call("active")

    def has_paired_call(self) -> bool:
        """
        Check if the paired call has been picked up.

        Returns:
            bool: True if the paired call has been picked up, otherwise False.
        """
        return self.__has_picked_up_call("paired")

    def get_called_phone_number(self) -> Optional[str]:
        """
        Get the phone number of the active call.

        Returns:
            str: The phone number of the active call.
        """
        if not self.active_call:
            print("Can't get called phone number: No active call.")
            return None

        return self.active_call.getInfo().remoteUri.split("@")[0].split(":")[1]

    def __wait_for_stop_calling(
        self, call_type: str = "active", timeout: Optional[float] = None
    ) -> None:
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
            call_info.state == pj.PJSIP_INV_STATE_CALLING
            or call_info.state == pj.PJSIP_INV_STATE_EARLY
        ) and (not timeout or waited_time < timeout):
            try:
                time.sleep(0.2)
                waited_time += 0.2
                if not call:
                    return
                call_info = call.getInfo()
            except Exception as e:
                return

    def wait_for_stop_calling(self, timeout: Optional[float] = None) -> None:
        """
        Wait for the active call to stop ringing. Holds program execution.

        Args:
            timeout (float, optional): The maximum time to wait in seconds. If None, waits indefinitely. Defaults to None.

        Returns:
            None
        """
        self.__wait_for_stop_calling("active", timeout)

    def hangup(self, paired_only: bool = False) -> None:
        """
        Hang up the current call(s) and clean up artifacts.

        Args:
            paired_only (bool, optional): If True, only the paired call is hung up. If False,
            both active and paired call are hung up. Defaults to False.

        Returns:
            None
        """
        self.__media_player_1 = None
        self.__media_player_2 = None
        self.__media_recorder = None

        if self.__paired_call:
            self.__paired_call.hangup(pj.CallOpParam(True))
            self.__paired_call = None

        if paired_only:
            return

        if self.active_call:
            self.active_call.hangup(pj.CallOpParam(True))
            self.active_call = None

        if self.__external_incoming_buffer_thread:
            self.__external_incoming_buffer_thread.join()
        if self.__external_outgoing_buffer_thread:
            self.__external_outgoing_buffer_thread.join()

        self.__remove_artifacts()

    def __get_message_hash(self, message: str) -> str:
        """
        Calculate the hash of a given string message using SHA-256.

        Args:
            message (str): The input string to hash.

        Returns:
            str: The hexadecimal representation of the hash.
        """
        sha256_generator = hashlib.sha256()
        sha256_generator.update(message.encode("utf-8"))
        return sha256_generator.hexdigest()

    def say(self, message: str, cache_audio: bool = False) -> None:
        """
        Read out a message as audio to the active call.

        Args:
            message (str): The message to be converted to speech and streamed to the call.
            cache_audio (bool, optional): If True, the audio will be cached for future use. Defaults to False.

        Returns:
            None
        """
        if not self.active_call:
            print("Can't say: No call in progress.")
            return
        if self.__paired_call:
            print("Can't say: Call is in forwarding session.")
            return

        if message is None or message == "":
            return

        # check for priority external audio
        while self.__prioritize_external_audio:
            time.sleep(0.2)

        call_medium = self.__get_call_medium()
        if not call_medium:
            print("Can't say: No call media available.")
            return

        # wait for incoming buffer to finish playing
        self.__audio_output_lock.acquire()

        # -- Scan for cached audio file --
        message_hash = self.__get_message_hash(message)
        cached_audio_path = os.path.join(HERE / "../cache", f"{message_hash}.wav")
        if os.path.isfile(cached_audio_path):
            if self.__media_player_1:
                self.__media_player_1.stopTransmit(call_medium)
            if self.__media_player_2:
                self.__media_player_2.stopTransmit(call_medium)

            cached_audio = AudioSegment.from_wav(str(cached_audio_path))
            self.__media_player_1 = pj.AudioMediaPlayer()
            self.__media_player_1.createPlayer(
                str(cached_audio_path), pj.PJMEDIA_FILE_NO_LOOP
            )
            self.__media_player_1.startTransmit(call_medium)

            time.sleep(cached_audio.duration_seconds)

            if self.__media_player_1:
                self.__media_player_1.stopTransmit(call_medium)
            self.__audio_output_lock.release()
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
                                self.__media_player_2.stopTransmit(call_medium)
                            self.__media_player_1 = pj.AudioMediaPlayer()
                            self.__media_player_1.createPlayer(
                                str(
                                    HERE
                                    / f"../artifacts/{self.__id}_outgoing_buffer_0.wav"
                                ),
                                pj.PJMEDIA_FILE_NO_LOOP,
                            )
                            self.__media_player_1.startTransmit(call_medium)

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
                                buffer_1.setnchannels(self.__config["tts_channels"])
                                buffer_1.setsampwidth(self.__config["tts_sample_width"])
                                buffer_1.setframerate(self.__config["tts_sample_rate"])
                                buffer_1.writeframes(chunk)
                                time.sleep(delay)
                        else:
                            buffer_switch = True
                            # play audio from buffer 1
                            if self.__media_player_1:
                                self.__media_player_1.stopTransmit(call_medium)
                            self.__media_player_2 = pj.AudioMediaPlayer()
                            self.__media_player_2.createPlayer(
                                str(
                                    HERE
                                    / f"../artifacts/{self.__id}_outgoing_buffer_1.wav"
                                ),
                                pj.PJMEDIA_FILE_NO_LOOP,
                            )
                            self.__media_player_2.startTransmit(call_medium)

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
                                buffer_0.setnchannels(self.__config["tts_channels"])
                                buffer_0.setsampwidth(self.__config["tts_sample_width"])
                                buffer_0.setframerate(self.__config["tts_sample_rate"])
                                buffer_0.writeframes(chunk)
                                time.sleep(delay)

                # save cache file
                if cache_audio:
                    combined_audio.export(str(cached_audio_path), format="wav")

                time.sleep(delay)

                self.__audio_output_lock.release()
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
            self.__audio_output_lock.release()
        return

    def play_audio(self, audio_file_path: str, do_loop: bool = False) -> None:
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

        self.stop_audio()

        call_medium = self.__get_call_medium()
        if not call_medium:
            print("Can't play audio: No call media available.")
            return

        self.__media_player_1 = pj.AudioMediaPlayer()
        loop_mode = pj.PJMEDIA_FILE_LOOP if do_loop else pj.PJMEDIA_FILE_NO_LOOP
        self.__media_player_1.createPlayer(audio_file_path)
        self.__media_player_1.startTransmit(call_medium)

    def stop_audio(self) -> None:
        """
        Stop playing audio to the active call.

        Returns:
            None
        """

        call_medium = self.__get_call_medium()
        if not call_medium:
            print("Can't stop audio: No call media available.")
            return

        if self.__media_player_1:
            self.__media_player_1.stopTransmit(call_medium)
            del self.__media_player_1
        if self.__media_player_2:
            self.__media_player_2.stopTransmit(call_medium)
            del self.__media_player_2

    def record_audio(
        self,
        output_path: str,
        vad: Optional[bool] = False,
        duration: Optional[float] = None,
    ) -> bool:
        """
        Record audio from the active call for a specified duration and save it as an artifact WAVE file.

        Args:
            output_path (str): The file path to save the recorded audio.
            vad (bool, optional): If True, uses Voice Activity Detection to skip silence. Defaults to False.
            duration (float, optional): The duration in seconds to record the audio. Defaults to 5.0.

        Returns:
            bool: True if the recording was successful, False otherwise.
        """
        if not self.active_call:
            print("Can't record audio: No call in progress.")
            return False

        # user-called recording should always have priority over worker threads
        self.__audio_input_priority_thread = threading.current_thread()
        
         # no AI audio should be played during recording
        while not self.__external_incoming_buffer.empty():
            time.sleep(0.2)
        self.__audio_output_lock.acquire()
        if vad:
            if not self.__skip_silence():
                self.__audio_input_priority_thread = None
                if self.__audio_output_lock.locked():
                    self.__audio_output_lock.release()
                return False
            recording_successful, recorded_audio = self.__record_while_not_silent()
            if not recording_successful:
                self.__audio_input_priority_thread = None
                if self.__audio_output_lock.locked():
                    self.__audio_output_lock.release()
                return False

            recorded_audio.export(output_path, format="wav")
            self.__audio_input_priority_thread = None
            if self.__audio_output_lock.locked():
                    self.__audio_output_lock.release()
            return True
        elif duration:
            if not self.__record_incoming_audio(
                output_path=output_path,
                duration=duration,
            ):
                self.__audio_input_priority_thread = None
                if self.__audio_output_lock.locked():
                    self.__audio_output_lock.release()
                return False
            self.__audio_input_priority_thread = None
            if self.__audio_output_lock.locked():
                    self.__audio_output_lock.release()
            return True
        else:
            raise ValueError("Either vad or duration must be specified.")

    def listen(self) -> str:
        """
        Listen for incoming audio on the incoming call and transcribe it to text. Listens as long
        as a certain decibel level is maintained.

        Returns:
            str: The transcribed text from the recorded audio.
        """
        if not self.__skip_silence():
            return "##INTERRUPTED##"

        if not self.active_call or self.__paired_call:
            return ""

        recording_successful, recorded_audio = self.__record_while_not_silent()

        if not recording_successful:
            return "##INTERRUPTED##"

        if not self.active_call or self.__paired_call:
            return ""

        # output combined audio to file
        recorded_audio.export(
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

    def __record_incoming_audio(
        self,
        output_path: str,
        duration: float = 1.0,
        unavailable_media_timeout: int = 60,
    ) -> bool:
        """
        Record incoming audio from the active call for a specified duration and save it as an artifact WAVE file.

        Args:
            output_path (str): The file path to save the recorded audio.
            duration (float, optional): The duration in seconds to record the audio. Defaults to 1.0.
            unavailable_media_timeout (int, optional): The timeout in seconds to wait if call media becomes unavailable (eg. due to holding the call). Defaults to 60.

        Returns:
            bool: True if the recording was successful, False otherwise.
        """
        # wait for priority thread to stop recording
        while (
            self.__audio_input_priority_thread is not None
            and self.__audio_input_priority_thread != threading.current_thread()
        ):
            time.sleep(0.2)

        self.__audio_input_lock.acquire()
        waited_on_media = 0
        while waited_on_media < unavailable_media_timeout:
            call_info = self.active_call.getInfo()
            for i in range(len(call_info.media)):
                if (
                    call_info.media[i].type == pj.PJMEDIA_TYPE_AUDIO
                    and call_info.media[i].status == pj.PJSUA_CALL_MEDIA_ACTIVE
                ):
                    call_medium = self.active_call.getAudioMedia(i)

                    self.__media_recorder = pj.AudioMediaRecorder()
                    self.__media_recorder.createRecorder(output_path)
                    call_medium.startTransmit(self.__media_recorder)
                    time.sleep(duration)

                    # call was terminated while recording.
                    if not self.__media_recorder or not self.active_call:
                        self.__audio_input_lock.release()
                        return False

                    # call media no longer active. probably holding. Wait for media.
                    call_info = self.active_call.getInfo()
                    if not call_info.media[i].status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                        call_medium.stopTransmit(self.__media_recorder)
                        time.sleep(1)
                        waited_on_media += 1
                        continue

                    # recorded successfully
                    call_medium.stopTransmit(self.__media_recorder)
                    del self.__media_recorder
                    self.__audio_input_lock.release()
                    return True

            # no available call media. probably holding. Wait for media.
            time.sleep(1)
            waited_on_media += 1
            continue

        self.__audio_input_lock.release()
        return False

    def __skip_silence(self) -> bool:
        """
        Wait until incoming audio stream is no longer silent.

        Returns:
            bool: True if recording could be performed successfully, False otherwise.
        """
        if not self.__record_incoming_audio(
            output_path=self.__get_incoming_audio_path(),
            duration=self.__config["silence_sample_interval"],
        ):
            return False

        last_segment = AudioSegment.from_wav(self.__get_incoming_audio_path())
        while last_segment.dBFS < self.__config["silence_threshold"]:

            if not self.active_call or self.__paired_call:
                return ""

            if not self.__record_incoming_audio(
                output_path=self.__get_incoming_audio_path(),
                duration=self.__config["silence_sample_interval"],
            ):
                return False
            last_segment = AudioSegment.from_wav(self.__get_incoming_audio_path())

        return True

    def __record_while_not_silent(self) -> Tuple[bool, Optional[AudioSegment]]:
        """
        Record incoming audio while over silence threshold.

        Returns:
            tuple: A tuple containing a boolean indicating if the recording was successful (bool) and the combined audio segments (AudioSegment).
        """

        self.__record_incoming_audio(
            output_path=self.__get_incoming_audio_path(),
            duration=self.__config["silence_sample_interval"],
        )
        last_segment = AudioSegment.from_wav(self.__get_incoming_audio_path())
        combined_segments = last_segment

        active_threshold = self.__config["silence_threshold"]

        while last_segment.dBFS > active_threshold:

            # adapt thrshold to current noise level
            active_threshold = last_segment.dBFS - 3

            if not self.active_call or self.__paired_call:
                return True, ""

            if not self.__record_incoming_audio(
                output_path=self.__get_incoming_audio_path(),
                duration=self.__config["speaking_sample_interval"],
            ):
                return False, None
            last_segment = AudioSegment.from_wav(self.__get_incoming_audio_path())
            combined_segments += last_segment

        return True, combined_segments

    def prioritize_external_audio(self) -> None:
        """
        Prioritize the playback of external audio over internal audio for the next external message.

        Returns:
            None
        """
        self.__prioritize_external_audio = True

    def add_dtmf_reciever(self, reciever_function: Callable[[str], None]) -> None:
        """
        Subscribe a function to recieve DTMF signals from the active call.

        Returns:
            None
        """
        self.dtmf_recievers.append(reciever_function)

    def remove_dtmf_reciever(self, reciever_function: Callable[[str], None]) -> None:
        """
        Unsubscribe a function from recieving DTMF signals from the active call.

        Returns:
            None
        """
        self.dtmf_recievers.remove(reciever_function)


class SoftphoneGroup:
    pjsua_endpoint = None
    pjsua_account = None
    sip_credentials = None
    softphones = []

    is_listening = False

    def __init__(self, credentials_path: str) -> None:
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

        # Try to create transport on first available port
        port = 5060
        max_port_attempts = 10
        for attempt in range(max_port_attempts):
            try:
                sipTpConfig = pj.TransportConfig()
                sipTpConfig.port = port
                self.pjsua_endpoint.transportCreate(pj.PJSIP_TRANSPORT_UDP, sipTpConfig)
                print(f"Transport created on port {port}")
                break
            except pj.Error as e:
                port += 1
        else:
            raise RuntimeError("No available port for transport")

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

    def add_phone(self, phone: Softphone) -> None:
        """
        Add a softphone instance to this softphone group.

        Args:
            phone (Softphone): The softphone instance to be added to the group.

        Returns:
            None
        """
        self.softphones.append(phone)

    def remove_phone(self, phone: Softphone) -> None:
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
