

from machine import Pin
btn = Pin(9, Pin.IN, Pin.PULL_UP)
while True:
    print(btn.value())
