#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Django-aware pyinfra wrapper for djaploy

This script sets up Django environment before running pyinfra,
allowing inventory files to use Django models and settings.
"""

# Monkey-patch before anything else is imported. pyinfra_cli does this in its
# own __init__, but by then Django has been set up and ssl/threading are already
# imported — gevent warns about exactly that ("Monkey-patching ssl after ssl has
# already been imported ... may silently lead to incorrect behaviour"), which is
# not a warning to live with in a tool that exists to open SSH connections.
#
# It also breaks Django projects outright. Patching walks every live object and
# isinstance()-checks it; that resolves any LazyObject it touches. Django's
# `django.contrib.admin.sites.site` is one, and it exists whenever anything has
# imported `django.contrib.admin` — Wagtail does, via
# `wagtail/admin/admin_url_finder.py`. If the project doesn't have the admin app
# in INSTALLED_APPS, resolving it raises `LookupError: No installed app with
# label 'admin'` before pyinfra has connected to anything.
from gevent import monkey

monkey.patch_all()

import os
import sys

import django

def main():
    """Main entry point - setup Django and run pyinfra."""

    # The environment and PYTHONPATH should already be set correctly
    # by the calling process, so we just need to set up Django.

    # Check that we have the required Django settings.
    if not os.environ.get('DJANGO_SETTINGS_MODULE'):
        print("Error: DJANGO_SETTINGS_MODULE environment variable not set")
        return 1

    # Setup Django using the standard approach.
    # Django will use the DJANGO_SETTINGS_MODULE from environment
    # and the PYTHONPATH to find the Django app.
    try:
        django.setup(set_prefix=False)
    except Exception as exc:  # pragma: no cover - defensive logging
        print(f"Error: Could not set up Django: {exc}")
        print(f"DJANGO_SETTINGS_MODULE: {os.environ.get('DJANGO_SETTINGS_MODULE')}")
        print(f"Current directory: {os.getcwd()}")
        print(f"Python path: {sys.path[:3]}")
        return 1

    # Import and run pyinfra CLI.
    try:
        from pyinfra_cli.main import main as pyinfra_main
    except ImportError:
        try:
            from pyinfra_cli.__main__ import main as pyinfra_main
        except ImportError as exc:
            print(f"Error: Could not import pyinfra CLI: {exc}")
            print("Make sure pyinfra is installed in your environment.")
            return 1

    return pyinfra_main()

if __name__ == '__main__':
    raise SystemExit(main())
