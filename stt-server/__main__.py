"""Allow running as: python -m stt_server serve --port 8112 --model distil-small.en"""

from server import cli

if __name__ == "__main__":
    cli()
