import os
from pathlib import Path

import yaml
from llm_extractor import LLMExtractor, ExtractionStatus

HERE = Path(os.path.abspath(__file__)).parent

conversation_config = yaml.safe_load(open(HERE / "../../forwarding_service/conversation_configs/forwarding_conversation.yaml", 'r'))
extractor = LLMExtractor(conversation_config, llm_provider="openai")
print(extractor.run_extraction_step(""))
while extractor.status == ExtractionStatus.IN_PROGRESS:
    user_input = input()
    print(extractor.run_extraction_step(user_input))
print(extractor.get_conversation_state())