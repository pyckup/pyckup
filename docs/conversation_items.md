# Conversation Items

Each conversation is comprised of paths, that are in turn lists of conversation items. At the start of each conversation, the `entry` path will be executed, meaning each conversation item in this path will be handled from top to bottom. If the user indicates that they want to quit the conversation, the `abort` path is called automatically. You can define new paths and call them through the `path` conversation item. Conversation configs can be directly created as an instance of the ConversationConfig class or be parsed from a yaml file using ConversationConfig.from_yaml().

## Read
```yaml
-   type: read
    text: <text>
```
### Description
Read out some text.
### Parameters
-   `text (str)`: The text to be read out.


## Prompt
```yaml
-   type: prompt
    prompt: <prompt>
```
### Description
Directly pass a prompt to the agent for execution.
### Parameters
-   `prompt (str)`: The prompt to be passed to the agent.


## Choice
```yaml
-   type: choice
    choice: <choice>
    silent: <silent>
    options:
    -   option: <option_1>
        dial_number: <dial_number_1>
        items: <items_1>
    -   option: <option_2>
        dial_number: <dial_number_2>
        items: <items_2>
    -   ...
    -   option: <option_n>
        dial_number: <dial_number_n>
        items: <items_n>
```
### Description
Branch between multiple paths depending on the user input. Each path is defined by an `option` item, that contains a description of the option and the conversation items to be executed upon choosing this option. A choice can be made not only through voice input but also by pressing the associated DTMF-number.
### Parameters
-   `choice (str)`: Description of the choice or direct prompt to the user.
-   `silent (bool)`:  When true, the choice will be prompted to the user; when false, only input will be taken.
-   `option_n (str)`: Clear identifier or short description of the option.
-   `dial_number_n (int, optional)`: The DTMF number to be pressed to choose this option.
-   `items (list[conversation_items])`: The conversation items to be executed when choosing this option.


## Information
```yaml
-   type: information
    title: <title>
    description: <description>
    format: <format>
```
### Description
Extract some information from the user. The information will be saved in the conversation state under the provided title in the given format.
### Parameters
-   `title (str)`: Identifier of the information, will be used when saving in the conversation state.
-   `description (str)`: Description of what information should be extracted from the user.
-   `format (str)`: Description of how the extracted data should be formatted when saving.


## Function
```yaml
-   type: function
    module: <module>
    function: <function>
```
### Description
Call a function from you Python code. The function should be implemented like this:
```python
def your_function(conversation_state, softphone):
    # enter your code here
    return text_to_be_read
```
`conversation state` is a dict containing all the previously extracted information. `softphone` is the Softphone object that is used to handle the call. See the [Softphone API](api.md#softphone) for more details. The text returned by your function will be read out by the agent. Can return None.
### Parameters
-   `module (str)`: The module your function is located in.
-   `function (str)`: The name of your function.


## Function Choice
```yaml
-   type: function_choice
    module: <module>
    function: <function>
    options:
    -   option: <option_1>
        dial_number: <dial_number_1>
        items: <items_1>
    -   option: <option_2>
        dial_number: <dial_number_2>
        items: <items_2>
    -   ...
    -   option: <option_n>
        dial_number: <dial_number_n>
        items: <items_n>
```
### Description
Branch between multiple paths depending on the output of a function from you Python code. Your function should be implemented like for the `Function` conversation item, except the return value will not be read out but rather matched against the option descriptions. If a matching option is found, their items will be executed.
### Parameters
-   `module (str)`: The module your function is located in.
-   `function (str)`: The name of your function.
-   `option_n (str)`: The option description, matched against the return value of your function.
-   `items (list[conversation_items])`: The conversation items to be executed when choosing this option.


## Path
```yaml
-   type: path
    path: <path>
```
### Description
Execute conversation items from the given path. The remaining items of the current path will not be executed.
### Parameters
-   `path (str)`: Identifier of the path to be executed.

