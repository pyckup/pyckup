# About
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

A good point to get started with Pyckup is the [quickstart](quickstart.md).
