from machine import Pin
import time
BUTTON_PINS = [2, 3, 4, 5, 10, 8, 7, 6, 9, 11]   # 1–8=coords, 9=A1(OK), 11=Hint IRQ
OK_BUTTON_INDEX   = 8   # Button 9
HINT_BUTTON_INDEX = 9   # Button 10
pins = [Pin(g, Pin.IN, Pin.PULL_UP) for g in BUTTON_PINS]
while True:
    #print("Button 10: ", btn1.value())
    #print("Button 11: ", btn2.value())
    if pins[0].value() == 0:
        print("Button 1: ", pins[0].value())
        continue
    elif pins[1].value() == 0:
        print("Button 2: ", pins[1].value())
        continue
    elif pins[2].value() == 0:
        print("Button 3: ", pins[2].value())
        continue
    elif pins[3].value() == 0:
        print("Button 4: ", pins[3].value())
        continue
    elif pins[4].value() == 0:
        print("Button 5: ", pins[4].value())
        continue
    elif pins[5].value() == 0:
        print("Button 6: ", pins[5].value())
        continue
    elif pins[6].value() == 0:
        print("Button 7: ", pins[6].value())
        continue
    elif pins[7].value() == 0:
        print("Button 8: ", pins[7].value())
        continue
    elif pins[8].value() == 0:
        print("Button OK: ", pins[8].value())
        continue
    elif pins[9].value() == 0:
        print("Button Hint: ", pins[9].value())
        continue
    