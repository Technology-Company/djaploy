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
    """Main entry point - setup Django and run pyinfra"""
    
    # The environment and PYTHONPATH should already be set correctly
    # by the calling process, so we just need to setup Django
    
    # Check that we have the required Django settings
    if not os.environ.get('DJANGO_SETTINGS_MODULE'):
        print("Error: DJANGO_SETTINGS_MODULE environment variable not set")
        sys.exit(1)
    
    # Setup Django using the standard approach
    # Django will use the DJANGO_SETTINGS_MODULE from environment
    # and the PYTHONPATH to find the Django app
    try:
        django.setup(set_prefix=False)
    except Exception as e:
        print(f"Error: Could not setup Django: {e}")
        print(f"DJANGO_SETTINGS_MODULE: {os.environ.get('DJANGO_SETTINGS_MODULE')}")
        print(f"Current directory: {os.getcwd()}")
        print(f"Python path: {sys.path[:3]}")
        sys.exit(1)
    
    # Import and run pyinfra CLI
    try:
        import pyinfra_cli.__main__
    except ImportError as e:
        print(f"Error: Could not import pyinfra CLI: {e}")
        print("Make sure pyinfra is installed in your environment.")
        sys.exit(1)

if __name__ == '__main__':
    main()