def name_is_marius(conversation_state, softphone):
    is_marius = conversation_state["name"] == "Marius"
    conversation_state["is_marius"] = is_marius
    return is_marius


def say_goodbye(conversation_state, softphone):
    return f"Goodbye {'Marius' if conversation_state['is_marius'] else ''}!"
