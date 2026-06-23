

import sys


def read_terminal(prompt: str) -> str:
    #Read from the controlling terminal, falling back to standard input
    try:
        with open("/dev/tty", "r", encoding="utf-8") as terminal:
            print(prompt, end="", flush=True)
            line = terminal.readline()
            if line == "":
                raise EOFError
            return line.rstrip("\r\n")
    except OSError:
        return input(prompt)
