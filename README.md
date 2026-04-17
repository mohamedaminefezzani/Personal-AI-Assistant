# Personal-AI-Assistant

This is an ongoing personal project to build my own local agent. I have yet to set on its goal but I'm experimenting as I go through.
Still in its early phases, this repo provides the basic foundation of the agent in the style of a multi-turn conversational chatbot.

# Technical Details

The agent is powered by Mistral's recent LLM, **Ministral 3 3b**. It is capable of processing images, handling different languages, as well as calling tools.
Currently, only web search is implemented. Chat memory is session-persistent

# Requirements

- Python 3.12 (the project runs on Python 3.12.10)
- Ollama running, Ministral 3 and codellama weights (`ollama pull ministral-3:3b`, `ollama pull codellama:7b`)
- Tavily API key **set as an environment variable** (for web search calls)

# Getting Started

1. Fork the repository
2. Create a virtual environment.
   - `python -m venv venv`
   - `source venv/bin/activate` (for Linux) or `venv/Scripts/activate` (for Windows)
   - `pip install -qr requirements.txt`
3. Run *web_app.py*
   - `python web_app.py`
  
Now, you can con converse.

**NOTE: Ollama must be running in the background. To do so, either:**
- run `ollama serve` in a separate terminal if on Linux.
- open the app in background on Windows.
