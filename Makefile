BINARY   := bin/cc
CMD      := ./cmd/cc
INSTALL  := $(HOME)/.local/bin/cc

.PHONY: build install clean

build:
	@mkdir -p bin
	go build -o $(BINARY) $(CMD)

install: build
	@mkdir -p $(dir $(INSTALL))
	cp $(BINARY) $(INSTALL)
	@echo "Installed to $(INSTALL)"

clean:
	rm -rf bin/
