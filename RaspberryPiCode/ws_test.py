import board, digitalio
from PIL import Image, ImageDraw
from adafruit_rgb_display.st7789 import ST7789

spi = board.SPI()
cs_pin = digitalio.DigitalInOut(board.CE0)
dc_pin = digitalio.DigitalInOut(board.D25)
rst_pin = digitalio.DigitalInOut(board.D27)

display = ST7789(spi, cs=cs_pin, dc=dc_pin, rst=rst_pin, baudrate=40_000_000)

img = Image.new("RGB", (display.width, display.height), "blue")
draw = ImageDraw.Draw(img)
draw.rectangle((10, 10, 100, 100), outline="white", width=3)
display.image(img)
