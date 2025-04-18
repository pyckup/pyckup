from typing import Any, Callable, Dict, List
from pydantic import BaseModel

from pyckup_core.softphone import Softphone

class ConversationItem(BaseModel):
    interactive: bool = False
    
class ReadItem(ConversationItem):
    text: str
    
class PromptItem(ConversationItem):
    prompt: str
    
class ChoiceOption(BaseModel):
    option: Any
    dial_number: int = 99
    items: List[ConversationItem]
    
class ChoiceItemBase(ConversationItem):
    
    options: List[ChoiceOption]
    
    def get_items_for_choice(self, choice: str) -> List[ConversationItem]:
        """
        Get the conversation items for a given choice of the current choice item.

        Args:
            choice (str): The selected choice.

        Returns:
            list: The conversation items for the selected choice.
        """
        return [
            option
            for option in self.options
            if option.option == choice
        ][0].items
        
    def get_all_options(self) -> List[str]:
        """
        Get all possible options for the current choice item.

        Returns:
            list: The possible options for the current choice item.
        """
        return [option.option for option in self.options]
    
class ChoiceItem(ChoiceItemBase):
    choice: str
    silent: bool = False
    first_run_done: bool = False
    
    
class InformationItem(ConversationItem):
    title: str
    description: str
    format: str
    
class FunctionItem(ConversationItem):
    function: Callable[[Dict[str, Any], Softphone], Any]
    
class FunctionChoiceItem(ChoiceItemBase):
    function: Callable[[Dict[str, Any], Softphone], str]
    options: List[ChoiceOption]

class PathItem(ConversationItem):
    path: str

class ConversationConfig(BaseModel):
    title: str
    paths: dict[str, List[ConversationItem]]
    
    @classmethod
    def from_yaml(cls, path: str) -> 'ConversationConfig':
        pass