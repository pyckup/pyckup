import base64
import copy
import json
import os
from pathlib import Path
import time
import traceback
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.output_parsers.string import StrOutputParser
from langchain_core.runnables import RunnableBranch
from langchain_openai import ChatOpenAI
from langchain_community.llms import Ollama
from enum import Enum
import threading
import importlib
import websocket
from typing import Any, Dict, List, Optional, Tuple, Union
from queue import Queue

from pyckup_core.conversation_config import (
    ChoiceItem,
    ChoiceItemBase,
    ConversationConfig,
    ConversationItem,
    FunctionChoiceItem,
    FunctionItem,
    InformationItem,
    PathItem,
    PromptItem,
    ReadItem,
)
from pyckup_core.softphone import Softphone


HERE = Path(os.path.abspath(__file__)).parent

vad_config = {
    "type": "server_vad",
    "threshold": 0.5,
    "prefix_padding_ms": 300,
    "silence_duration_ms": 500,
    "create_response": True
}


class LLMExtractor:
    """
    Initialize the LLMExtractor with the given configuration. The extract is responsible
    for guiding the user through a conversation and extracting information from the user's responses.
    The conversation is defined in the conversation configuration.

    Args:
        conversation_config (ConversationConfig): The conversation configuration object that defines the conversation flow.
        llm_provider (str, optional): The LLM provider to use. Options are "openai" and "ollama". Defaults to "openai".
        softphone (object, optional): Softphone used for passing to function calls. Defaults to None.
        realtime (bool, optional): Whether to use the OpenAI realtime API. Defaults to True.
        incoming_buffer (Queue, optional): Queue for incoming audio data. Defaults to None Has to be specified when using realtime.
        outgoing_buffer (Queue, optional): Queue for outgoing audio data. Defaults to None. Has to be specified when using realtime.

    """

    def __init__(
        self,
        conversation_config: ConversationConfig,
        llm_provider: str = "openai",
        softphone: Optional[Any] = None,
        realtime: bool = True,
        incoming_buffer: Optional[Queue] = None,
        outgoing_buffer: Optional[Queue] = None,
    ) -> None:
        if llm_provider == "openai":
            self.__llm = ChatOpenAI(
                api_key=os.environ["OPENAI_API_KEY"], model="gpt-4-turbo-preview"
            )
        elif llm_provider == "ollama":
            self.__llm = Ollama(model="gemma2:2b-instruct-q3_K_M")
        else:
            raise ValueError("Invalid LLM provider. Options: openai, llama.")

        self.status = ExtractionStatus.IN_PROGRESS
        self.chat_history = []

        self.__softphone: Softphone = softphone

        self.__conversation_config: ConversationConfig = copy.deepcopy(
            conversation_config
        )
        self.__load_conversation_path("entry")
        self.__conversation_state = (
            {}
        )  # includes extracted information and can be used to store data conversation-wide
        self.__conversation_state_lock = (
            threading.Lock()
        )  # acquire this before accessing the conversation state
        self.__repeat_item = (
            None  # if information filtering failed, the item is repeated
        )

        softphone.add_dtmf_reciever(self.__check_dialled_choice)
        self.__dialled_choice = None  # the choice selected by user dial input

        self.information_extraction_chain = self.__verify_information | RunnableBranch(
            (
                lambda data: data["information_verification_status"] == "YES",
                self.__information_extraction_successful,
            ),
            (
                lambda data: data["information_verification_status"] == "NO",
                self.__make_information_extractor,
            ),
            self.__extraction_aborted,
        )

        self.choice_extraction_chain = self.__verify_choice | RunnableBranch(
            (
                lambda data: data["choice"] == "##NONE##",
                self.__make_choice_extractor,
            ),
            (
                lambda data: data["choice"] == "##ABORT##",
                self.__extraction_aborted,
            ),
            self.__choice_extraction_successful,
        )

        # realtime stuff
        self.__realtime = realtime
        self.__realtime_connection = None
        self.__incoming_buffer = incoming_buffer
        self.__outgoing_buffer = outgoing_buffer
        self.__current_item_messages = []
        self.__current_item_callback_args = ""
        self.__current_item_in_progress = False
        self.__response_done = False  # True if model has processed most recent prompt
        self.__accepts_user_input = False  # Is user supposed to talk right now?

        if self.__realtime:
            self.__webclient_thread = threading.Thread(
                target=self.__run_realtime_client
            )
            self.__outgoing_buffer_thread = threading.Thread(
                target=self.__send_outgoing_buffer
            )

            self.__webclient_thread.start()
            self.__outgoing_buffer_thread.start()

    def __del__(self) -> None:
        self.__softphone.remove_dtmf_reciever(self.__check_dialled_choice)

        if self.__realtime_connection:
            self.__realtime_connection.close()
            self.__webclient_thread.join()
            self.__outgoing_buffer_thread.join()

    def __run_realtime_client(self) -> None:
        """
        Establish and manage a real-time WebSocket connection with the OpenAI API.

        Event Handlers:
            on_open(ws): Sends session parameters to the server when the connection is opened.
            on_message(ws, message): Processes incoming messages from the server.
            on_error(error): Prints the error message and stack trace if an error occurs.

        Returns:
            None
        """

        def on_open(ws):
            # set up session parameters
            event = {
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "instructions": "You will lead the user through a conversation, where the current topic is given through system prompts to you. Don't say anything in the beginning.",
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "turn_detection": None,
                    "tools": [],
                    "input_audio_transcription": {"model": "whisper-1"},
                },
            }
            ws.send(json.dumps(event))

        def on_message(ws, message):
            server_event = json.loads(message)
            if server_event["type"] == "response.audio.delta":
                # append incoming audio to buffer
                encoded_audio = server_event["delta"]
                audio_bytes = base64.b64decode(encoded_audio)
                self.__incoming_buffer.put(audio_bytes)
            elif server_event["type"] == "response.done":
                self.__response_done = True
                # track transcribed model output
                if (
                    len(server_event["response"]["output"]) <= 0
                    or hasattr(server_event["response"]["output"][0], "content")
                    == False
                ):
                    return
                model_response = server_event["response"]["output"][0]["content"][0][
                    "transcript"
                ]
                self.__current_item_messages.append(AIMessage(content=model_response))
            elif (
                server_event["type"]
                == "conversation.item.input_audio_transcription.completed"
            ):
                # track transcribed user input
                user_input = server_event["transcript"]
                self.__current_item_messages.append(HumanMessage(content=user_input))
            elif server_event["type"] == "response.function_call_arguments.done":
                # callback sent from model
                self.__current_item_callback_args = json.loads(
                    server_event["arguments"]
                )
                self.__current_item_in_progress = False

        def on_error(error):
            print(f"An error occured in OpenAI realtime connection: {error}")
            traceback.print_exc()

        api_url = (
            "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2025-06-03"
        )
        headers = [
            f"Authorization: Bearer {os.environ['OPENAI_API_KEY']}",
            "OpenAI-Beta: realtime=v1",
        ]

        self.__realtime_connection = websocket.WebSocketApp(
            api_url,
            header=headers,
            on_message=on_message,
            on_error=lambda ws, error: on_error(error),
        )
        self.__realtime_connection.on_open = on_open
        self.__realtime_connection.run_forever()

    def __send_outgoing_buffer(self) -> None:
        """
        Send outgoing audio buffer encoded as base64 to the OpenAI realtime API.

        Returns:
            None
        """
        while self.status == ExtractionStatus.IN_PROGRESS:
            if self.__realtime_connection is None:
                time.sleep(0.2)
                continue

            audio_bytes = self.__outgoing_buffer.get()

            if not self.__accepts_user_input:
                continue

            encoded_audio = base64.b64encode(audio_bytes).decode("utf-8")
            append_event = {"type": "input_audio_buffer.append", "audio": encoded_audio}
            self.__realtime_connection.send(json.dumps(append_event))

    def __load_conversation_path(self, conversation_path: str) -> None:
        """
        Load items from the specified conversation path into the current conversation.

        Args:
            conversation_path (str): Name of the path in the configuration.

        Raises:
            KeyError: If the conversation path does not exist in the configuration.
        """
        self.__conversation_items: List[ConversationItem] = (
            self.__conversation_config.paths[conversation_path]
        )
        self.__current_item: ConversationItem = self.__conversation_items.pop(0)

    def __verify_information(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Verify if the last user message contains the required information and store the
        result in the 'information_verification_status' key of the provided data dictionary.

        Args:
            data (dict): Langchain conversation data.

        Returns:
            dict: The updated data dictionary with the 'information_verification_status' key added.
        """

        # If this method was triggered by a previous item, then the input can't be relevant for this extraction
        if data["is_recursive"]:
            data["information_verification_status"] = "NO"
            return data

        verification_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    # You can imply information (so if the user says 'I am Max', then you can imply that the name is 'Max'
                    # and don't need them to say 'My name is Max').
                    "system",
                    """Check if the last user message contains the required information.
                    If the information was provided, 
            output the single word 'YES'. If not, output the single word 'NO'. If the user appears to
            feel uncomfortable, output 'ABORT'. But don`t abort without reason. Don't ouput anything but
            YES, NO or ABORT. Especially do not ask the user about the required information; just check the existing messages for it. If the last message is empty or nonsense, output 'NO'""",
                ),
                ("system", "Required information: {current_information_description}"),
                ("user", "{input}"),
                MessagesPlaceholder(variable_name="chat_history"),
            ]
        )
        verifyer_chain = verification_prompt | self.__llm | StrOutputParser()
        information_verification_status = verifyer_chain.invoke(data).strip()
        data["information_verification_status"] = information_verification_status
        return data

    def __verify_choice(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Verify if the last user message contains a valid choice and store it in the
        'choice' key of the provided data dictionary.

        Args:
            data (dict): Langchain conversation data.

        Returns:
            dict: The updated data dictionary with the 'choice' key added.
        """

        # If this method was triggered by a previous item, then the input can't be relevant for this extraction
        if data["is_recursive"]:
            data["choice"] = "##NONE##"
            return data

        verification_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """The user was given a choice between multiple options. Check if the user message 
                    contains a selection of one of the possible choice options (it doesn't have to be
                    the exact wording if you get which choice they prefer). If so, output the selected
                    option (as it was given in possible choice options). If not, output '##NONE##'.
                    If the user appears to feel uncomfortable, output '##ABORT##'. Don't ouput anything 
                    but the choice or ##NONE## or ##ABORT##. 
                    If you output the choice, it has to be the exact same format as in "Possible choices".
                    If the user provides no message, output ##NONE##.
                    """,
                ),
                (
                    "system",
                    "Choice prompt: {current_choice}, Possible choice options: {current_choice_options}",
                    # "Possible choice options: {current_choice_options}",
                ),
                ("user", "{input}"),
                MessagesPlaceholder(variable_name="chat_history"),
            ]
        )
        verifyer_chain = verification_prompt | self.__llm | StrOutputParser()
        data["choice"] = verifyer_chain.invoke(data).strip()
        return data

    def __filter_information(self, data: Dict[str, Any]) -> Optional[str]:
        """
        Filter out a specific piece of information from the last user message, abiding to the given format.

        Args:
            data (dict): Langchain conversation data.

        Returns:
            str or None: The filtered information if successful, otherwise None.
        """

        filter_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """Your job is to filter out a certain piece of information from the user message. 
        You will be given the desciption of the information and the format in which the data should be returned.
        Just output the filtered data without any extra text. If the data is not contained in the message,
        output '##FAILED##'""",
                ),
                (
                    "system",
                    "Information description: {current_information_description}",
                ),
                ("system", "Information format: {current_information_format}"),
                ("user", "{input}"),
            ]
        )
        information_extractor = filter_prompt | self.__llm | StrOutputParser()
        filtered_information = information_extractor.invoke(data).strip()

        return filtered_information if filtered_information != "##FAILED##" else None

    def __make_information_extractor(self, data: Dict[str, Any]) -> Any:
        """
        Create an langchain subchain to retrieve specific information from the user, in a conversational manner.

        Args:
            data (dict): Langchain conversation data.

        Returns:
            object: A lngchain subchain for information extraction.
        """

        extraction_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """Extract different pieces of information from the user. Have a casual conversation tone but stay on topic.
                    If the user derivates from the topic of the information you want to have, gently guide 
                    them back to the topic. 
                    If the user answers gibberish or something unrelated, ask them to repeat IN A FULL SENTENCE.        
                    Be brief. Use the language in which the required information is given.
                    If you think the last AI message was off or doesn't fit the context, DO NOT comment on it or apologize.""",
                ),
                (
                    "system",
                    "Information you want to have: {current_information_description}",
                ),
                MessagesPlaceholder(variable_name="chat_history"),
            ]
        )
        information_extractor = extraction_prompt | self.__llm | StrOutputParser()
        return information_extractor

    def __make_choice_extractor(self, data: Dict[str, Any]) -> Any:
        """
        Create an langchain subchain to get a choice selection from the user, in a conversational manner.

        Args:
            data (dict): Langchain conversation data.

        Returns:
            object: A lngchain subchain for choice extraction.
        """
        # choices = ", ".join(data["current_choice_options"].keys())
        choices = ", ".join(data["current_choice_options"])
        extraction_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """Ask the user for a choice between multiple options. The type of choice is given by the choice prompt.
                    If the choices are yes or no, don't say so because thats obvious.
                    If the user derivates from the topic of the choice, gently guide 
                    them back to the topic. 
                    If the user answers gibberish or something unrelated, ask them to repeat IN A FULL SENTENCE.        
                    Be brief. Use the language in which the choice prompt is given.
                    If you think the last AI message was off or doesn't fit the context, DO NOT comment on it or apologize.""",
                ),
                (
                    "system",
                    f"Choice prompt: {data['current_choice']}, Possible choices: {choices}",
                ),
                MessagesPlaceholder(variable_name="chat_history"),
            ]
        )
        choice_extractor = extraction_prompt | self.__llm | StrOutputParser()
        return choice_extractor

    def __append_filtered_info(
        self, data: Dict[str, Any], information_item: InformationItem
    ) -> None:
        """
        Append filtered information thread-safely to the extracted information dictionary.

        Args:
            data (dict): Langchain conversation data.
            information_item (dict): The conversation item that required the information.

        Returns:
            None
        """
        self.__conversation_state_lock.acquire()
        filtered_info = self.__filter_information(data)
        if filtered_info:
            self.__conversation_state[information_item.title] = filtered_info
            self.__repeat_item = None
        else:
            # information couldn't be extracted, so repeat the item at next possibility
            self.__repeat_item = information_item

        self.__conversation_state_lock.release()

    def __information_extraction_successful(self, data: Dict[str, Any]) -> str:
        """
        Handle the successful extraction of information, proceed with the conversation or end it.

        Args:
            data (dict): Langchain conversation data.
        Returns:
            str: The result of processing the next conversation item or an empty string if the extraction is completed.
        """

        thread = threading.Thread(
            target=self.__append_filtered_info,
            args=(data, self.__current_item),
        )
        thread.start()

        if len(self.__conversation_items) > 0:
            self.__current_item = self.__conversation_items.pop(0)
        else:
            self.status = ExtractionStatus.COMPLETED
            return ""

        return self.__process_conversation_items(data["input"], is_recursive=True)

    def __choice_extraction_successful(self, data: Dict[str, Any]) -> str:
        """
        Handle the successful extraction of a choice and update the conversation flow accordingly.

        Args:
            data (dict): Langchain conversation data.

        Returns:
            str: The result of processing the next conversation item.
        """
        selected_choice = data["choice"]

        assert isinstance(self.__current_item, ChoiceItemBase)

        self.__conversation_items = self.__current_item.get_items_for_choice(
            selected_choice
        )
        self.__current_item = self.__conversation_items.pop(0)
        return self.__process_conversation_items(data["input"], is_recursive=True)

    def __extraction_aborted(self, data: Dict[str, Any]) -> Union[str, None]:
        """
        Handle the scenario where information extraction is aborted by loading the "aborted" conversation path.

        Args:
            data (dict): Langchain conversation data.

        Returns:
            str: The result of processing the next conversation item or an empty string if there are no more items.
        """

        self.status = ExtractionStatus.ABORTED

        self.__conversation_items = copy.deepcopy(
            self.__conversation_config.paths["aborted"]
        )
        if len(self.__conversation_items) > 0:
            self.__current_item = self.__conversation_items.pop(0)
        else:
            return ""

        if self.__realtime:
            data["input"] = ""

        return self.__process_conversation_items(
            data["input"], is_recursive=True, aborted=True
        )

    def __execute_prompt(self, prompt: str) -> str:
        """
        Execute a LLM chat prompt.

        Args:
            prompt (str): The prompt string to be executed.

        Returns:
            str: The result of the prompt execution.
        """
        prompt_template = ChatPromptTemplate.from_messages(
            [
                ("system", prompt),
                MessagesPlaceholder(variable_name="chat_history"),
            ]
        )
        prompt_chain = prompt_template | self.__llm | StrOutputParser()
        return prompt_chain.invoke({"chat_history": self.chat_history})

    def __check_item_repetition(self) -> None:
        """
        Check if an item needs to be repeated and insert it at the beginning of the conversation queue.

        Returns:
            None
        """
        if not self.__repeat_item:
            return

        if self.__realtime:
            self.__conversation_items.insert(0, self.__current_item)
            self.__conversation_items.insert(0, self.__repeat_item)
            self.__repeat_item = None
        else:
            # inform user that we need a piece of info again
            repeat_prompt_item = {
                "type": "prompt",
                "prompt": "Say (in the current language) that you need to ask again for an information. It doesnt matter if the info is already in the conversation.",
                "interactive": True,
            }
            self.__conversation_items.insert(0, self.__current_item)
            self.__conversation_items.insert(0, self.__repeat_item)
            self.__current_item = repeat_prompt_item
            self.__repeat_item = None

    def __get_conversation_state_lock(self) -> bool:
        """
        Acquire and release the conversation state lock.

        Returns:
            bool: True if state dict is valid, i.e. no repitition needs to be performed, False otherwise.
        """
        self.__conversation_state_lock.acquire()
        self.__conversation_state_lock.release()
        return not self.__repeat_item

    def __check_dialled_choice(self, dialled_number: str) -> None:
        """
        Check if the dialled number matches any of the options in the current choice item and update the dialled choice accordingly.

        Args:
            dialled_number (str): The number dialled by the user.

        Returns:
            None
        """

        if not isinstance(self.__current_item, ChoiceItemBase):
            return

        for option in self.__current_item.options:
            if str(option.dial_number) == str(dialled_number):
                self.__dialled_choice = option.option
                self.__current_item_in_progress = False
                return

    def __wait_for_model_callback(
        self,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[List[Any]]]:
        """
        Wait for a functions callback from the realtime API.

        Returns:
            tuple: A tuple containing the callback arguments (dict) and the list of chat messages (list).
        """
        self.__current_item_messages = []
        self.__current_item_callback_args = None
        self.__current_item_in_progress = True
        self.__dialled_choice = None

        # wait until callback by model
        while self.__current_item_in_progress and self.__softphone.has_picked_up_call():
            time.sleep(0.2)

        if not self.__softphone.has_picked_up_call():
            self.status = ExtractionStatus.ABORTED
            return None, None

        # model has responded, we stop listening for user input
        event = {
            "type": "session.update",
            "session": {
                "turn_detection": None,
                "tools": [],
            },
        }
        self.__realtime_connection.send(json.dumps(event))
        self.__accepts_user_input = False

        chat_messages = self.__current_item_messages

        if self.__dialled_choice:
            args = {"choice": self.__dialled_choice}
        else:
            args = self.__current_item_callback_args

        return args, chat_messages

    def __process_read_item(
        self, item: ReadItem
    ) -> Tuple[List[Tuple[str, str]], List[Any], bool]:
        """
        Process a conversation item of type read and generate responses.

        Returns:
            tuple: A tuple containing the responses (list), chat messages (list), and a boolean indicating if interaction is required (bool).
        """
        response_text = item.text + "\n"
        responses = [(response_text, "read")]
        chat_messages = [AIMessage(content=response_text)]
        requires_interaction = self.__realtime

        return responses, chat_messages, requires_interaction

    def __process_prompt_item(
        self, item: PromptItem
    ) -> Tuple[List[Tuple[str, str]], List[Any], bool]:
        """
        Process a conversation item of type prompt and generate responses.

        Returns:
            tuple: A tuple containing the responses (list), chat messages (list), and a boolean indicating if interaction is required (bool).
        """
        if self.__realtime:
            # REALTIME CASE
            # self.__accepts_user_input = True

            # instruct the model to execute prompt
            conversation_item_event = {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"Execute the given prompt: {item.prompt}",
                        }
                    ],
                },
            }
            self.__realtime_connection.send(json.dumps(conversation_item_event))

            self.__current_item_messages = []
            self.__response_done = False

            # initiate a model response
            response_event = {
                "type": "response.create",
                "response": {
                    "modalities": ["text", "audio"],
                },
            }
            self.__realtime_connection.send(json.dumps(response_event))

            self.__softphone.prioritize_external_audio()
            # wait for audio packages to arrive
            while not self.__response_done:
                time.sleep(0.2)

            responses = [("Realtime Conversation", "prompt")]
            chat_messages = self.__current_item_messages
        else:
            # NON-REALTIME CASE
            response_text = self.__execute_prompt(item.prompt) + "\n"
            responses = [(response_text, "prompt")]
            chat_messages = [AIMessage(content=response_text)]

        return responses, chat_messages, False

    def __process_path_item(
        self, item: PathItem
    ) -> Tuple[List[Tuple[str, str]], List[Any], bool]:
        """
        Process a conversation item of type path and generate responses.

        Returns:
            tuple: A tuple containing the responses (list), chat messages (list), and a boolean indicating if interaction is required (bool).
        """
        self.__conversation_items = copy.deepcopy(
            self.__conversation_config.paths[item.path]
        )

        return [], [], False

    def __process_information_item(
        self, item: InformationItem, user_input: str, is_recursive: bool
    ) -> Tuple[List[Tuple[str, str]], List[Any], bool]:
        """
        Process a conversation item of type information and generate responses.

        Returns:
            tuple: A tuple containing the responses (list), chat messages (list), and a boolean indicating if interaction is required (bool).
        """
        if self.__realtime:
            self.__accepts_user_input = True
            # define callback function
            session_update_event = {
                "type": "session.update",
                "session": {
                    "tools": [
                        {
                            "type": "function",
                            "name": "information_callback",
                            "description": f"Call this function once you have sucessfully extracted the information from the user. Provide as a parameter the extracted information. The parameter should be in the following format: {item.format}. If the user seems doesn't want to answer or wants to quit the conversation, output ##ABORT## as parameter.",
                            "parameters": {
                                "type": "object",
                                "properties": {"information": {"type": "string"}},
                                "required": ["information"],
                            },
                        }
                    ],
                    "turn_detection": vad_config
                },
            }
            self.__realtime_connection.send(json.dumps(session_update_event))

            # instruct the model to extract information
            conversation_item_event = {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"Extract a piece of information from the user. If the user derivates from the topic, lead them gently back to it. Once you have the information, don't give any response. Required information: {item.description}",
                        }
                    ],
                },
            }
            self.__realtime_connection.send(json.dumps(conversation_item_event))

            # initiate a model response
            response_event = {
                "type": "response.create",
                "response": {
                    "modalities": ["text", "audio"],
                },
            }
            self.__realtime_connection.send(json.dumps(response_event))

            callback_args, chat_messages = self.__wait_for_model_callback()

            # aborted while waiting
            if callback_args is None:
                return [], [], True

            # extract information from callback
            # TODO: we assume model ouputs always right args. Maybe introduce a null check and handle by repeating, as in the non-realtime case
            information = callback_args["information"]

            if information == "##ABORT##":
                self.__extraction_aborted({})
                return [], [], False

            self.__conversation_state_lock.acquire()
            self.__conversation_state[item.title] = information
            self.__repeat_item = None
            self.__conversation_state_lock.release()

            responses = [("Realtime Conversation", "information")]
            requires_interaction = False
        else:
            # NON-REALTIME CASE
            response = self.information_extraction_chain.invoke(
                {
                    "input": user_input,
                    "chat_history": self.chat_history,
                    "current_information_description": item.description,
                    "current_information_format": item.format,
                    "is_recursive": is_recursive,
                }
            )
            if isinstance(response, list):
                responses = response
                chat_messages = []
            else:
                responses = [(response, "information")]
                chat_messages = [AIMessage(content=response)]
            requires_interaction = True

        return responses, chat_messages, requires_interaction

    def __process_choice_item(
        self, item: ChoiceItem, user_input: str, is_recursive: bool
    ) -> Tuple[List[Tuple[str, str]], List[Any], bool]:
        """
        Process a conversation item of type choice and generate responses.

        Returns:
            tuple: A tuple containing the responses (list), chat messages (list), and a boolean indicating if interaction is required (bool).
        """
        if self.__realtime:
            # define callback function
            self.__accepts_user_input = True
            session_update_event = {
                "type": "session.update",
                "session": {
                    "tools": [
                        {
                            "type": "function",
                            "name": "choice_callback",
                            "description": f"Call this function once you have sucessfully extracted the selected choice, which should be only one of the following: {item.get_all_options()}. If the user seems doesn't want to answer or wants to quit the conversation, output ##ABORT## as parameter.",
                            "parameters": {
                                "type": "object",
                                "properties": {"choice": {"type": "string"}},
                                "required": ["choice"],
                            },
                        }
                    ],
                    "turn_detection": vad_config
                },
            }
            self.__realtime_connection.send(json.dumps(session_update_event))

            # instruct the model to extract choice
            is_silent = item.silent
            silent_prompt_text = (
                "At the start of the conversation say nothing and wait for the user to say something."
                if is_silent
                else ""
            )
            conversation_item_event = {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"Present the choice to the user and ask them to select one of the given options. If the user derivates from the topic, lead them gently back to it. Once you have the selected option, don't give any response. {silent_prompt_text} Choice: {item.choice}, Possible options: {item.get_all_options()}",
                        }
                    ],
                },
            }
            self.__realtime_connection.send(json.dumps(conversation_item_event))

            # initiate a model response
            if not is_silent:
                response_event = {
                    "type": "response.create",
                    "response": {
                        "modalities": ["text", "audio"],
                    },
                }
                self.__realtime_connection.send(json.dumps(response_event))

            callback_args, chat_messages = self.__wait_for_model_callback()

            # aborted while waiting
            if callback_args is None:
                return [], [], True

            # extract choice and update conversation path accordingly
            # TODO: we assume model ouputs always right args. Maybe introduce a null check and handle by repeating, as in the non-realtime case
            selected_choice = callback_args["choice"]

            if selected_choice == "##ABORT##":
                self.__extraction_aborted({})
                return [], [], False

            assert isinstance(self.__current_item, ChoiceItemBase)

            new_items = self.__current_item.get_items_for_choice(
                selected_choice
            )
            if not new_items:
                self.__conversation_items = [self.__current_item]
                return [], [], False
            self.__conversation_items = new_items

            responses = [("Realtime Conversation", "choice")]
            requires_interaction = False
        else:
            # NON-REALTIME CASE
            if item.silent and not item.first_run_done:
                item.first_run_done = True
                return [], [], True

            response = self.choice_extraction_chain.invoke(
                {
                    "input": user_input,
                    "chat_history": self.chat_history,
                    "current_choice": item.choice,
                    "current_choice_options": list(item.get_all_options()),
                    "is_recursive": is_recursive,
                }
            )
            if isinstance(response, list):
                responses = response
                chat_messages = []
            else:
                responses = [(response, "choice")]
                chat_messages = [AIMessage(content=response)]
            requires_interaction = True

        return responses, chat_messages, requires_interaction

    def __process_function_item(
        self, item: FunctionItem
    ) -> Tuple[List[Tuple[str, str]], List[Any], bool]:
        """
        Process a conversation item of type function and generate responses.

        Returns:
            tuple: A tuple containing the responses (list), chat messages (list), and a boolean indicating if interaction is required (bool).
        """
        information_is_valid = self.__get_conversation_state_lock()
        if not information_is_valid:
            return [], [], False

        function = item.function
        response_text = function(self.__conversation_state, self.__softphone)
        if response_text:
            responses = [(response_text, "function")]
            chat_messages = [AIMessage(content=response_text)]
        else:
            responses = [("", "function")]
            chat_messages = []

        requires_interaction = self.__realtime

        return responses, chat_messages, requires_interaction

    def __process_function_choice_item(
        self, item: FunctionChoiceItem
    ) -> Tuple[List[Tuple[str, str]], List[Any], bool]:
        """
        Process a conversation item of type function choice and generate responses.

        Returns:
            tuple: A tuple containing the responses (list), chat messages (list), and a boolean indicating if interaction is required (bool).
        """
        information_is_valid = self.__get_conversation_state_lock()
        if not information_is_valid:
            return [], [], False

        function = item.function
        choice = function(self.__conversation_state, self.__softphone)

        self.__conversation_items = item.get_items_for_choice(choice)

        return [], [], False

    def __process_conversation_items(
        self, user_input: str, is_recursive: bool = False, aborted: bool = False
    ) -> List[Tuple[str, str]]:
        """
        Process items of the current conversation sequentially based on their type and update the conversation flow.

        Args:
            user_input (str): The input provided by the user.
            is_recursive (bool, optional): Whether method was called as part of a previous call. Defaults to False.
            aborted (bool, optional): Whether the conversation was aborted. Defaults to False.

        Returns:
            list: A list of collected responses from processing the conversation items. Each response is a tuple (message, type), where 'message' is the actual response and 'type' is the type of the conversation item that produced this response.
        """
        if not is_recursive and not self.__realtime:
            self.chat_history.append(HumanMessage(content=user_input))

        collected_responses = []

        # sequentially process conversation items
        while True:
            # check if conversation item needs to be repeated
            self.__check_item_repetition()

            if isinstance(self.__current_item, ReadItem):
                responses, chat_messages, requires_interaction = (
                    self.__process_read_item(self.__current_item)
                )
            elif isinstance(self.__current_item, PromptItem):
                responses, chat_messages, requires_interaction = (
                    self.__process_prompt_item(self.__current_item)
                )
            elif isinstance(self.__current_item, PathItem):
                responses, chat_messages, requires_interaction = (
                    self.__process_path_item(self.__current_item)
                )
            elif isinstance(self.__current_item, InformationItem):
                responses, chat_messages, requires_interaction = (
                    self.__process_information_item(
                        self.__current_item, user_input, is_recursive
                    )
                )
            elif isinstance(self.__current_item, ChoiceItem):
                responses, chat_messages, requires_interaction = (
                    self.__process_choice_item(
                        self.__current_item, user_input, is_recursive
                    )
                )
            elif isinstance(self.__current_item, FunctionItem):
                responses, chat_messages, requires_interaction = (
                    self.__process_function_item(self.__current_item)
                )
            elif isinstance(self.__current_item, FunctionChoiceItem):
                responses, chat_messages, requires_interaction = (
                    self.__process_function_choice_item(self.__current_item)
                )

            collected_responses.extend(responses)
            self.chat_history.extend(chat_messages)

            if requires_interaction and not self.__realtime:
                break

            if len(self.__conversation_items) > 0:
                # for interactive items, breakt the loop to get user input. Last item can`t be interactive.
                if (
                    requires_interaction and self.__realtime
                ) or self.__current_item.interactive:
                    self.__current_item = self.__conversation_items.pop(0)
                    break
                else:
                    self.__current_item = self.__conversation_items.pop(0)
            else:
                if not aborted:
                    self.status = ExtractionStatus.COMPLETED
                return collected_responses

        return collected_responses

    def run_extraction_step(self, user_input: str) -> List[Tuple[str, str]]:
        """
        Run a single step of the information extraction process.

        Args:
            user_input (str): The input provided by the user.

        Returns:
            str: The generated response.
        """
        return self.__process_conversation_items(
            user_input, is_recursive=False, aborted=False
        )

    def get_conversation_state(self) -> Dict[str, Any]:
        """
        Thread-safely etrieve the information extracted during the conversation so far.

        Returns:
            dict: The dictionary containing the extracted information.
        """
        self.__conversation_state_lock.acquire()
        self.__conversation_state_lock.release()
        return self.__conversation_state

    def get_status(self) -> Any:
        return self.status


class ExtractionStatus(Enum):
    IN_PROGRESS = 0
    COMPLETED = 1
    ABORTED = 2
