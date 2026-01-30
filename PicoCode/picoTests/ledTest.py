
from machine import Pin
import neopixel
import time

LED_PIN = 16     # your wiring
LED_COUNT = 10   # 4×8 strip chain

np = neopixel.NeoPixel(Pin(LED_PIN), LED_COUNT)

def fill(color):
    for i in range(LED_COUNT):
        np[i] = color
    np.write()

# TEST SEQUENCE
while True:
    print("Test: red")
    fill((255, 0, 0))   # low brightness red
    time.sleep(1)

    print("Test: green")
    fill((0, 255, 0))
    time.sleep(1)

    print("Test: blue")
    fill((0, 0, 255))
    time.sleep(1)

    print("Running pixel...")
    for i in range(LED_COUNT):
        fill((0,0,0))
        np[i] = (255,255,255)
        np.write()
        time.sleep(0.1)
