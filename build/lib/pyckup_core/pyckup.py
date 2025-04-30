import os
from pathlib import Path
import traceback
import yaml
from pyckup_core.conversation_config import ConversationConfig
from pyckup_core.llm_extractor import LLMExtractor, ExtractionStatus
from pyckup_core.call_logging import log_message, setup_log
from pyckup_core.softphone import Softphone, SoftphoneGroup
import sqlite3
import threading
import time
from typing import Optional, Tuple, Dict, List

HERE = Path(os.path.abspath(__file__)).parent


class Pyckup:

    db = None

    def __init__(
        self,
        sip_credentials_path: str,
        db_path: Optional[str] = None,
        log_dir: Optional[str] = None,
        realtime: bool = True,
    ) -> None:
        """
        Create an instance of the Pyckup class.

        Args:
            sip_credentials_path (str): The file path to the SIP credentials.
            db_path (str, optional): The file path to the database (will be created if it doesn't exist). If None, database functions can't be used. Defaults to None.
            log_dir (str, optional): The directory where logs will be saved. If None, logging is disabled. Defaults to None.
            realtime (bool, optional): Whether to use the OpenAI realtime API. Defaults to True.
        """
        self.__sip_credentials_path = sip_credentials_path
        self.__setup_db(db_path)
        self.__log_dir = log_dir
        self.__realtime = realtime

    def __del__(self) -> None:
        if self.db is not None:
            self.db.close()

    def setup_conversation(self, conversation_config: ConversationConfig) -> str:
        """
        Get conversation config and title and, if database functionality is used, ensure tables exist.

        Args:
            config_path (str): The file path to the outgoing conversation configuration YAML file.

        Returns:
            Tuple[Dict, str]: A tuple containing the conversation configuration dictionary and the conversation title string.
        """

        conversation_title = conversation_config.title.lower().replace(" ", "_")

        if self.db is not None:
            # ensure that results table exists
            fields = ""
            for path in conversation_config.paths.values():
                for item in path:
                    if "title" in item:
                        fields += ",\n"
                        fields += f"{item['title'].lower().replace(' ', '_')} TEXT"

            cursor = self.db.cursor()
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {conversation_title} (
                    result_id INTEGER PRIMARY KEY, 
                    contact_id INTEGER UNIQUE{fields}
                )"""
            )

            # ensure that status table exists
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {conversation_title}_status (
                    status_id INTEGER PRIMARY KEY, 
                    contact_id INTEGER UNIQUE,
                    num_attempts INTEGER,
                    status TEXT
                )"""
            )

            self.db.commit()

        return conversation_title

    def __setup_db(self, db_path: Optional[str]) -> None:
        """
        Ensure that the database and the contacts table exists.

        Args:
            db_path (str): The file path to the (to be created) SQLite database. If None, no database will be created.

        Returns:
            None
        """

        if db_path is None:
            self.db = None
            return

        self.db = sqlite3.connect(db_path)
        cursor = self.db.cursor()

        # Ensure contacts table exists
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS contacts (
                contact_id INTEGER PRIMARY KEY, 
                name TEXT,
                phone_number TEXT,
                CONSTRAINT unq UNIQUE (name, phone_number)
            )"""
        )
        self.db.commit()

    def add_contact(self, name: str, phone_number: str) -> None:
        """
        Add a new contact to the database.

        Args:
            name (str): The name of the contact.
            phone_number (str): The phone number of the contact.

        Returns:
            None
        """
        if self.db is None:
            print("Cannot add contact: no database provided")
            return

        cursor = self.db.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO contacts (name, phone_number) VALUES (?, ?)
        """,
            (name, phone_number),
        )

        self.db.commit()

    def get_contact(self, contact_id: int) -> Optional[Dict]:
        """
        Retrieve contact information from the database by contact ID.

        Args:
            contact_id (int): The ID of the contact to retrieve.

        Returns:
            Optional[Dict]: A dictionary with the contact's information if found, otherwise None.
        """
        if self.db is None:
            print("Cannot get contact: no database provided")
            return None

        cursor = self.db.cursor()
        cursor.execute(
            """
            SELECT * FROM contacts WHERE contact_id = ?
        """,
            (contact_id,),
        )

        contact_data = cursor.fetchone()

        if not contact_data:
            return None

        return {
            "name": contact_data[1],
            "phone_number": contact_data[2],
        }

    def get_contact_status(
        self, contact_id: int, conversation_config: ConversationConfig
    ) -> Optional[Dict]:
        """
        Retrieve the status of a contact for the previous calls using this conversation.

        Args:
            contact_id (int): The ID of the contact whose status is to be retrieved.
            conversation_config_path (str): The path to the config file for which the status should be retrieved.

        Returns:
            Optional[Dict]: A dictionary with the contact's status information if found, otherwise None.
        """
        if self.db is None:
            print("Cannot get contact status: no database provided")
            return None

        conversation_title = self.setup_conversation(conversation_config)

        cursor = self.db.cursor()
        cursor.execute(
            f"""
            SELECT * FROM {conversation_title}_status WHERE contact_id = ?
        """,
            (contact_id,),
        )

        status_data = cursor.fetchone()

        if not status_data:
            return None

        return {"num_attempts": status_data[2], "status": status_data[3]}

    def __call_core_routine(
        self,
        softphone: Softphone,
        conversation_config: ConversationConfig,
        is_outgoing: bool,
        enable_logging: bool = True,
        contact_id: Optional[int] = None,
        conversation_title: Optional[str] = None,
    ) -> None:
        """
        Core routine for handling a call.

        Args:
            softphone (Softphone): The softphone instance used for the call.
            conversation_config (dict): The conversation configuration that is used.
            is_outgoing (bool): Flag indicating if the call is outgoing.
            enable_logging (bool, optional): Flag to enable or disable logging. Defaults to True.
            contact_id (str, optional): The contact ID for the call. Only set if call is performed by call_contact(s). Defaults to None.
            conversation_title (str, optional): The title of the conversation. Only set if call is performed by call_contact(s). Defaults to None.

        Returns:
            None
        """
        do_db_updates = is_outgoing and contact_id is not None and self.db is not None
        enable_logging = self.__log_dir is not None and enable_logging
        phone_number = softphone.get_called_phone_number()

        if enable_logging:
            log_path = setup_log(self.__log_dir, phone_number)

        if self.__realtime:
            # REALTIME CASE
            num_logged_messages = 0
            incoming_buffer, outgoing_buffer = softphone.handle_external_buffers()
            extractor = LLMExtractor(
                conversation_config,
                softphone=softphone,
                realtime=True,
                incoming_buffer=incoming_buffer,
                outgoing_buffer=outgoing_buffer,
            )

            while (
                extractor.get_status() == ExtractionStatus.IN_PROGRESS
                and softphone.has_picked_up_call()
            ):
                extractor_responses = extractor.run_extraction_step("")

                # log new messages
                if enable_logging:
                    chat_history = extractor.chat_history
                    for message in chat_history[num_logged_messages:]:
                        if message.type == "ai":
                            log_message(log_path, message.content, role="Pyckup")
                        elif message.type == "human":
                            log_message(log_path, message.content, role="User")
                    num_logged_messages += len(chat_history[num_logged_messages:])

                # read out responses
                for response in extractor_responses:
                    if response[1] == "read" or response[1] == "function":
                        softphone.say(response[0], cache_audio=True)
            softphone.wait_for_external_output_finish()

        else:
            # NOT-REALTIME CASE
            extractor = LLMExtractor(
                conversation_config, softphone=softphone, realtime=False
            )
            extractor_responses = extractor.run_extraction_step("")
            if enable_logging:
                for response in extractor_responses:
                    log_message(log_path, response[0], role="Pyckup")
            for response in extractor_responses:
                cache_audio = True if response[1] == "read" else False
                softphone.say(response[0], cache_audio=cache_audio)

            while (
                extractor.get_status() == ExtractionStatus.IN_PROGRESS
                and softphone.has_picked_up_call()
            ):
                time.sleep(0.5)
                user_input = softphone.listen()

                # user input is ##INTERRUPTED## if listening couldn`t be performed. Could be due to call interruption or holding the call for too long.
                if user_input == "##INTERRUPTED##":
                    softphone.hangup()
                    print("Call interrupted during listening.")

                softphone.play_audio(str(HERE / "resources/processing.wav"))
                if enable_logging:
                    log_message(log_path, user_input, role="User")
                extractor_responses = extractor.run_extraction_step(user_input)
                if enable_logging:
                    for response in extractor_responses:
                        log_message(log_path, response[0], role="Pyckup")
                for response in extractor_responses:
                    cache_audio = True if response[1] == "read" else False
                    softphone.say(response[0], cache_audio=cache_audio)

            if extractor.get_status() != ExtractionStatus.COMPLETED:
                print("Extraction aborted")

                if do_db_updates:
                    cursor = self.db.cursor()
                    cursor.execute(
                        f"""
                    UPDATE {conversation_title}_status SET status = "ABORTED" WHERE contact_id = ?
                    """,
                        (contact_id,),
                    )
                    self.db.commit()
            else:
                # successful extraction, save results in db
                print("Extraction completed")

                if do_db_updates:
                    cursor = self.db.cursor()
                    cursor.execute(
                        f"""
                    UPDATE {conversation_title}_status SET status = "COMPLETED" WHERE contact_id = ?
                    """,
                        (contact_id,),
                    )

                    information = extractor.get_conversation_state()
                    cursor.execute(
                        f"""
                        INSERT OR REPLACE INTO {conversation_title} (
                            contact_id, {', '.join([key.lower() for key in information.keys()])}
                            )
                        VALUES (
                            ?, {', '.join(['?']*len(information))}
                            )
                    """,
                        [contact_id] + list(information.values()),
                    )
                    self.db.commit()

        # usually we would hang up here, but if the call is forwarded then should keep the connection open
        while softphone.is_forwarded():
            time.sleep(1)
        softphone.hangup()
        print("Call ended.")

    def __perform_outgoing_call(
        self,
        conversation_config: ConversationConfig,
        phone_number: Optional[str] = None,
        contact_id: Optional[int] = None,
        enable_logging: bool = True,
    ) -> None:
        """
        Perform an outgoing call to a specified phone number or contact ID. Wrapped by call_number and call_contact.

        Args:
            conversation_config_path (str): The file path to the conversation configuration.
            phone_number (str, optional): The phone number to call. Defaults to None. Either this or contact_id must be provided.
            contact_id (int, optional): The contact ID to call. Defaults to None. Either this or phone_number must be provided.
            enable_logging (bool, optional): Whether to save a log of the conversation. Only works if log_dir has been set. Defaults to True.

        Returns:
            None
        """
        if phone_number is None and contact_id is None:
            print(
                "Couldn't make call: you either have to provide a phone number or a contact id."
            )
            return

        conversation_title = self.setup_conversation(conversation_config)

        if contact_id and self.db is not None:
            contact = self.get_contact(contact_id)
            if not contact:
                print("Couldn't make call: invalid contact id.")
                return

            phone_number = contact["phone_number"]

            # ensure that contact has status entry
            cursor = self.db.cursor()
            cursor.execute(
                f"""
                INSERT OR IGNORE INTO {conversation_title}_status (contact_id, num_attempts, status) VALUES (?, 0, "NOT_REACHED")
                """,
                (contact_id,),
            )

            # increment number of attempts
            cursor.execute(
                f"""
                UPDATE {conversation_title}_status SET num_attempts = num_attempts + 1 WHERE contact_id = ?
                """,
                (contact_id,),
            )

            self.db.commit()

        sf = Softphone(self.__sip_credentials_path)
        print("Calling " + phone_number + "...")
        sf.call(phone_number)
        sf.wait_for_stop_calling()

        if not sf.has_picked_up_call():
            print("Call not picked up.")
            return

        print("Call picked up. Setting up extractor.")

        self.__call_core_routine(
            sf,
            conversation_config,
            is_outgoing=True,
            enable_logging=enable_logging,
            contact_id=contact_id,
            conversation_title=conversation_title,
        )

    def call_number(
        self,
        phone_number: str,
        conversation_config: ConversationConfig,
        enable_logging: bool = True,
    ) -> None:
        """
        Initiate a call to a phone number and lead recipient through the specifiedconversation.
        Update contact status while doing so.

        Args:
            phone_number (int): The phone number to call in E.164 format.
            conversation_config_path (str): The file path to the conversation configuration.
            enable_logging (bool, optional): Whether to save a log of the conversation. Defaults to True.

        Returns:
            None
        """
        self.__perform_outgoing_call(
            conversation_config,
            phone_number=phone_number,
            enable_logging=enable_logging,
        )

    def call_contact(
        self,
        contact_id: int,
        conversation_config: ConversationConfig,
        enable_logging: bool = True,
    ) -> None:
        """
        Initiate a call to a contact and lead them through the specified conversation.
        Update contact status while doing so.

        Args:
            contact_id (int): The ID of the contact to call.
            conversation_config_path (str): The file path to the conversation configuration.
            enable_logging (bool, optional): Whether to save a log of the conversation. Defaults to True.

        Returns:
            None
        """
        if self.db is None:
            print("Cannot call contact: no database provided")
            return

        self.__perform_outgoing_call(
            conversation_config,
            contact_id=contact_id,
            enable_logging=enable_logging,
        )

    def call_numbers(
        self,
        phone_numbers: List[str],
        conversation_config: ConversationConfig,
        enable_logging: bool = True,
    ) -> None:
        """
        Call a list of phone numbers and lead them through the specified conversation.

        Args:
            phone_numbers (list of str): A list of phone numbers to call in E.164 format.
            conversation_config_path (str): The file path to the conversation configuration.
            enable_logging (bool, optional): Whether to save a log of the conversation. Defaults to True.

        Returns:
            None
        """
        for phone_number in phone_numbers:
            self.call_number(
                phone_number, conversation_config, enable_logging=enable_logging
            )

    def call_contacts(
        self,
        conversation_config: ConversationConfig,
        contact_ids: Optional[List[int]] = None,
        maximum_attempts: Optional[int] = None,
    ) -> None:
        """
        Call a list of contacts according to their statuses from previous call attempts.

        Args:
            conversation_config_path (str): The file path to the conversation configuration.
            contact_ids (list of int, optional): A list of contact IDs to call. If None, all contacts will be called. Defaults to None.
            maximum_attempts (int, optional): The maximum number of call attempts for each contact. If None, there is no limit. Defaults to None.

        Returns:
            None
        """
        if self.db is None:
            print("Cannot call contacts: no database provided")
            return

        if contact_ids is None:
            cursor = self.db.cursor()
            cursor.execute(
                f"""
                SELECT contact_id FROM contacts
            """
            )
            contact_ids = [contact[0] for contact in cursor.fetchall()]
            contact_ids.sort()

        if maximum_attempts is None:
            maximum_attempts = float("inf")

        for contact_id in contact_ids:
            print(f"Attempting to call contact {contact_id}")

            if self.get_contact(contact_id) is None:
                print(f"Invalid contact id: {contact_id}")
                continue

            status = self.get_contact_status(contact_id, conversation_config)

            if status is None:
                # this is the first call, always should be made
                self.call_contact(contact_id, conversation_config)
                continue

            if status["status"] != "NOT_REACHED":
                print(f"Contact {contact_id} has already been reached.")
                continue

            if status["num_attempts"] >= maximum_attempts:
                print(f"Contact {contact_id} has reached maximum number of attempts.")
                continue

            self.call_contact(contact_id, conversation_config)

    def __softphone_listen(
        self,
        sf: Softphone,
        sf_group: SoftphoneGroup,
        incoming_conversation_config: ConversationConfig,
        enable_logging: bool = True,
    ) -> None:
        """
        Thread used for listening for incoming calls. A thread is created for each softphone of the group.
        After connection is made, call is handled according to the incoming conversation configuration.

        Args:
            sf (Softphone): The softphone instance to use for the call.
            sf_group (SoftphoneGroup): The group of softphones to which the current softphone belongs.
            incoming_conversation_config (str): The configuration for the incoming conversation.
            enable_logging (bool, optional): Whether to save a log of the conversation. Only works if log_dir has been set. Defaults to True.

        Returns:
            None
        """

        # register thread
        sf_group.pjsua_endpoint.libRegisterThread(f"softphone_listen")

        try:
            print("Listening...")

            while not sf.has_picked_up_call():
                if not sf_group.is_listening:
                    return
                time.sleep(1)
                pass

            print(f"Incoming call on softphone {sf.get_id()}. Setting up extractor.")

            self.__call_core_routine(
                sf,
                incoming_conversation_config,
                is_outgoing=False,
                enable_logging=enable_logging,
            )
        except Exception as e:
            print("Exception in listening thread:", e)
            traceback.print_exc()
            sf.hangup()

        # restart thread after it was terminated (successful or not)
        listen_thread = threading.Thread(
            target=self.__softphone_listen,
            args=(sf, sf_group, incoming_conversation_config, enable_logging),
        )
        listen_thread.start()

    def start_listening(
        self,
        conversation_config: ConversationConfig,
        num_devices: int = 1,
        enable_logging: bool = True,
    ) -> SoftphoneGroup:
        """
        Start listening for incoming calls.

        Args:
            conversation_path (str): The file path to the conversation configuration YAML file.
            num_devices (int, optional): The number of softphone devices (= number of concurrent calls) to initialize. Defaults to 1.
            enable_logging (bool, optional): Whether to save a log of the conversation. Only works if log_dir has been set. Defaults to True.

        Returns:
            SoftphoneGroup: The group of softphones that are listening for incoming calls.
        """

        sf_group = SoftphoneGroup(self.__sip_credentials_path)
        for i in range(int(num_devices)):
            sf = Softphone(self.__sip_credentials_path, sf_group)
            listen_thread = threading.Thread(
                target=self.__softphone_listen,
                args=(sf, sf_group, conversation_config, enable_logging),
            )
            listen_thread.start()
        return sf_group

    def stop_listening(self, sf_group: SoftphoneGroup) -> None:
        """
        Stop listening for incoming calls on the specified softphone group.

        Args:
            sf_group (SoftphoneGroup): The group of softphones that should stop listening.

        Returns:
            None
        """
        sf_group.is_listening = False
