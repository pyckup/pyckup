# Quickstart

Here we will set up a simple Hello-World-service, that when called will simply read out "Hello World" an then end the call.

## Install Pyckup

Follow the [installation guide](installation.md).

## Setup your SIP credentials

To use Pyckup, you will need a [SIP-trunk](https://aws.amazon.com/what-is/sip-trunking/), basiscally a virtual phone line access. You can rent SIP-trunks for relatively cheap from a variety of providers. Once you have your trunk setup, you can create a `sip_credentials.json` file.

```json
{
    "idUri": "sip:<username>@<registrar>",
    "registrarUri": "sip:<registrar>",
    "username": "<username>",
    "password": "<password>"
}
```

Replace the placeholder values with the actual ones from your SIP account.

## Setup conversation config

The conversation config is the heart of your Pyckup application and contains a blueprint of each user conversation that is followed by the AI agent. It is composed of multiple conversation items, that respresent different actions the agent can perform.  
You can create a new ConversationConfig like this:

```python
from pyckup_core.conversation_config import ConversationConfig, ReadItem

config = ConversationConfig(
    title="Hello World Demo",
    paths={
        "entry": [
            ReadItem(
                text="Hello World"
            ),
        ],
        "aborted": []    
    }
)
```

You can also specify the values in a `hello_world_config.yaml` and later parse it to a ConversationConfig:
```yaml
conversation_title: Hello World Demo
conversation_paths:
  entry: 
    - type: read 
      text: Hello World
  aborted:
```

This simple example just reads out "Hello World" after the conversation has started and then ends. For a full list of possible actions, see the [conversation items documentation](conversation_items.md).

## Start Pyckup

Now you have everything you need to jump into your Python code. We can start listening for incoming calls in a few lines of code.

```python
from pyckup_core.pyckup import Pyckup

pu = Pyckup("sip_credentials.json")

grp = pu.start_listening(ConversationConfig.from_yaml("hello_world_config.yaml"))
# calls can be recieved during this time
input()
pu.stop_listening(grp)
```

`grp` is of type SoftphoneGroup and manages all the softphones that are used by Pyckup to handle the incoming calls.


## Try it out

Go ahead and call the number of your SIP-trunk. You should be greeted by a welcoming "Hello World"!