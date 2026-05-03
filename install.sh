#!/bin/bash
set -e

echo "Installing Ollama..."
curl -fsSL https://ollama.com/install.sh | sh

echo "Pulling model..."
ollama pull ministral-3:3b

echo "Installing requirements..."
pip install -r requirements.txt

echo "Installation complete"
