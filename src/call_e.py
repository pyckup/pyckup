
import os
from pathlib import Path
import yaml
from llm_extractor import llm_extractor, ExtractionStatus
from softphone import softphone
import sqlite3

HERE = Path(os.path.abspath(__file__)).parent



class call_e:
    
    conversation_config = None
    conversation_title = None
    db = None
    
    def __init__(self, conversation_config_path):
        self.__setup_db()
        self.update_conversation_config(conversation_config_path)
        
    def __del__(self):
        if self.db is not None:
            self.db.close()
        
    def __read_conversation_config(self, config_path):
        with open(config_path, 'r') as config_file:
            return yaml.safe_load(config_file)
    
    def update_conversation_config(self, config_path):
        self.conversation_config = self.__read_conversation_config(config_path)
        
        # ensure that results table exists
        self.conversation_title = self.conversation_config['conversation_title'].lower().replace(" ", "_")
        fields = ""
        for item in self.conversation_config['active_conversation']:
            if 'title' in item:
                fields += ",\n"
                fields +=  f"{item['title'].lower().replace(' ', '_')} TEXT"
            
        cursor = self.db.cursor()
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.conversation_title} (
                result_id INTEGER PRIMARY KEY, 
                contact_id INTEGER UNIQUE{fields}
            )"""
        )	
        
        # ensure that status table exists
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.conversation_title}_status (
                status_id INTEGER PRIMARY KEY, 
                contact_id INTEGER UNIQUE,
                num_attempts INTEGER,
                status TEXT
            )"""
        )	
        
        
        self.db.commit()
    
    def __setup_db(self):
        self.db = sqlite3.connect(HERE / "../call_e.db")
        cursor = self.db.cursor()
        
        # Ensure contacts table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                contact_id INTEGER PRIMARY KEY, 
                name TEXT,
                phone_number TEXT,
                adress TEXT,
                CONSTRAINT unq UNIQUE (name, phone_number)
            )"""
        )
        self.db.commit()
    
    def add_contact(self, name, phone_number, adress):
        cursor = self.db.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO contacts (name, phone_number, adress) VALUES (?, ?, ?)
        """, (name, phone_number, adress))
        
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
            "adress": contact_data[3]
        }
    
    def get_contact_status(self, contact_id):
        cursor = self.db.cursor()
        cursor.execute(f"""
            SELECT * FROM {self.conversation_title}_status WHERE contact_id = ?
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
            INSERT OR IGNORE INTO {self.conversation_title}_status (contact_id, num_attempts, status) VALUES (?, 0, "NOT_REACHED")
            """, (contact_id,))
        
        # increment number of attempts
        cursor.execute(f"""
            UPDATE {self.conversation_title}_status SET num_attempts = num_attempts + 1 WHERE contact_id = ?
            """, (contact_id,))
        
        self.db.commit()
        
        sf = softphone()
        print("Calling " + contact['phone_number'] + "...")
        sf.call(contact['phone_number'])
        sf.wait_for_stop_calling()
        
        if not sf.has_picked_up_call():
            print("Call not picked up.")
            return
        
        print("Call picked up. Setting up extractor.")

        extractor = llm_extractor(self.conversation_config)
        extractor_response = extractor.run_extraction_step("")
        conversation_log += "Call-E: " + extractor_response + "\n"
        sf.say(extractor_response)
        
        while(extractor.get_status() == ExtractionStatus.IN_PROGRESS and sf.has_picked_up_call()):
            user_input = sf.listen()
            conversation_log += "User: " + user_input + "\n"
            extractor_response = extractor.run_extraction_step(user_input)
            conversation_log += "Call-E: " + extractor_response + "\n"
            sf.say(extractor_response)

        sf.hangup()
        print("Call ended.")
            
        if extractor.get_status() != ExtractionStatus.COMPLETED:
            print("Extraction aborted")
            
            cursor = self.db.cursor()
            cursor.execute(f"""
            UPDATE {self.conversation_title}_status SET status = "ABORTED" WHERE contact_id = ?
            """, (contact_id,))
            self.db.commit()
        else:
            # successful extraction, save results in db
            print("Extraction completed")
            
            cursor = self.db.cursor()
            cursor.execute(f"""
            UPDATE {self.conversation_title}_status SET status = "COMPLETED" WHERE contact_id = ?
            """, (contact_id,))
            
            information = extractor.get_information()
            cursor.execute(f"""
                INSERT OR REPLACE INTO {self.conversation_title} (
                    contact_id, {', '.join([key.lower() for key in information.keys()])}
                    )
                VALUES (
                    ?, {', '.join(['?']*len(information))}
                    )
            """,
            [contact_id] + list(information.values()))
            self.db.commit()
            
        # save log file
        if enable_logging:
            os.makedirs(HERE / "../logs", exist_ok=True)
            with open(HERE / f"../logs/{self.conversation_title}_{contact_id}.log", "w") as log_file:
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
  

    