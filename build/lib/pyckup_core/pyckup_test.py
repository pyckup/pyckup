import os
from pathlib import Path
from pyckup_core.conversation_config import ConversationConfig
from pyckup_core.pyckup import Pyckup
from samples.sample_conversation import sample_conversation

HERE = Path(os.path.abspath(__file__)).parent

calle = Pyckup(HERE / "../sip_credentials_alt.json", log_dir="logs") #, HERE / "../call_e.db")
# calle2 = call_e(HERE / "../sip_credentials.json") #, HERE / "../call_e.db")

# calle.add_contact("Max Rütz", "+4933123189599")
# calle.add_contact("Marius Merlin", "+4933123189599")
# calle.call_contacts(HERE / "../samples/sample_conversation_config.yaml")
# calle.call_number("+4933123189599", HERE / "../samples/sample_conversation_config.yaml")
# calle.call_number("+4933176993008", HERE / "../samples/sample_conversation_config.yaml")

# calle.call_numbers(["+4933123189599", "+4933123189599"], HERE / "../samples/sample_conversation_config.yaml")

# calle.call_contact(1, HERE / "../samples/sample_conversation_config.yaml")
# calle.call_contact("+4917656776025")
# calle.call_contact("+4915120776050")
# grp = calle.start_listening(HERE / "../samples/sample_conversation_config.yaml", num_devices=1)
grp = calle.start_listening(ConversationConfig.from_yaml(str(HERE / "../demos/fibonacci/fibonacci_config.yaml")), num_devices=1)
# grp = calle.start_listening(sample_conversation(), num_devices=1)


# grp2 = calle2.start_listening(HERE / "../samples/sample_conversation_config.yaml", num_devices=1)
input()
calle.stop_listening(grp)

print("done")