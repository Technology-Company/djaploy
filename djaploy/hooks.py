"""
Hook system for djaploy.

Allows Django apps to register functions that run at specific moments in the
deployment lifecycle. Each app can provide a ``djaploy_hooks.py`` file inside
its ``infra/`` directory. All hooks from all apps are collected (not first-match-wins).

Two decorators are provided:

- ``@hook("name")`` — local hook, runs on the machine executing ``manage.py``.
  Receives a single ``context`` dict.
- ``@deploy_hook("name")`` — remote hook, runs on target servers via pyinfra.
  Called directly from the command files in ``djaploy/commands/``.

Ordering is controlled by assigning hooks to the correct phase.  Each
command file calls phases in a fixed sequence (e.g. ``deploy:pre`` →
``deploy`` → ``deploy:post``).  Within a single phase hooks run in
registration order (built-in hooks first, then INSTALLED_APPS order).
"""

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


@dataclass
class RemoteFunctionHook:
    """A remote hook backed by a standalone function (from @deploy_hook)."""
    function: object  # Callable


class HookRegistry:
    """Collects and executes hook functions discovered from Django apps."""

    def __init__(self):
        self._hooks: Dict[str, List[Callable]] = {}
        self._remote_hooks: Dict[str, List[RemoteFunctionHook]] = {}
        self._discovered = False

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, hook_name: str, fn: Callable, *, remote: bool = False) -> None:
        if remote:
            self._remote_hooks.setdefault(hook_name, []).append(RemoteFunctionHook(function=fn))
        else:
            self._hooks.setdefault(hook_name, []).append(fn)

    def hook(self, name: str) -> Callable:
        """Decorator for local hooks (run on the deploying machine)."""
        def decorator(fn: Callable) -> Callable:
            self.register(name, fn, remote=False)
            return fn
        return decorator

    def deploy_hook(self, name: str) -> Callable:
        """Decorator for remote hooks (run on target servers via pyinfra)."""
        def decorator(fn: Callable) -> Callable:
            self.register(name, fn, remote=True)
            return fn
        return decorator

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def call(self, hook_name: str, context: Dict[str, Any]) -> List:
        """Call all local hooks registered for *hook_name*, in registration order.

        Returns a list of non-None return values from the hook functions.
        """
        results = []
        for fn in self._hooks.get(hook_name, []):
            result = fn(context)
            if result is not None:
                results.append(result)
        return results

    def get_remote_hooks(self, hook_name: str) -> List[RemoteFunctionHook]:
        """Return the list of remote hook functions for *hook_name*."""
        return list(self._remote_hooks.get(hook_name, []))

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self) -> None:
        """Load ``djaploy_hooks.py`` from every app's ``infra/`` directory.

        Idempotent — calling multiple times is safe.  Order:
        1. Built-in hooks (notifications, tagging)
        2. Django app hooks (``INSTALLED_APPS`` order, including djaploy apps)
        """
        if self._discovered:
            return

        # Load built-in hooks (notifications, tagging) before app hooks
        self._load_builtin_hooks()

        try:
            from .discovery import get_app_infra_dirs
        except (ImportError, ModuleNotFoundError):
            # If discovery is not available (e.g. Django not configured), skip
            self._discovered = True
            return

        for app_label, infra_dir in get_app_infra_dirs():
            hooks_file = infra_dir / "djaploy_hooks.py"
            if hooks_file.is_file():
                self._load_hooks_file(hooks_file, app_label)

        self._discovered = True

    def _load_builtin_hooks(self) -> None:
        try:
            import djaploy.builtin_hooks  # noqa: F401
        except (ImportError, ModuleNotFoundError):
            pass

    def _load_hooks_file(self, path: Path, app_label: str) -> None:
        module_name = f"djaploy_hooks_{app_label}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Reset the registry.  Useful for testing."""
        self._hooks.clear()
        self._remote_hooks.clear()
        self._discovered = False

    def get_hook_names(self) -> List[str]:
        """Return all registered hook names (local and remote)."""
        names = set(self._hooks.keys()) | set(self._remote_hooks.keys())
        return sorted(names)


# ------------------------------------------------------------------
# Module-level singleton + public API
# ------------------------------------------------------------------

_registry = HookRegistry()

hook = _registry.hook
deploy_hook = _registry.deploy_hook
call_hook = _registry.call
get_remote_hooks = _registry.get_remote_hooks
discover_hooks = _registry.discover
clear_hooks = _registry.clear
get_registry = lambda: _registry  # noqa: E731
