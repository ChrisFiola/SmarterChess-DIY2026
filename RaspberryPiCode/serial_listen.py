import serial # type: ignore

PORT = "/dev/serial0"
BAUD = 115200

print("Waiting for serial data...")

with serial.Serial(PORT, BAUD, timeout=1) as ser:
    while True:
        if ser.inWaiting:
            data = ser.readline().decode(errors="ignore").strip()
            if data:
                print("RX:", data)
