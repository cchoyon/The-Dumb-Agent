# The Dumb-Agent

**A simple AI agent that gets things done.**

The Dumb-Agent is an experimental AI agent for Windows designed to perform practical tasks using natural-language instructions. Instead of only answering questions, it can use available tools to interact with files, folders, command-line utilities, spreadsheets, and the web.

The project focuses on giving an AI model the ability to perform useful operations through a tool-based architecture.

> **Project status:** Experimental. The agent works, but features, reliability, and security are still being improved.

## Features

- **Multiple model providers:** Choose between Ollama for local inference and Groq for cloud-based models.
- **Command execution:** Perform supported PowerShell and command-line operations.
- **File management:** Read and write files, create folders, and organize data.
- **Excel assistance:** Help clean and process spreadsheets.
- **Web search:** Search the web to gather information for tasks.
- **Workspace-based operations:** Uses a dedicated workspace directory for agent-generated files and intended file-access restrictions.
- **Simple launcher:** Start the agent through `run.bat` and select your preferred model provider.

Capabilities and reliability depend on the selected model and the tools available to it.

## How It Works

The Dumb-Agent connects a language model to a collection of tools that allow it to perform operations beyond ordinary text generation.

When a user submits a task, the model determines how to approach it and can request available tools to complete the task. The agent executes supported operations and returns the results.

The general workflow is:

1. Launch the application.
2. Choose a model provider.
3. Submit a task in natural language.
4. Let the model use available tools.
5. Review the results and generated files.

## Choosing a Model Provider

### Ollama — Local Inference

Ollama allows the agent to use compatible models running locally on your computer.

- Model inference runs on your machine.
- No Groq API key is required.
- Performance depends on your hardware and selected model.
- Web search and other external integrations may still communicate with external services.

### Groq — Cloud Inference

Groq allows the agent to use supported hosted models.

- Requires a Groq API key.
- Model inference takes place through the provider's API.
- Available capabilities depend on the selected model.
- API usage may be subject to provider limits and pricing.

**Privacy note:** Local inference does not automatically make every operation private. Review which data is sent to external services when using web search or other integrations.

## Requirements

- Windows
- Python and the dependencies required by the project
- Ollama with a compatible model, or a Groq API key
- Internet access for Groq and web-search functionality

Refer to the installation instructions and dependency files in the repository for the exact setup requirements.

## Getting Started

### 1. Download the Project

Clone the repository:

```bash
git clone https://github.com/cchoyon/The-Dumb-Agent.git
cd The-Dumb-Agent
```

Alternatively, download the repository as a ZIP file and extract it.

### 2. Install Dependencies

Install the required Python packages using the project's dependency file:

```bash
python -m pip install -r requirements.txt
```

### 3. Configure Your Model Provider

For Ollama:

1. Install Ollama.
2. Download a model supported by the agent.
3. Ensure Ollama is running and the model is available.

For Groq:

1. Obtain an API key from the Groq Console.
2. Launch the application.
3. Select Groq when prompted.
4. Enter your API key when requested.

Never commit API keys or other credentials to the repository.

### 4. Launch the Agent

Run:

```text
run.bat
```

Follow the prompts to select the provider and configure the session.

## Workspace and Security

The agent is designed to operate within a dedicated workspace directory created inside the project when needed.

The workspace is intended to contain files created or managed during agent operations.

However, a workspace restriction implemented at the application level is not necessarily a complete security sandbox. Command execution may introduce additional risks if commands can access files or resources outside the workspace.

Do not run the agent with sensitive data or elevated privileges unless you understand and have tested its security boundaries.

Always review potentially destructive operations and keep backups of important files.

## Current Limitations

The project is still under development.

- Model performance varies considerably.
- Complex tasks may fail or produce incorrect results.
- Excel cleaning and data-processing capabilities need further testing.
- Tool execution and error handling can be improved.
- Security restrictions require continued testing and refinement.

The agent should be treated as an experimental tool rather than a fully autonomous or production-ready system.

## Roadmap

Planned areas of improvement include:

- More reliable tool selection and execution.
- Stronger workspace and command-execution security.
- Improved Excel processing and validation.
- Better error handling and recovery.
- Expanded testing and documentation.
- Support for additional models and tools.

## Contributing

Suggestions, bug reports, and contributions are welcome.

If you encounter an issue, provide the steps to reproduce it, the selected model provider, and relevant error messages. Remove API keys and other sensitive information before sharing logs.

## License

See the `LICENSE` file for the terms under which this project may be used, modified, and distributed.

---

**The Dumb-Agent — Experimental AI, practical tools, and room to improve.**
