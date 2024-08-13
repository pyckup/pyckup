import os
from pathlib import Path
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.output_parsers.string import StrOutputParser
from langchain_core.runnables import RunnableBranch
from langchain_openai import ChatOpenAI
from langchain_community.llms import Ollama
import yaml
from enum import Enum


HERE = Path(os.path.abspath(__file__)).parent


class llm_extractor:
    def __init__(self, information_config, llm_provider="openai"):
        """Create LLM extractor object.

        Args:
            information_config (dict): Which informations are to be extracted.
            llm_provider (str, optional): Which LLM to use. Options: openai, ollama. Defaults to "openai".
        """
        if llm_provider == "openai":
            self.__llm = ChatOpenAI(api_key=os.environ["OPENAI_API_KEY"])
        elif llm_provider == "ollama":
            self.__llm = Ollama(model="gemma2:2b-instruct-q3_K_M")
        else:
            raise ValueError("Invalid LLM provider. Options: openai, llama.")
        
        self.status = ExtractionStatus.IN_PROGRESS
        self.chat_history = []


        self.__information_to_extract = information_config
        self.__current_information = self.__information_to_extract.pop(0)
        self.extracted_information = {}

        self.information_extraction_chain = self.__verify_information | RunnableBranch(
            (
                lambda data: data["verification_status"] == "YES",
                self.__extraction_successful,
            ),
            (
                lambda data: data["verification_status"] == "NO",
                self.__make_information_extractor,
            ),
            self.__extraction_aborted,
        )
        
    def __verify_information(self, data):
        """
        Check if the user message contains the required information. Append result to data object.
        """

        verification_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """Check if the required information is contained inside the user message. If so, 
            output the single word 'YES'. If not, output the single word 'NO'. If the user says in some way
            that they don't want to provide the information, output 'ABORT'. Don't ouput anything but
            YES, NO or ABORT. If the user provides no message, output NO.""",
                ),
                ("system", "Required information: {current_information_description}"),
                ("user", "{input}"),
            ]
        )
        verifyer_chain = verification_prompt | self.__llm | StrOutputParser()
        data["verification_status"] = verifyer_chain.invoke(data).strip()
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
        output '##FAILED##""",
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
                    """Have a casual conversation with the user. Over the course of the conversation you are
                    supposed to extract different pieces of information from the user. Start by greeting them like you just called them.
                    Be open that you are looking for some information and ask them if they can help you with that.
                    If the user derivates from the topic of the information you want to have, gently guide 
                    them back to the topic.""",
                ),
                ("system", "Information you want to have: {current_information_description}"),
                MessagesPlaceholder(variable_name="chat_history"),
                ("user", "{input}"),
            ]
        )
        information_extractor = extraction_prompt | self.__llm | StrOutputParser()
        return information_extractor

    def __extraction_successful(self, data):
        """
        Store filtered extracted information and either continue with the next information or finish
        the process by thanking the user.
        """
        self.extracted_information[self.__current_information["title"]] = (
            self.__filter_information(data)
        )

        if len(self.__information_to_extract) == 0:
            # done extracting
            self.status = ExtractionStatus.COMPLETED
            extraction_finished_prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        """Thank the user shortly for providing the information and wish them a nice day. Don't 
                offer them any more assistance, they won't be able to communicate with you further.""",
                    ),
                    MessagesPlaceholder(variable_name="chat_history"),
                    ("user", "{input}"),
                ]
            )
            return extraction_finished_prompt | self.__llm | StrOutputParser()
        else:
            # continue with next information
            self.__current_information = self.__information_to_extract.pop(0)
            data["current_information_description"] = self.__current_information
            return self.__make_information_extractor(data).invoke(data)

    def __extraction_aborted(self, data):
        """
        End the process and return a subchain that apologizes to the user.
        """
        self.status = ExtractionStatus.ABORTED
        extraction_aborted_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", """Apologize to the user and wish them a nice day. Don't 
                offer them any more assistance, they won't be able to communicate with you further."""),
                MessagesPlaceholder(variable_name="chat_history"),
                ("user", "{input}"),
            ]
        )
        return extraction_aborted_prompt | self.__llm | StrOutputParser()

    def run_extraction_step(self, user_input):
        """Try to extract information from the user input and return a LLM reponse that can be output.

        Args:
            user_input (str): Most recent chat message from the user.
        """
        response = self.information_extraction_chain.invoke(
            {
                "input": user_input,
                "chat_history": self.chat_history,
                "current_information_description": self.__current_information[
                    "description"
                ],
                "current_information_format": self.__current_information["format"],
            }
        )
        self.chat_history.extend(
            [HumanMessage(content=user_input), AIMessage(content=response)]
        )
        return response
    
    def get_information(self):
        return self.extracted_information
    
    def get_status(self):
        return self.status


class ExtractionStatus(Enum):
    IN_PROGRESS = 0
    COMPLETED = 1
    ABORTED = 2
