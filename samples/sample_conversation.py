from pyckup_core.conversation_config import (
    ChoiceItem,
    ChoiceOption,
    ConversationConfig,
    FunctionChoiceItem,
    FunctionItem,
    InformationItem,
    PathItem,
    PromptItem,
    ReadItem,
)
from samples.sample_trunks import name_is_marius, say_goodbye


def sample_conversation():
    return ConversationConfig(
        title="Sample Conversation",
        paths={
            "entry": [
                ReadItem(
                    text="Hello, thank you for calling. Do you want to introduce yourself?"
                ),
                ChoiceItem(
                    choice="Do you want to introduce yourself?",
                    silent=True,
                    options=[
                        ChoiceOption(
                            option="I do",
                            dial_number=1,
                            items=[
                                InformationItem(
                                    title="name",
                                    description="The first name of the user.",
                                    format="The first name of the user, starting with a capital letter.",
                                ),
                                FunctionChoiceItem(
                                    function=name_is_marius,
                                    options=[
                                        ChoiceOption(
                                            option=True,
                                            items=[
                                                PathItem(
                                                    path="is_marius",
                                                )
                                            ],
                                        ),
                                        ChoiceOption(
                                            option=False,
                                            items=[
                                                PathItem(
                                                    path="is_not_marius",
                                                )
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        ChoiceOption(
                            option="I don't",
                            dial_number=2,
                            items=[
                                PromptItem(prompt="Wish the user a nice day."),
                            ],
                        ),
                    ],
                ),
            ],
            "is_marius": [
                ChoiceItem(
                    choice="Do you prefer apples or oranges?",
                    options=[
                        ChoiceOption(
                            option="Apples",
                            dial_number=1,
                            items=[
                                ReadItem(text="Apples are great!"),
                            ],
                        ),
                        ChoiceOption(
                            option="Oranges",
                            dial_number=2,
                            items=[
                                ReadItem(text="Oranges are great!"),
                            ],
                        ),
                    ],
                )
            ],
            "is_not_marius": [
                FunctionItem(
                    function=say_goodbye,
                )
            ],
            "aborted": [
                PromptItem(prompt="Apologize to the user."),
                ReadItem(text="Goodbye, thank you again for calling."),
            ],
        },
    )
