PREFIX ?= $(HOME)/.local
PLIST_DIR = $(HOME)/Library/LaunchAgents
PLIST_LABEL = com.whisperpocket.daemon

.PHONY: install uninstall install-daemon uninstall-daemon

install:
	install -d $(PREFIX)/bin
	install -d $(PREFIX)/lib/whisperpocket/Resources
	install wp $(PREFIX)/bin/wp
	install wp-hook.sh $(PREFIX)/bin/wp-hook.sh
	install -m 644 listen.py $(PREFIX)/lib/whisperpocket/listen.py
	install -m 644 pyproject.toml $(PREFIX)/lib/whisperpocket/pyproject.toml
	install -m 644 Resources/typing.wav $(PREFIX)/lib/whisperpocket/Resources/typing.wav

uninstall:
	rm -f $(PREFIX)/bin/wp
	rm -f $(PREFIX)/bin/wp-hook.sh
	rm -rf $(PREFIX)/lib/whisperpocket

install-daemon: install
	mkdir -p $(PLIST_DIR)
	sed 's|__BINARY__|$(PREFIX)/bin/wp|g' Resources/com.whisperpocket.daemon.plist > $(PLIST_DIR)/$(PLIST_LABEL).plist
	launchctl bootout gui/$$(id -u) $(PLIST_DIR)/$(PLIST_LABEL).plist 2>/dev/null || true
	launchctl bootstrap gui/$$(id -u) $(PLIST_DIR)/$(PLIST_LABEL).plist

uninstall-daemon:
	launchctl bootout gui/$$(id -u) $(PLIST_DIR)/$(PLIST_LABEL).plist 2>/dev/null || true
	rm -f $(PLIST_DIR)/$(PLIST_LABEL).plist
