
from machine import Pin
import neopixel
import time

LED_PIN = 22     # your wiring
LED_COUNT = 64   # 4×8 strip chain

np = neopixel.NeoPixel(Pin(LED_PIN), LED_COUNT)

def fill(color):
    for i in range(LED_COUNT):
        np[i] = color
    np.write()

# TEST SEQUENCE
while True:
    print("Test: red")
    fill((40, 0, 0))   # low brightness red
    time.sleep(1)

    print("Test: green")
    fill((0, 40, 0))
    time.sleep(1)

    print("Test: blue")
    fill((0, 0, 40))
    time.sleep(1)

    print("Running pixel...")
    for i in range(LED_COUNT):
        fill((0,0,0))
        np[i] = (60,60,60)
        np.write()
        time.sleep(0.1)
