"""SmarterChess Pi-side application package.

The systemd unit points at RaspberryPiCode/main/piMain.py. We keep that
entrypoint stable, but move complex logic into this package so it is easier
to extend (e.g., adding Lichess later).
"""
