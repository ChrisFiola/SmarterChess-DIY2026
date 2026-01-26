

from machine import Pin
btn1, btn2 = Pin(10, Pin.IN, Pin.PULL_UP), Pin(11, Pin.IN, Pin.PULL_UP)
while True:
    print("Button 10: ", btn1.value())
    print("Button 11: ", btn2.value())
