## About
☎️ Pyckup is a Python package used to make and recieve calls and have them conducted by AI.  
🔧 Can be used to build all kinds of realtime applications that work over the telephone network, including ones with complex conversational flows.  
📖 It's like [Bland](https://www.bland.ai/) or [Synthflow](https://synthflow.ai/), but Open Source.  
⚙️ Conversations can be defined by providing a YAML conversation configuration file (you can imagine the agent following a flowchart of conversation items that you specify). See annotated example in samples/sample_conversation_config.yaml.  
📞 Calls are made via a SIP softphone using the [PJSUA2 library](https://docs.pjsip.org/en/latest/pjsua2/intro.html). LLM and TTS/STT services accessed through [OpenAI API](https://platform.openai.com/docs/overview). 

### Features
-    design complex conversation flows, including branching, conversation state, information input
-    custom code integration allows for limitless use cases
-    quick realtime responses
-    multiple simultaneous calls on the same number
-    fully functional SIP softphone
-    DTMF input

## Setup
1. Install the package: `pip install git+https://github.com/pyckup/pyckup.git
2. Install FFMPEG: `sudo apt install ffmpeg`
3. [Install PJSUA2](https://docs.pjsip.org/en/latest/pjsua2/building.html) (there seems to be an issue in newer versions of the library, so checkout [this commit](https://github.com/pjsip/pjproject/commit/f5d890aa3463a096d7110ae935c67d6249d2f662) )
4. Setup OPENAI API environment variable: `export OPENAI_API_KEY=<your_openai_api_key>`      

## Example Usage
### Making Calls

    from pyckup_core.pyckup import Pyckup

    pu = Pyckup("samples/sample_credentials.json")

    # Call a single phone number
    pu.call_number("+4912345678", "../samples/sample_conversation_config.yaml")

    # Call multiple phone numbers
    pu.call_numbers(["+4912345678", "+499876543"], "../samples/sample_conversation_config.yaml")

    # Call a single contact
    pu.add_contact("Marius Testperson", "+4912345678")
    pu.call_contact(1, "../samples/sample_conversation_config.yaml")

    # Call multiple contacts
    pu.add_contact("Maria Testperson", "+499876543")
    pu.call_contacts("../samples/sample_conversation_config.yaml")


### Recieving calls
    grp = pu.start_listening("../samples/sample_conversation_config.yaml", num_devices=1)
    # calls can be recieved during this time
    input()
    pu.stop_listening(grp)

## Contributing
For information on how to contribute to the repository, see [the contribution guide](CONTRIBUTING.md)
