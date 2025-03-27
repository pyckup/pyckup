## About
Python package used to make and recieve calls and have them conducted by AI.  
Can be used to build all kinds of realtime applications that work over the telephone network, including ones with complex conversational flows.  
It's like [Bland](https://www.bland.ai/) or [Synthflow](https://synthflow.ai/), but Open Source.
Conversations can be defined by providing a YAML conversation configuration file (you can imagine the agent following a flowchart of conversation items that you specify). See annotated example in samples/sample_conversation_config.yaml.  
Calls are made via a SIP softphone using the PJSUA2 library. LLM and TTS/STT services accessed through OpenAI API. 

## Setup
1. Install package 
    - **automatically**: `pip install git+https://github.com/ruetzmax/call-e.git` (you may have to install missing dependencies) OR
    - **manually**: `git clone https://github.com/ruetzmax/call-e.git`, `cd call-e`, `pip install -r requirements.txt`, `pip install .`
2. [Install PJSUA2](https://docs.pjsip.org/en/latest/pjsua2/building.html) (there seems to be an issue in newer versions of the library, so checkout [this commit](https://github.com/pjsip/pjproject/commit/f5d890aa3463a096d7110ae935c67d6249d2f662) )
3. Setup OPENAI API environment variable: `export OPENAI_API_KEY=<your_openai_api_key>`      

## Example Usage
### Making Calls

    from call_e import call_e

    calle = call_e("samples/sample_credentials.json")

    # Call a single phone number
    calle.call_number("+4912345678", "../samples/sample_conversation_config.yaml")

    # Call multiple phone numbers
    calle.call_numbers(["+4912345678", "+499876543"], "../samples/sample_conversation_config.yaml")

    # Call a single contact
    calle.add_contact("Marius Testperson", "+4912345678")
    calle.call_contact(1, "../samples/sample_conversation_config.yaml")

    # Call multiple contacts
    calle.add_contact("Maria Testperson", "+499876543")
    calle.call_contacts("../samples/sample_conversation_config.yaml")


### Recieving calls
    grp = calle.start_listening(HERE / "../samples/sample_conversation_config.yaml", num_devices=1)
    # calls can be recieved during this time
    input()
    calle.stop_listening(grp)

## Contributing
For information on how to contribute to the repository, see [the contribution guide](CONTRIBUTING.md)
