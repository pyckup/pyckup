
import os
from pathlib import Path
import traceback
import yaml
from calle_core.llm_extractor import llm_extractor, ExtractionStatus
from calle_core.softphone import softphone, softphone_group
import sqlite3
import threading
import time

HERE = Path(os.path.abspath(__file__)).parent



class call_e:
    
    outgoing_conversation_config = None
    outgoing_conversation_title = None
    db = None
    
    def __init__(self, sip_credentials_path, outgoing_conversation_config_path, db_path):
        self.__sip_credentials_path = sip_credentials_path
        self.__setup_db(db_path)
        self.update_outgoing_conversation_config(outgoing_conversation_config_path)
        
    def __del__(self):
        if self.db is not None:
            self.db.close()
        
    def __read_conversation_config(self, config_path):
        with open(config_path, 'r') as config_file:
            return yaml.safe_load(config_file)
    
    def update_outgoing_conversation_config(self, config_path):
        self.outgoing_conversation_config = self.__read_conversation_config(config_path)
        
        # ensure that results table exists
        self.outgoing_conversation_title = self.outgoing_conversation_config['conversation_title'].lower().replace(" ", "_")
        fields = ""
        for path in self.outgoing_conversation_config['conversation_paths'].values():
            for item in path:
                if 'title' in item:
                    fields += ",\n"
                    fields +=  f"{item['title'].lower().replace(' ', '_')} TEXT"
            
        cursor = self.db.cursor()
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.outgoing_conversation_title} (
                result_id INTEGER PRIMARY KEY, 
                contact_id INTEGER UNIQUE{fields}
            )"""
        )	
        
        # ensure that status table exists
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.outgoing_conversation_title}_status (
                status_id INTEGER PRIMARY KEY, 
                contact_id INTEGER UNIQUE,
                num_attempts INTEGER,
                status TEXT
            )"""
        )	
        
        
        self.db.commit()
    
    def __setup_db(self, db_path):
        self.db = sqlite3.connect(db_path)
        cursor = self.db.cursor()
        
        # Ensure contacts table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                contact_id INTEGER PRIMARY KEY, 
                name TEXT,
                phone_number TEXT,
                address TEXT,
                CONSTRAINT unq UNIQUE (name, phone_number)
            )"""
        )
        self.db.commit()
    
    def add_contact(self, name, phone_number, address):
        cursor = self.db.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO contacts (name, phone_number, address) VALUES (?, ?, ?)
        """, (name, phone_number, address))
        
        self.db.commit()
    
    def get_contact(self, contact_id):
        cursor = self.db.cursor()
        cursor.execute("""
            SELECT * FROM contacts WHERE contact_id = ?
        """, (contact_id,))
        
        contact_data = cursor.fetchone()
        
        if not contact_data:
            return None
        
        return {
            "name": contact_data[1],
            "phone_number": contact_data[2],
            "address": contact_data[3]
        }
    
    def get_contact_status(self, contact_id):
        cursor = self.db.cursor()
        cursor.execute(f"""
            SELECT * FROM {self.outgoing_conversation_title}_status WHERE contact_id = ?
        """, (contact_id,))
        
        status_data = cursor.fetchone()
        
        if not status_data:
            return None
        
        return {
            "num_attempts": status_data[2],
            "status": status_data[3]
        }
        
    def call_contact(self, contact_id, enable_logging = True):
        conversation_log = ""
        contact = self.get_contact(contact_id)
        
        if not contact:
            print("Couldn't make call: invalid contact id.")
            return
        
        # ensure that contact has status entry
        cursor = self.db.cursor()
        cursor.execute(f"""
            INSERT OR IGNORE INTO {self.outgoing_conversation_title}_status (contact_id, num_attempts, status) VALUES (?, 0, "NOT_REACHED")
            """, (contact_id,))
        
        # increment number of attempts
        cursor.execute(f"""
            UPDATE {self.outgoing_conversation_title}_status SET num_attempts = num_attempts + 1 WHERE contact_id = ?
            """, (contact_id,))
        
        self.db.commit()
        
        sf = softphone(self.__sip_credentials_path)
        print("Calling " + contact['phone_number'] + "...")
        sf.call(contact['phone_number'])
        sf.wait_for_stop_calling()
        
        if not sf.has_picked_up_call():
            print("Call not picked up.")
            return
        
        print("Call picked up. Setting up extractor.")
    
        extractor = llm_extractor(self.outgoing_conversation_config, softphone=sf)
        extractor_response = extractor.run_extraction_step("")
        conversation_log += "Call-E: " + extractor_response + "\n"
        sf.say(extractor_response)
        
        while(extractor.get_status() == ExtractionStatus.IN_PROGRESS and sf.has_picked_up_call()):
            user_input = sf.listen()
            sf.play_audio(str(HERE / "../resources/processing.wav"))
            conversation_log += "User: " + user_input + "\n"
            extractor_response = extractor.run_extraction_step(user_input)
            conversation_log += "Call-E: " + extractor_response + "\n"
            sf.say(extractor_response)
            
        if extractor.get_status() != ExtractionStatus.COMPLETED:
            print("Extraction aborted")
            
            cursor = self.db.cursor()
            cursor.execute(f"""
            UPDATE {self.outgoing_conversation_title}_status SET status = "ABORTED" WHERE contact_id = ?
            """, (contact_id,))
            self.db.commit()
        else:
            # successful extraction, save results in db
            print("Extraction completed")
            
            cursor = self.db.cursor()
            cursor.execute(f"""
            UPDATE {self.outgoing_conversation_title}_status SET status = "COMPLETED" WHERE contact_id = ?
            """, (contact_id,))
            
            information = extractor.get_information()
            cursor.execute(f"""
                INSERT OR REPLACE INTO {self.outgoing_conversation_title} (
                    contact_id, {', '.join([key.lower() for key in information.keys()])}
                    )
                VALUES (
                    ?, {', '.join(['?']*len(information))}
                    )
            """,
            [contact_id] + list(information.values()))
            self.db.commit()
            
        # usually we would hang up here, but if the call is forwarded then should keep the connection open
        while sf.is_forwarded():
            time.sleep(1)
        sf.hangup()
        print("Call ended.")
        
        # save log file
        if enable_logging:
            os.makedirs(HERE / "../logs", exist_ok=True)
            with open(HERE / f"../logs/{self.outgoing_conversation_title}_{contact_id}.log", "w") as log_file:
                log_file.write(conversation_log)
    
    def call_contacts(self, contact_ids = None, maximum_attempts = None):
        if contact_ids is None:  
            cursor = self.db.cursor()
            cursor.execute(f"""
                SELECT contact_id FROM contacts
            """)
            contact_ids = [contact[0] for contact in cursor.fetchall()]
            contact_ids.sort()
        
        if maximum_attempts is None:
            maximum_attempts = float('inf')
            
        for contact_id in contact_ids:
            print(f"Attempting to call contact {contact_id}")
            
            if self.get_contact(contact_id) is None:
                print(f"Invalid contact id: {contact_id}")
                continue
            
            status = self.get_contact_status(contact_id)
            
            if status is None:
                # this is the first call, always should be made
                self.call_contact(contact_id)
                continue
                
            if status['status'] != "NOT_REACHED":
                print(f"Contact {contact_id} has already been reached.")
                continue
            
            if status['num_attempts'] >= maximum_attempts:
                print(f"Contact {contact_id} has reached maximum number of attempts.")
                continue
            
            self.call_contact(contact_id)
            
    def __softphone_listen(self, sf, sf_group, outgoing_conversation_config):
        # register thread
        sf_group.pjsua_endpoint.libRegisterThread('softphone_listen')
        
        try:
            print("Listening...")
            
            while not sf.has_picked_up_call():
                if not sf_group.is_listening:
                    return
                pass
            
            print("Incoming call. Setting up extractor.")

            extractor = llm_extractor(outgoing_conversation_config, softphone=sf)
            extractor_response = extractor.run_extraction_step("")
            sf.say(extractor_response)
            
            while(extractor.get_status() == ExtractionStatus.IN_PROGRESS and sf.has_picked_up_call()):
                user_input = sf.listen()
                sf.play_audio(str(HERE / "../resources/processing.wav"))
                extractor_response = extractor.run_extraction_step(user_input)
                sf.say(extractor_response)
                
            if extractor.get_status() != ExtractionStatus.COMPLETED:
                print("Extraction aborted")
            else:
                print("Extraction completed")
            
            # usually we would hang up here, but if the call is forwarded then should keep the connection open
            while sf.is_forwarded():
                time.sleep(1)
            sf.hangup()
            print("Call ended.")
        except Exception as e:
            print("Exception in listening thread:", e)
            traceback.print_exc()
            sf.hangup()
            
        listen_thread = threading.Thread(target=self.__softphone_listen, args=(sf, sf_group, outgoing_conversation_config))
        listen_thread.start()
            
    
    def start_listening(self, conversation_path, num_devices = 1):
        outgoing_conversation_config = self.__read_conversation_config(conversation_path)
        sf_group = softphone_group(self.__sip_credentials_path)
        for i in range(num_devices):
            sf = softphone(self.__sip_credentials_path, sf_group)
            listen_thread = threading.Thread(target=self.__softphone_listen, args=(sf, sf_group, outgoing_conversation_config))
            listen_thread.start()
        return sf_group
    
    def stop_listening(self, sf_group):
        sf_group.is_listening = False

  

    