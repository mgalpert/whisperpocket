PREFIX ?= $(HOME)/.local
PLIST_DIR = $(HOME)/Library/LaunchAgents
PLIST_LABEL = com.brabpocket.daemon

.PHONY: install uninstall install-daemon uninstall-daemon

install:
	install -d $(PREFIX)/bin
	install brabpocket $(PREFIX)/bin/brabpocket
	install brabble-tts-hook.sh $(PREFIX)/bin/brabble-tts-hook.sh

uninstall:
	rm -f $(PREFIX)/bin/brabpocket
	rm -f $(PREFIX)/bin/brabble-tts-hook.sh

install-daemon: install
	mkdir -p $(PLIST_DIR)
	sed 's|__BINARY__|$(PREFIX)/bin/brabpocket|g' Resources/com.brabpocket.daemon.plist > $(PLIST_DIR)/$(PLIST_LABEL).plist
	launchctl bootout gui/$$(id -u) $(PLIST_DIR)/$(PLIST_LABEL).plist 2>/dev/null || true
	launchctl bootstrap gui/$$(id -u) $(PLIST_DIR)/$(PLIST_LABEL).plist

uninstall-daemon:
	launchctl bootout gui/$$(id -u) $(PLIST_DIR)/$(PLIST_LABEL).plist 2>/dev/null || true
	rm -f $(PLIST_DIR)/$(PLIST_LABEL).plist
