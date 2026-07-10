

import sys


def read_terminal(prompt):
    try:
        with open("/dev/tty", "r",encoding="utf-8") as terminal:
            print(prompt, end="", flush=True)
            line = terminal.readline()

            if line == "":
                raise EOFError
            return line.rstrip("\r\n")
    except OSError:
        return input(prompt)
