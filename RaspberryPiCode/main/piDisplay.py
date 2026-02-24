# -*- coding: utf-8 -*-
"""
Display abstraction for SMARTCHESS (NDJSON display server)
- Same interface as before
"""
import os
import time
import subprocess

PIPE_PATH: str = "/tmp/lcdpipe"
READY_FLAG_PATH: str = "/tmp/display_server_ready"
DISPLAY_SERVER_SCRIPT: str = "/home/king/SmarterChess-DIY2026/RaspberryPiCode/screen/display_server.py"

class Display:
    def __init__(self, pipe_path: str = PIPE_PATH, ready_flag: str = READY_FLAG_PATH):
        self.pipe_path = pipe_path
        self.ready_flag = ready_flag

    def restart_server(self) -> None:
        subprocess.Popen("pkill -f display_server.py", shell=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.2)
        if not os.path.exists(self.pipe_path):
            try:
                os.mkfifo(self.pipe_path)
            except FileExistsError:
                pass
        subprocess.Popen(["python3", DISPLAY_SERVER_SCRIPT],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait_ready(self, timeout_s: float = 10.0) -> None:
        start = time.time()
        while not os.path.exists(self.ready_flag):
            if time.time() - start > timeout_s:
                break
            time.sleep(0.05)

    def send(self, message: str, size: str = "auto") -> None:
        parts = message.split("\n")
        payload = "|".join(parts) + f"|{size}\n"
        with open(self.pipe_path, "w") as pipe:
            pipe.write(payload)

    # Convenience UI helpers
    def banner(self, text: str, delay_s: float = 0.0) -> None:
        self.send(text)
        if delay_s > 0:
            time.sleep(delay_s)

    def show_arrow(self, uci: str, suffix: str = "") -> None:
        arrow = f"{uci[:2]} → {uci[2:4]}"
        if suffix:
            self.send(f"{arrow}\n{suffix}")
        else:
            self.send(arrow)

    def prompt_move(self, side: str) -> None:
        self.send(f"You are {side.lower()}\nEnter move:")

    def show_hint_result(self, uci: str) -> None:
        try:
            frm, to = uci[:2], uci[2:4]
            if len(uci) >= 4:
                self.send(f"Hint received:\n{frm} → {to}")
            else:
                self.send(f"Hint received:\n{uci}")
        except Exception:
            self.send(f"Hint received:\n{uci}")

    def show_invalid(self, text: str) -> None:
        self.send(f"Invalid\n{text}\nTry again")

    def show_illegal(self, uci: str, side_name: str) -> None:
        self.send(f"Illegal move!\nEnter new\nmove...")

    def show_gameover(self, result: str) -> None:
        self.send(f"Game Over\nResult {result}\nPress OK")

    def show_hint_thinking(self) -> None:
        self.send("Hint\nThinking...")
