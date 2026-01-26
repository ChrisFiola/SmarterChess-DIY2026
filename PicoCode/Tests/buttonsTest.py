from machine import Pin
import time
BUTTON_PINS = [2, 3, 4, 6, 7, 8, 9, 10, 12, 13]
pins = [Pin(g, Pin.IN, Pin.PULL_UP) for g in BUTTON_PINS]
while True:
    #print("Button 10: ", btn1.value())
    #print("Button 11: ", btn2.value())
    if pins[0].value() == 0:
        print("Button 1: ", pins[0].value())
    elif pins[1].value() == 0:
        print("Button 2: ", pins[1].value())
    elif pins[2].value() == 0:
        print("Button 3: ", pins[2].value())
    elif pins[3].value() == 0:
        print("Button 4: ", pins[3].value())
    elif pins[4].value() == 0:
        print("Button 5: ", pins[4].value())
    elif pins[5].value() == 0:
        print("Button 6: ", pins[5].value())
    elif pins[6].value() == 0:
        print("Button 7: ", pins[6].value())
    elif pins[7].value() == 0:
        print("Button 8: ", pins[7].value())
    elif pins[8].value() == 0:
        print("Button 9: ", pins[8].value())
    elif pins[9].value() == 0:
        print("Button 10: ", pins[9].value())
    