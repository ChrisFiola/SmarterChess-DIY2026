
from machine import UART, Pin

class Link:
    def __init__(self, uart_id=0, baudrate=115200, tx_pin=0, rx_pin=1, timeout=10):
        self.uart = UART(uart_id, baudrate=baudrate, tx=Pin(tx_pin), rx=Pin(rx_pin), timeout=timeout)

    def send_to_pi(self, kind, payload=""):
        self.uart.write(f"heypi{kind}{payload}\n".encode())

    def read_from_pi(self):
        if self.uart.any():
            try:
                return self.uart.readline().decode().strip()
            except:
                return None
        return None

    def send_typing_preview(self, label, text):
        self.uart.write(f"heypityping_{label}_{text}\n".encode())
