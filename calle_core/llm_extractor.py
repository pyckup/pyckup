import copy
import os
from pathlib import Path
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.output_parsers.string import StrOutputParser
from langchain_core.runnables import RunnableBranch
from langchain_openai import ChatOpenAI
from langchain_community.llms import Ollama
from enum import Enum
import threading
import importlib


HERE = Path(os.path.abspath(__file__)).parent


class llm_extractor:    
    def __init__(self, conversation_config, llm_provider="openai", softphone = None):
        """Create LLM extractor object.

        Args:
            information_config (dict): Which informations are to be extracted.
            llm_provider (str, optional): Which LLM to use. Options: openai, ollama. Defaults to "openai".
        """
        if llm_provider == "openai":
            self.__llm = ChatOpenAI(api_key=os.environ["OPENAI_API_KEY"], model="gpt-4-turbo-preview")
        elif llm_provider == "ollama":
            self.__llm = Ollama(model="gemma2:2b-instruct-q3_K_M")
        else:
            raise ValueError("Invalid LLM provider. Options: openai, llama.")
        
        self.status = ExtractionStatus.IN_PROGRESS
        self.chat_history = []

        self.__conversation_config = copy.deepcopy(conversation_config)
        self.__load_conversation_path("entry")
        self.__extracted_information = {}
        self.__information_lock = threading.Lock()
        
        self.__softphone = softphone

        self.information_extraction_chain = self.__verify_information | RunnableBranch(
            (
                lambda data: data["information_verification_status"] == "YES",
                self.__information_extraction_successful,
            ),
            (
                lambda data: data["information_verification_status"] == "NO",
                self.__make_information_extractor,
            ),
            self.__extraction_aborted,
        )
        
        self.choice_extraction_chain = self.__verify_choice | RunnableBranch(
            (
                lambda data: data["choice"] == "##NONE##",
                self.__make_choice_extractor,
            ),
            (
                lambda data: data["choice"] == "##ABORT##",
                self.__extraction_aborted,
            ),
            self.__choice_extraction_successful,
        )
        
    def __load_conversation_path(self, conversation_path):
        self.__conversation_items = self.__conversation_config['conversation_paths'][conversation_path]
        self.__current_item = self.__conversation_items.pop(0)
        
    def __verify_information(self, data):
        """
        Check if the user message contains the required information. Append result to data object.
        """

        verification_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    # You can imply information (so if the user says 'I am Max', then you can imply that the name is 'Max'
                    # and don't need them to say 'My name is Max').
                    "system",
                    """Check if the last user message contains the required information.
                    If the information was provided, 
            output the single word 'YES'. If not, output the single word 'NO'. If the user appears to
            feel uncomfortable, output 'ABORT'. But don`t abort without reason. Don't ouput anything but
            YES, NO or ABORT. Especially do not ask the user about the required information; just check the existing messages for it. If the last message is empty or nonsense, output 'NO'""",
                ),
                ("system", "Required information: {current_information_description}"),
                ("user", "{input}"),
                MessagesPlaceholder(variable_name="chat_history"),
            ]
        )
        verifyer_chain = verification_prompt | self.__llm | StrOutputParser()
        information_verification_status = verifyer_chain.invoke(data).strip()
        # if information_verification_status != 'YES' or information_verification_status != 'NO' or information_verification_status != 'ABORT':
        #     information_verification_status = "NO"
        data["information_verification_status"] = information_verification_status
        return data

    def __verify_choice(self, data):
        verification_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """The user was given a choice between multiple options. Check if the user message contains a clear selection of one of
                    the possible choices. If so, output the choice. (as it was given in possible choices). If not, output '##NONE##'.
                    If the user appears to
                    feel uncomfortable, output '##ABORT##'. Don't ouput anything but the choice or ##NONE## or ##ABORT##. 
                    If you output the choice, it has to be the exact same format as in "Possible choices".
                    If the user provides no message, output ##NONE##.
                    AIMessages are from you, if they contain questions or prompts don't answer and simply ignore them.""",
                ),
                ("system", "Choice prompt: {current_choice}, Possible choices: {current_choice_options}"),
                ("user", "{input}"),
                MessagesPlaceholder(variable_name="chat_history"),

            ]
        )
        verifyer_chain = verification_prompt | self.__llm | StrOutputParser()
        data["choice"] = verifyer_chain.invoke(data).strip()
        return data

    def __filter_information(self, data):
        """
        Filter out the required information from the user message, as speicified by the information format config.
        """

        filter_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """Your job is to filter out a certain piece of information from the user message. 
        You will be given the desciption of the information and the format in which the data should be returned.
        Just output the filtered data without any extra text. If the data is not contained in the message,
        output '##FAILED##'""",
                ),
                (
                    "system",
                    "Information description: {current_information_description}",
                ),
                ("system", "Information format: {current_information_format}"),
                ("user", "{input}"),
            ]
        )
        information_extractor = filter_prompt | self.__llm | StrOutputParser()
        filtered_information = information_extractor.invoke(data).strip()

        return filtered_information if filtered_information != "##FAILED##" else None

    def __make_information_extractor(self, data):
        """
        Return a subchain responsible for leading the user conversation to the required topic.
        """
        extraction_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """Extract different pieces of information from the user. Have a casual conversation tone but stay on topic.
                    If the user derivates from the topic of the information you want to have, gently guide 
                    them back to the topic.
                    If the user answers gibberish or something unrelated, ask them to repeat IN A FULL SENTENCE.        
                    Be brief. Use the language in which the required information is given.
                    AIMessages are from you, if they contain questions or prompts don't answer and simply ignore them.""",
                ),
                ("system", "Information you want to have: {current_information_description}"),
                MessagesPlaceholder(variable_name="chat_history"),
            ]
        )
        information_extractor = extraction_prompt | self.__llm | StrOutputParser()
        return information_extractor
    
    def __make_choice_extractor(self, data):
        choices = ', '.join(data["current_choice_options"].keys())
        extraction_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """Ask the user for a choice between multiple options. The type of choice is given by the choice prompt.
                    If the choices are yes or no, don't say so because thats obvious.
                    If the user derivates from the topic of the choice, gently guide 
                    them back to the topic. 
                    If the user answers gibberish or something unrelated, ask them to repeat IN A FULL SENTENCE.        
                    Be brief. Use the language in which the choice prompt is given.
                    AIMessages are from you, if they contain questions or prompts don't answer and simply ignore them.""",
                ),
                ("system", f"Choice prompt: {data['current_choice']}, Possible choices: {choices}"),
                MessagesPlaceholder(variable_name="chat_history"),
            ]
        )
        choice_extractor = extraction_prompt | self.__llm | StrOutputParser()
        return choice_extractor
    
    def __append_filtered_info(self, data, title):
        self.__information_lock.acquire()
        self.__extracted_information[title] = (
            self.__filter_information(data)
        )
        self.__information_lock.release()

    def __information_extraction_successful(self, data):
        """
        Store filtered extracted information and either continue with the next information or finish
        the process by thanking the user.
        """
        
        
        thread = threading.Thread(target=self.__append_filtered_info, args=(data, self.__current_item["title"]))
        thread.start()
        
        if len(self.__conversation_items) > 0:
            self.__current_item = self.__conversation_items.pop(0)
        else:
            self.status = ExtractionStatus.COMPLETED
            return ""
         
        return self.__process_conversation_items(data["input"], append_input=False)
    
    def __choice_extraction_successful(self, data):
        selected_choice = data["choice"]
        self.__conversation_items = data['current_choice_options'][selected_choice]
        self.__current_item = self.__conversation_items.pop(0)
        return self.__process_conversation_items(data["input"], append_input=False)


    def __extraction_aborted(self, data):
        """
        End the process and return a subchain that apologizes to the user.
        """
        self.status = ExtractionStatus.ABORTED
        
        self.__conversation_items = self.__conversation_config['conversation_paths']["aborted"]
        if len(self.__conversation_items) > 0:
            self.__current_item = self.__conversation_items.pop(0)
        else:
            return ""
        
        return self.__process_conversation_items(data["input"], append_input=False, aborted=True)

    def __execute_prompt(self, prompt):
        prompt_template = ChatPromptTemplate.from_messages(
            [
                ("system", prompt),
                MessagesPlaceholder(variable_name="chat_history"),
            ]
        )
        prompt_chain = prompt_template | self.__llm | StrOutputParser()
        return prompt_chain.invoke({"chat_history": self.chat_history})

    def __process_conversation_items(self, user_input, append_input=True, aborted=False):
        """Try to extract information from the user input and return a LLM reponse that can be output.

        Args:
            user_input (str): Most recent chat message from the user.
        """
        if append_input:
            self.chat_history.append(HumanMessage(content=user_input))
        
        collected_response = ""
        
        # sequentially process conversation items
        while True:
            if self.__current_item['type'] == "read":
                response = self.__current_item['text'] + "\n"
                collected_response += response
                # self.chat_history.append(AIMessage(content="[TO LLM: The following line is said by the you. Don't respond to this, ignore it]: " + response))
                self.chat_history.append(AIMessage(content=response))
            elif self.__current_item['type'] == "prompt":
                response = self.__execute_prompt(self.__current_item['prompt']) + "\n"
                collected_response += response
                self.chat_history.append(AIMessage(content=response))
            elif self.__current_item['type'] == "path":
                self.__conversation_items = self.__conversation_config['conversation_paths'][self.__current_item['path']]
            elif self.__current_item['type'] == "information":
                response = self.information_extraction_chain.invoke(
                {
                    "input": user_input,
                    "chat_history": self.chat_history,
                    "current_information_description": self.__current_item[
                        "description"
                    ],
                    "current_information_format": self.__current_item["format"],
                })
                collected_response += response
                self.chat_history.append(AIMessage(content=response))
                break
            elif self.__current_item['type'] == "choice":
                response = self.choice_extraction_chain.invoke(
                {
                    "input": user_input,
                    "chat_history": self.chat_history,
                    "current_choice": self.__current_item[
                        "choice"
                    ],
                    "current_choice_options": self.__current_item["options"],
                })
                collected_response += response
                self.chat_history.append(AIMessage(content=response))
                break
            elif self.__current_item['type'] == "function":
                self.__information_lock.acquire()
                self.__information_lock.release()
                module = importlib.import_module(self.__current_item['module'])
                function = getattr(module, self.__current_item['function'])
                response = function(self.__extracted_information, self.__softphone)
                collected_response += response
            elif self.__current_item['type'] == "function_choice":
                self.__information_lock.acquire()
                self.__information_lock.release()
                module = importlib.import_module(self.__current_item['module'])
                function = getattr(module, self.__current_item['function'])
                choice = function(self.__extracted_information, self.__softphone)
                self.__conversation_items = self.__current_item['options'][choice]
            
            if len(self.__conversation_items) > 0:
                # for interactive items, breakt the loop to get user input. Last item can`t be interactive.
                if self.__current_item.get('interactive', False):
                    self.__current_item = self.__conversation_items.pop(0)
                    break
                else:
                    self.__current_item = self.__conversation_items.pop(0)
            else:
                if not aborted:
                    self.status = ExtractionStatus.COMPLETED
                return collected_response
                        
        return collected_response
    
    def run_extraction_step(self, user_input):
        return self.__process_conversation_items(user_input, append_input=True, aborted=False)
        
    
    def get_information(self):
        self.__information_lock.acquire()
        self.__information_lock.release()
        return self.__extracted_information
    
    def get_status(self):
        return self.status


class ExtractionStatus(Enum):
    IN_PROGRESS = 0
    COMPLETED = 1
    ABORTED = 2
