PREFIX ?= $(HOME)/.local

.PHONY: install uninstall

install:
	install -d $(PREFIX)/bin
	install -d $(PREFIX)/lib/whisperpocket/Resources
	install wp $(PREFIX)/bin/wp
	install -m 644 listen.py $(PREFIX)/lib/whisperpocket/listen.py
	install -m 644 pyproject.toml $(PREFIX)/lib/whisperpocket/pyproject.toml
	install -m 644 Resources/typing.wav $(PREFIX)/lib/whisperpocket/Resources/typing.wav

uninstall:
	rm -f $(PREFIX)/bin/wp
	rm -rf $(PREFIX)/lib/whisperpocket
