# API

## Pyckup
Instances of the Pyckup class are able to perform outgoing calls or to receive incoming calls that are then handled by guiding users through predefined conversation configs.

### Constructor
```python
def __init__(
        self,
        sip_credentials_path: str,
        log_dir: Optional[str] = None,
        realtime: bool = True,
    ) -> None:
``` 
#### Description
Create an instance of the Pyckup class.
#### Arguments
-   `sip_credentials_path (str)`: The file path to the SIP credentials.
-   `log_dir (str, optional)`: The directory where logs will be saved. If None, logging is disabled. Defaults to None.
-   `realtime (bool, optional)`: Whether to use the OpenAI realtime API. Defaults to True.

### call_number
```python
def call_number(
        self,
        phone_number: str,
        conversation_config_path: str,
        enable_logging: bool = True,
    ) -> None:
``` 
#### Description
Initiate a call to a phone number and lead recipient through the specified conversation.
#### Arguments
-   `phone_number (int)`: The phone number to call in E.164 format.
-   `conversation_config_path (str)`: The file path to the conversation configuration.
-   `enable_logging (bool, optional)`: Whether to save a log of the conversation. Defaults to True.

### call_numbers
```python
def call_numbers(
        self,
        phone_numbers: List[str],
        conversation_config_path: str,
        enable_logging: bool = True,
    ) -> None:
``` 
#### Description
Call a list of phone numbers and lead them through the specified conversation.
#### Arguments
-   `phone_numbers (List[str])`: A list of phone numbers to call in E.164 format.
-   `conversation_config_path (str)`: The file path to the conversation configuration.
-   `enable_logging (bool, optional)`: Whether to save a log of the conversation. Defaults to True.

### start_listening
```python
def start_listening(
        self,
        conversation_config_path: str,
        num_devices: int = 1,
        enable_logging: bool = True,
    ) -> SoftphoneGroup:
``` 
#### Description
Start listening for incoming calls.
#### Arguments
-   `conversation_path (str)`: The file path to the conversation configuration YAML file.
-   `num_devices (int, optional)`: The number of softphone devices (= number of concurrent calls) to initialize. Defaults to 1.
-   `enable_logging (bool, optional)`: Whether to save a log of the conversation. Only works if log_dir has been set. Defaults to True.
#### Returns
-   SoftphoneGroup: The group of softphones that are listening for incoming calls.

### stop_listening
```python
def stop_listening(self, sf_group: SoftphoneGroup) -> None:
``` 
#### Description
Stop listening for incoming calls on the specified softphone group.
#### Arguments
-   `sf_group (SoftphoneGroup)`: The group of softphones that should stop listening.



## Softphone
Each Pyckup instance owns a group of Softphone instances that are used to perform and handle calls. You can create Sofphone instances independently to access the full functionality of a SIP softphone.

### Constructor
```python
def __init__(
        self, credentials_path: str
    ) -> None:
``` 
#### Description
Initialize a Softphone instance with the provided SIP credentials. Used to make and answer calls and perform various call actions (e.g. hangup, forward, say, play_audio, listen).
#### Arguments
-   `credentials_path (str)`: The file path to the SIP credentials.

### call
```python
def get_id(self) -> str:
``` 
#### Description
Get the unique ID of the Softphone instance.
#### Returns
-   str: The unique ID of the softphone instance.

### call
```python
def call(self, phone_number: str) -> None:
``` 
#### Description
Initiate a call to the specified phone number.
#### Arguments
-   `phone_number (str)`: The phone number to call in E.164 format.

### wait_for_stop_calling
```python
def wait_for_stop_calling(self, timeout: Optional[float] = None) -> None:
``` 
#### Description
Wait for the active call to stop ringing. Holds program execution.
#### Arguments
-   `timeout (float, optional)`: The maximum time to wait in seconds. If None, waits indefinitely. Defaults to None.

### forward_call
```python
def forward_call(self, phone_number: str, timeout: Optional[float] = None) -> bool:
``` 
#### Description
Attempt to forward the current call to a specified phone number. A seperate call will be made and the two calls will be paired.
#### Arguments
-   `phone_number (str)`: The phone number to forward the call to in E.164 format.
-    `timeout (float, optional)`: The maximum time to wait for the forwarded call to be picked up in seconds. If None, waits indefinitely. Defaults to None.
#### Returns
-   bool: True if the call was successfully forwarded, False otherwise.

### hangup
```python
def hangup(self, paired_only: bool = False) -> None:
``` 
#### Description
Hang up the current call(s) and clean up artifacts.
#### Arguments
-   `paired_only (bool, optional)`: If True, only the paired call is hung up. If False, both active and paired call are hung up. Defaults to False.

### is_forwarded
```python
def is_forwarded(self) -> bool:
``` 
#### Description
Check if the current call is forwarded.
#### Returns
-   bool: True if the current call is forwarded, False otherwise.

### has_picked_up_call
```python
def has_picked_up_call(self) -> bool:
``` 
#### Description
Check if the active call has been picked up.
#### Returns
-   bool: True if the active call has been picked up, otherwise False.

### has_paired_call
```python
def has_paired_call(self) -> bool:
``` 
#### Description
Check if the paired call has been picked up.
#### Returns
-   bool: True if the paired call has been picked up, False otherwise.

### get_called_phone_number
```python
def get_called_phone_number(self) -> Optional[str]:
``` 
#### Description
Get the phone number of the active call.
#### Returns
-   str: The phone number of the active call.

### say
```python
def say(self, message: str, cache_audio: bool = False) -> None:
``` 
#### Description
Read out a message as audio to the active call.
#### Arguments
-   `message (str)`: The message to be converted to speech and streamed to the call.
-   `cache_audio (bool, optional)`: If True, the audio will be cached for future use. Defaults to False.

### play_audio
```python
def play_audio(self, audio_file_path: str, do_loop: bool = False) -> None:
``` 
#### Description
Play an audio file to the active call.
#### Arguments
-   `audio_file_path (str)`: The file path to the audio file to be played.
-   `do_loop (bool, optional)`: Whether to loop the audio file. Defaults to False.

### stop_audio
```python
def stop_audio(self) -> None:
``` 
#### Description
Stop playing audio to the active call.
        

            

            





            




