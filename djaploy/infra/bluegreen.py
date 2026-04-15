"""
Blue-green deployment state management.

Functions that generate shell commands for managing state.json on
the remote server.  All functions return command strings to be run
via pyinfra's server.shell().

State file format (state.json):
{
    "active_slot": "blue" | "green" | null,
    "slots": {
        "blue":  { "release": "...", "commit": "...", "deployed_at": "...",
                    "python_interpreter": "...", "venv_path": "..." } | null,
        "green": { ... } | null
    }
}
"""

import json

SLOTS = ("blue", "green")
DEFAULT_STATE = {"active_slot": None, "slots": {"blue": None, "green": None}}


def other_slot(slot: str) -> str:
    """Return the other slot name."""
    return "green" if slot == "blue" else "blue"


def init_state_cmd(state_file: str) -> str:
    """Shell command to create state.json if it doesn't exist."""
    default = json.dumps(DEFAULT_STATE)
    return (
        f'test -f {state_file} || '
        f"echo '{default}' > {state_file}"
    )


def read_active_slot_cmd(state_file: str) -> str:
    """Shell command that prints the active slot name (or empty string).

    Returns empty string if state.json doesn't exist yet (first deploy).
    """
    return (
        f"python3 -c \""
        f"import json, os; "
        f"s=json.load(open('{state_file}')) if os.path.exists('{state_file}') else {json.dumps(DEFAULT_STATE)}; "
        f"print(s.get('active_slot') or '')"
        f"\""
    )


def read_state_cmd(state_file: str) -> str:
    """Shell command that prints the full state.json contents."""
    return f"cat {state_file}"


def update_slot_info_cmd(
    state_file: str,
    slot: str,
    release: str,
    commit: str,
    python_interpreter: str,
    venv_path: str,
) -> str:
    """Shell command to update a slot's deployment metadata in state.json.

    Note: Production code uses heredocs instead of this function.
    Kept for testing and as a utility.
    """
    # Serialize values safely to avoid injection
    safe_vals = json.dumps({
        "release": release,
        "commit": commit,
        "python_interpreter": python_interpreter,
        "venv_path": venv_path,
    })
    return (
        f"python3 -c \""
        f"import json, datetime, os; "
        f"s=json.load(open('{state_file}')); "
        f"vals={safe_vals}; "
        f"vals['deployed_at']=datetime.datetime.now(datetime.timezone.utc).isoformat(); "
        f"s['slots']['{slot}']=vals; "
        f"f=open('{state_file}.tmp','w'); "
        f"json.dump(s,f,indent=2); f.close(); "
        f"os.rename('{state_file}.tmp','{state_file}')"
        f"\""
    )


def set_active_slot_cmd(state_file: str, slot: str) -> str:
    """Shell command to set the active_slot field in state.json."""
    return (
        f"python3 -c \""
        f"import json; "
        f"s=json.load(open('{state_file}')); "
        f"s['active_slot']='{slot}'; "
        f"f=open('{state_file}.tmp','w'); "
        f"json.dump(s,f,indent=2); f.close(); "
        f"import os; os.rename('{state_file}.tmp','{state_file}')"
        f"\""
    )


def print_slot_info_cmd(state_file: str, slot: str) -> str:
    """Shell command that prints deployment info for a slot."""
    return (
        f"python3 -c \""
        f"import json; "
        f"s=json.load(open('{state_file}')); "
        f"info=s['slots'].get('{slot}'); "
        f"active=s.get('active_slot'); "
        f"status='active' if active=='{slot}' else 'inactive'; "
        f"print(); "
        f"print(f'=== Slot: {slot} ({{status}}) ==='); "
        f"if info: ["
        f"print(f'  Release:      {{info[\\\"release\\\"]}}'),"
        f"print(f'  Commit:       {{info[\\\"commit\\\"]}}'),"
        f"print(f'  Deployed:     {{info[\\\"deployed_at\\\"]}}'),"
        f"print(f'  Python:       {{info[\\\"python_interpreter\\\"]}}'),"
        f"print(f'  Venv:         {{info[\\\"venv_path\\\"]}}'),"
        f"]; "
        f"print() if not info else None; "
        f"print('  (empty)') if not info else None"
        f"\""
    )


