import importlib
from typing import Any, Callable, Dict, List
from pydantic import BaseModel
import yaml

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
        selected_options = [option for option in self.options if option.option == choice]
        if not selected_options:
            return []
        return selected_options[0].items

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
    def _parse_items(cls, items: List[Dict[str, Any]]) -> List[ConversationItem]:
        """
        Parse the items in the conversation config file.

        Args:
            items (list): The list of items to parse.

        Returns:
            list: The parsed conversation items.
        """
        type_mapping = {
            "read": ReadItem,
            "prompt": PromptItem,
            "choice": ChoiceItem,
            "information": InformationItem,
            "function": FunctionItem,
            "function_choice": FunctionChoiceItem,
            "path": PathItem,
        }

        parsed_items = []
        for item in items:
            # recursively parse items
            if item["type"] == "choice" or item["type"] == "function_choice":
                for option in item["options"]:
                    option["items"] = cls._parse_items(option["items"])

            # set function
            if item["type"] == "function" or item["type"] == "function_choice":
                module = importlib.import_module(item["module"])
                item["function"] = getattr(module, item["function"])
                item.pop("module")

            item_type = type_mapping.get(item["type"])
            if item_type:
                parsed_items.append(item_type(**item))
            else:
                raise ValueError(f"Unknown item type: {item['type']}")

        return parsed_items

    @classmethod
    def from_yaml(cls, path: str) -> "ConversationConfig":
        with open(path, "r") as config_file:
            config_dict = yaml.safe_load(config_file)

        paths = {}
        for path in config_dict["conversation_paths"].items():
            paths[path[0]] = cls._parse_items(path[1])

        return cls(title=config_dict["conversation_title"], paths=paths)
