import sys, getopt

# Grab the arguments
argv = sys.argv[1:]

textLine1 = ''
textLine2 = ''
textLine3 = ''
textSize = ''

# Work through those arguments
try:
    opts, args = getopt.getopt(
        argv,
        "ha:b:c:s:",
        ["firstLine=", "secondLine=", "thirdLine=", "textSize="]
    )
except getopt.GetoptError:
    print('printToConsole.py -a <firstline> -b <secondline> -c <thirdline> -s <textsize>')
    sys.exit(2)

for opt, arg in opts:
    if opt == '-h':
        print('printToConsole.py -a <firstline> -b <secondline> -c <thirdline> -s <textsize>')
        sys.exit()
    elif opt in ("-a", "--firstLine"):
        textLine1 = arg
    elif opt in ("-b", "--secondLine"):
        textLine2 = arg
    elif opt in ("-c", "--thirdLine"):
        textLine3 = arg
    elif opt in ("-s", "--textSize"):
        textSize = int(arg)

# Instead of displaying on OLED, just print to console
print("\n" + "="*40)
print(f"Text Size: {textSize}")
print(f"{textLine1.center(40)}")
print(f"{textLine2.center(40)}")
print(f"{textLine3.center(40)}")
print("="*40 + "\n")
