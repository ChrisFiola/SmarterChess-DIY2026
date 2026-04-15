#!/home/king/chessenv/bin/python
"""
Quick screen bring-up test.  Run this directly on the Pi Zero:
  python test_screen.py

Checks each layer independently and prints exactly where it fails.
"""
import sys, time

print("=== SmarterChess Screen Test ===")

# 1. SPI device nodes
import os
for dev in ["/dev/spidev0.0", "/dev/spidev0.1"]:
    print(f"  {dev}: {'OK' if os.path.exists(dev) else 'MISSING — run raspi-config and enable SPI'}")

# 2. spidev
try:
    import spidev
    print("  spidev: OK")
except ImportError:
    print("  spidev: MISSING — pip install spidev")
    sys.exit(1)

# 3. RPi.GPIO
try:
    import RPi.GPIO as GPIO
    print("  RPi.GPIO: OK")
except ImportError:
    print("  RPi.GPIO: MISSING — pip install RPi.GPIO")
    sys.exit(1)

# 4. Pillow
try:
    from PIL import Image, ImageDraw, ImageFont
    print("  Pillow: OK")
except ImportError:
    print("  Pillow: MISSING — pip install Pillow")
    sys.exit(1)

# 5. Backlight — just turn it on
BL_PIN = 18
GPIO.setmode(GPIO.BCM)
GPIO.setup(BL_PIN, GPIO.OUT)
GPIO.output(BL_PIN, GPIO.HIGH)
print(f"  Backlight GPIO{BL_PIN}: set HIGH — screen should glow now")
time.sleep(2)

# 6. Full ILI9341 init
print("  Initialising ILI9341...")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from screen.ili9341_pi import ILI9341
    disp = ILI9341()
    disp.bl_DutyCycle(80)
    print("  ILI9341 init: OK")
except Exception as e:
    print(f"  ILI9341 init: FAILED — {e}")
    import traceback; traceback.print_exc()
    GPIO.cleanup()
    sys.exit(1)

# 7. Fill red — should show solid red
print("  Filling screen RED...")
from PIL import Image
img = Image.new("RGB", (240, 320), (255, 0, 0))
disp.ShowImage(img)
time.sleep(2)

# 8. Renderer + splash
print("  Testing renderer...")
try:
    from screen.renderer import Renderer
    r = Renderer(240, 320)
    splash = r.render_splash()
    disp.ShowImage(splash)
    print("  Renderer: OK — splash drawn")
except Exception as e:
    print(f"  Renderer: FAILED — {e}")
    import traceback; traceback.print_exc()

time.sleep(3)

# 9. Touch init
print("  Initialising XPT2046 touch...")
try:
    from screen.xpt2046 import XPT2046
    touch = XPT2046()
    print("  XPT2046: OK")
    print("  Touch IRQ reading:", "TOUCHED" if touch.touched() else "not touched")
except Exception as e:
    print(f"  XPT2046: FAILED — {e}")

print("\n=== Test complete ===")
GPIO.cleanup()
