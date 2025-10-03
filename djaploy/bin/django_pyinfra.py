#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Django-aware pyinfra wrapper for djaploy

This script sets up Django environment before running pyinfra,
allowing inventory files to use Django models and settings.
"""

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
        from pyinfra_cli import __main__ as pyinfra_main
    except ImportError as exc:
        print(f"Error: Could not import pyinfra CLI: {exc}")
        print("Make sure pyinfra is installed in your environment.")
        return 1

    return pyinfra_main.main()

if __name__ == '__main__':
    raise SystemExit(main())
