"""Small adapter for structured, non-interactive Codex and Claude CLI calls."""

import json
import shutil
from pathlib import Path


PROVIDERS = ("codex", "claude")
CODEX_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")


def executable(provider, command=None):
    if provider not in PROVIDERS:
        raise ValueError("Unsupported CLI provider: " + provider)
    name = command or provider
    if not shutil.which(name):
        raise RuntimeError(provider.capitalize() + " CLI unavailable: " + name)
    return name


def command_for(
    provider, command, schema, result, images=(), image_root=None, codex_effort=None
):
    """Build argv only; callers retain their own timeout and audit ledger."""
    if provider not in PROVIDERS:
        raise ValueError("Unsupported CLI provider: " + provider)
    if codex_effort is not None and (
        provider != "codex" or codex_effort not in CODEX_EFFORTS
    ):
        raise ValueError("--codex-effort requires Codex and a supported effort value")
    name = command or provider
    if provider == "codex":
        argv = [
            name,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--output-schema",
            str(schema),
            "--output-last-message",
            str(result),
        ]
        if codex_effort is not None:
            argv.extend(("-c", "model_reasoning_effort=" + json.dumps(codex_effort)))
        for image in images:
            argv.extend(("--image", str(Path(image).resolve())))
        return argv + ["-"]
    schema_json = Path(schema).read_text(encoding="utf-8")
    argv = [
        name,
        "--print",
        "--output-format",
        "json",
        "--json-schema",
        schema_json,
        "--no-session-persistence",
        "--tools",
        "Read" if images else "",
    ]
    if images and image_root:
        argv.extend(("--add-dir", str(Path(image_root).resolve())))
    return argv


def prompt_with_images(provider, prompt, images):
    if provider != "claude" or not images:
        return prompt
    paths = "\n".join(str(Path(image).resolve()) for image in images)
    return (
        prompt + "\nUse the Read tool to inspect each image below before deciding. "
        "Use no other tools. Image paths, in order:\n" + paths
    )


def save_response(provider, stdout, result):
    """Codex writes its result file; Claude returns a JSON envelope on stdout."""
    result = Path(result)
    if provider == "codex":
        return result.is_file()
    envelope = json.loads(stdout)
    value = envelope.get("structured_output")
    if value is None:
        value = envelope.get("result")
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("Claude CLI did not return a structured JSON object.")
    result.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return True
