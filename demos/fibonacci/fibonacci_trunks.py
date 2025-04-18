import os
from pathlib import Path
import time


HERE = Path(os.path.abspath(__file__)).parent


def fibonacci(n):
    fib = [0, 1]
    for _ in range(2, n):
        fib.append(fib[-1] + fib[-2])
    return fib


def read_fibonacci(conversation_state, softphone):
    fibonacci_numbers = fibonacci(int(conversation_state["num_fibonacci"]))
    return " ".join(map(str, fibonacci_numbers))


def play_music(conversation_state, softphone):
    softphone.play_audio(str(HERE / "music.wav"))
    time.sleep(5)
    softphone.stop_audio()
    return
