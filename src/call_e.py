
import os
from pathlib import Path
import yaml
from llm_extractor import llm_extractor, ExtractionStatus
from softphone import softphone

HERE = Path(os.path.abspath(__file__)).parent



class call_e:
    
    information_config = None
    
    def __init__(self, information_config_path):
        self.update_information_config(information_config_path)
        
    def __read_information_config(self, config_path):
        with open(config_path, 'r') as config_file:
            return yaml.safe_load(config_file)
    
    def update_information_config(self, config_path):
        self.information_config = self.__read_information_config(config_path)
    
    def setup_db(self):
        # check if 'contacts' table exists
        # check if tables for current information config exists (hash of information titles) -> tables information and status
        ()
        
    # TODO: change to contact_id in db
    def call_contact(self, contact_number, enable_logging = True):
        conversation_log = ""
        
        sf = softphone()
        print("Calling " + contact_number + "...")
        sf.call(contact_number)
        sf.wait_for_stop_calling()
        
        if not sf.has_picked_up_call():
            print("Call not picked up.")
            return
        
        print("Call picked up. Setting up extractor.")

        extractor = llm_extractor(self.information_config)
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
        else:
            print(extractor.get_information())
            
        if enable_logging:
            os.makedirs(HERE / "../logs", exist_ok=True)
            with open(HERE / f"../logs/{contact_number}.log", "w") as log_file:
                log_file.write(conversation_log)
    